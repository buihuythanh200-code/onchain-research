"""Module 2.5.2a: validate ONE counterfactual Aave liquidation per borrower.

Uses historical token balances, post-shock prices and estimated liquidation pairs.
Does NOT execute an Aave liquidation or simulate on-chain rounding / dust rules.
It validates conservation, nonnegative quantities, and recomputes HF using E-mode.

Usage:
  python validate_liquidation_state_update.py --self-test
  python validate_liquidation_state_update.py
  python validate_liquidation_state_update.py --players 5
  python validate_liquidation_state_update.py --mode ALL_ELIGIBLE
"""
import argparse
import csv
from collections import defaultdict
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 60
D = Decimal
BLOCK = 26402092
EPS = D('0.00000001')

FILES = {
    'assets': 'aave_user_assets_full.csv',
    'emodes': 'aave_emode_categories.csv',
    'mapping': 'aave_emode_asset_mapping_resolved.csv',
    'scenarios': 'aave_stress_test_summary.csv',
    'stress': 'aave_stress_test_details.csv',
    'pairs': 'aave_liquidation_reward_estimates.csv',
    'pga': 'aave_pga_spne_results.csv',
}


def load_csv(path, required):
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'{path}: missing columns {sorted(missing)}')
        rows = list(reader)
    if not rows:
        raise ValueError(f'{path}: empty CSV')
    if 'snapshot_block' in rows[0] and any(int(r['snapshot_block']) != BLOCK for r in rows):
        raise ValueError(f'{path}: historical block mismatch')
    return rows


def parse_shocks(value):
    if value.strip().lower() in ('none', ''):
        return {}
    shocks = {}
    for part in value.split(';'):
        symbol, value_text = part.strip().rsplit(':', 1)
        symbol = symbol.strip()
        if not value_text.endswith('%') or symbol in shocks:
            raise ValueError(f'Invalid shock {part!r}')
        shock = D(value_text[:-1]) / D(100)
        if not D(-1) < shock <= 0:
            raise ValueError(f'Invalid shock magnitude: {part}')
        shocks[symbol] = shock
    return shocks


def calculate_hf(rows, shocks, emode_lt, bitmap):
    """Rows hold mutable token amounts; prices remain baseline oracle prices."""
    numerator, debt_usd, collateral_usd = D(0), D(0), D(0)
    for item in rows.values():
        factor = 1 + shocks.get(item['symbol'], D(0))
        price = item['baseline_price_usd'] * factor
        if price <= 0:
            raise ValueError('Nonpositive asset price')
        lt = item['base_lt_bps'] / D(10000)
        category = item['emode_category']
        if category and (bitmap[category] >> item['reserve_id']) & 1:
            lt = emode_lt[category]
        coll_value = item['collateral_amount'] * price if item['collateral_enabled'] else D(0)
        debt_value = item['debt_amount'] * price
        numerator += coll_value * lt
        collateral_usd += coll_value
        debt_usd += debt_value
    return (numerator / debt_usd if debt_usd else None, collateral_usd, debt_usd)


def update_position(asset_rows, pair, shocks):
    """Return new balances, fee transfers, and conservation checks.

    Borrower loses all gross seized collateral (liquidator share + protocol fee).
    Borrower's chosen debt balance falls by actual repayment.
    This is an off-chain arithmetic update based on upstream pair estimates.
    """
    coll_key = pair['collateral_address'].lower()
    debt_key = pair['debt_address'].lower()
    if coll_key not in asset_rows or debt_key not in asset_rows:
        raise ValueError('Liquidation pair token not present in borrower assets')
    coll, debt = asset_rows[coll_key], asset_rows[debt_key]
    cp = coll['baseline_price_usd'] * (1 + shocks.get(coll['symbol'], D(0)))
    dp = debt['baseline_price_usd'] * (1 + shocks.get(debt['symbol'], D(0)))
    if cp <= 0 or dp <= 0 or not coll['collateral_enabled']:
        raise ValueError('Invalid collateral or debt token for update')
    if coll['symbol'] != pair['collateral_symbol'] or debt['symbol'] != pair['debt_symbol']:
        raise ValueError('Pair asset-symbol mismatch')
    if abs(cp - D(pair['collateral_price_after_shock_usd'])) > D('0.0000001'):
        raise ValueError('Collateral post-shock price mismatch')
    if abs(dp - D(pair['debt_price_after_shock_usd'])) > D('0.0000001'):
        raise ValueError('Debt post-shock price mismatch')
    repay_usd = D(pair['estimated_debt_repaid_usd'])
    seized_usd = D(pair['estimated_collateral_seized_before_protocol_fee_usd'])
    fee_usd = D(pair['estimated_protocol_fee_usd'])
    liquidator_usd = D(pair['estimated_collateral_received_by_liquidator_usd'])
    if min(repay_usd, seized_usd, fee_usd, liquidator_usd) < 0:
        raise ValueError('Negative liquidation amounts')
    if abs(seized_usd - fee_usd - liquidator_usd) > EPS:
        raise ValueError('Protocol-fee conservation failed')
    if repay_usd <= 0 or seized_usd <= 0:
        raise ValueError('Zero-sized liquidation')
    gross_profit = liquidator_usd - repay_usd
    if abs(gross_profit - D(pair['estimated_gross_profit_before_gas_swap_slippage_usd'])) > EPS:
        raise ValueError('Estimated gross-profit mismatch')
    repay_tokens = repay_usd / dp
    seized_tokens = seized_usd / cp
    fee_tokens = fee_usd / cp
    liquidator_tokens = liquidator_usd / cp
    if repay_tokens > debt['debt_amount'] + D('1e-15'):
        raise ValueError('Debt repayment exceeds token balance')
    if seized_tokens > coll['collateral_amount'] + D('1e-15'):
        raise ValueError('Seized collateral exceeds balance')
    if abs(seized_tokens - fee_tokens - liquidator_tokens) > D('1e-18'):
        raise ValueError('Token conservation failed')
    # Copy, do not change the shared baseline across scenarios.
    updated = {address: dict(row) for address, row in asset_rows.items()}
    updated[debt_key]['debt_amount'] -= repay_tokens
    updated[coll_key]['collateral_amount'] -= seized_tokens
    for address in (debt_key, coll_key):
        for field in ('debt_amount', 'collateral_amount'):
            amount = updated[address][field]
            if amount < -D('1e-15'):
                raise ValueError('Negative post-liquidation token balance')
            if amount < 0:
                updated[address][field] = D(0)
    return updated, {
        'debt_repaid_usd': repay_usd,
        'collateral_seized_usd': seized_usd,
        'protocol_fee_usd': fee_usd,
        'liquidator_collateral_usd': liquidator_usd,
        'gross_profit_before_costs_usd': gross_profit,
        'debt_repaid_tokens': repay_tokens,
        'collateral_seized_tokens': seized_tokens,
        'fee_tokens': fee_tokens,
        'liquidator_tokens': liquidator_tokens,
    }


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8') as handle:
        wr = csv.DictWriter(handle, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)


def test_arithmetic():
    # Known, hand-checkable miniature example: 100 collateral tokens at $2,
    # 120 debt tokens at $1; repay $60, bonus 5%, fee 20% of bonus.
    assets = {
        'c': {'symbol': 'C', 'baseline_price_usd': D(2), 'collateral_amount': D(100),
              'debt_amount': D(0), 'collateral_enabled': True, 'base_lt_bps': D(8000),
              'reserve_id': 0, 'emode_category': 0},
        'd': {'symbol': 'D', 'baseline_price_usd': D(1), 'collateral_amount': D(0),
              'debt_amount': D(120), 'collateral_enabled': False, 'base_lt_bps': D(0),
              'reserve_id': 1, 'emode_category': 0},
    }
    pair = {
        'collateral_address': 'c', 'debt_address': 'd',
        'collateral_symbol': 'C', 'debt_symbol': 'D',
        'collateral_price_after_shock_usd': '2', 'debt_price_after_shock_usd': '1',
        'estimated_debt_repaid_usd': '60',
        'estimated_collateral_seized_before_protocol_fee_usd': '63',
        'estimated_protocol_fee_usd': '0.6',
        'estimated_collateral_received_by_liquidator_usd': '62.4',
        'estimated_gross_profit_before_gas_swap_slippage_usd': '2.4',
    }
    old_hf, _, old_debt = calculate_hf(assets, {}, {}, {})
    updated, moved = update_position(assets, pair, {})
    new_hf, _, new_debt = calculate_hf(updated, {}, {}, {})
    assert old_hf == D(160) / D(120)
    assert updated['c']['collateral_amount'] == D('68.5')
    assert updated['d']['debt_amount'] == D(60)
    assert moved['fee_tokens'] == D('0.3')
    assert moved['liquidator_tokens'] == D('31.2')
    assert old_debt - new_debt == D(60)
    assert new_hf == D('109.6') / D(60)
    assert assets['c']['collateral_amount'] == D(100)  # no baseline mutation
    # Same-token collateral and debt must update both fields, without overwrite.
    same = {'x': {'symbol': 'X', 'baseline_price_usd': D(1),
                  'collateral_amount': D(200), 'debt_amount': D(120),
                  'collateral_enabled': True, 'base_lt_bps': D(8000),
                  'reserve_id': 2, 'emode_category': 0}}
    second = dict(pair, collateral_address='x', debt_address='x', collateral_symbol='X',
                  debt_symbol='X', collateral_price_after_shock_usd='1')
    output, _ = update_position(same, second, {})
    assert output['x']['collateral_amount'] == D(137)
    assert output['x']['debt_amount'] == D(60)
    print('SELF-TEST PASS: conservation, token balances, HF, same-token update, immutable baseline')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('.'))
    parser.add_argument('--players', type=int, choices=(3, 5), default=3)
    parser.add_argument('--mode', choices=('PGA_SPNE', 'ALL_ELIGIBLE'), default='PGA_SPNE')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        test_arithmetic()
        return

    required = {
        'assets': ['borrower_address','snapshot_block','asset_address','symbol','reserve_id',
                   'emode_category','price_usd','collateral_amount','debt_amount',
                   'collateral_enabled','base_lt_bps'],
        'emodes': ['emode_category','liquidation_threshold_bps'],
        'mapping': ['emode_category','collateral_bitmap'],
        'scenarios': ['scenario','price_shocks'],
        'stress': ['scenario','borrower_address','modeled_hf'],
        'pairs': ['scenario','borrower_address','collateral_address','debt_address',
                  'collateral_symbol','debt_symbol','estimated_gross_profit_before_gas_swap_slippage_usd',
                  'collateral_price_after_shock_usd','debt_price_after_shock_usd',
                  'estimated_debt_repaid_usd','estimated_collateral_seized_before_protocol_fee_usd',
                  'estimated_protocol_fee_usd','estimated_collateral_received_by_liquidator_usd'],
        'pga': ['scenario','borrower_address','num_liquidators','winner',
                'collateral_symbol','debt_symbol','estimated_debt_repaid_usd'],
    }
    tables = {key: load_csv(args.directory / filename, required[key])
              for key, filename in FILES.items()}
    emode_lt = {int(x['emode_category']): D(x['liquidation_threshold_bps']) / 10000
                for x in tables['emodes']}
    bitmap = {int(x['emode_category']): int(x['collateral_bitmap'])
              for x in tables['mapping']}
    scenarios = {x['scenario']: parse_shocks(x['price_shocks'])
                 for x in tables['scenarios']}
    assets_by_user = defaultdict(dict)
    for row in tables['assets']:
        user = row['borrower_address'].lower()
        address = row['asset_address'].lower()
        if address in assets_by_user[user]:
            raise ValueError(f'Duplicate token for borrower {user}: {address}')
        assets_by_user[user][address] = {
            'symbol': row['symbol'], 'reserve_id': int(row['reserve_id']),
            'emode_category': int(row['emode_category']),
            'baseline_price_usd': D(row['price_usd']),
            'collateral_amount': D(row['collateral_amount']),
            'debt_amount': D(row['debt_amount']),
            'collateral_enabled': row['collateral_enabled'].lower() == 'true',
            'base_lt_bps': D(row['base_lt_bps']),
        }
    if len(assets_by_user) != 530:
        raise ValueError(f'Expected 530 sampled borrowers, got {len(assets_by_user)}')
    stress_by_key = {}
    for row in tables['stress']:
        key = (row['scenario'], row['borrower_address'].lower())
        if key in stress_by_key:
            raise ValueError(f'Duplicate stress key {key}')
        stress_by_key[key] = row
    alternatives = defaultdict(list)
    for row in tables['pairs']:
        alternatives[(row['scenario'], row['borrower_address'].lower())].append(row)
    selected = {}
    for key, rows in alternatives.items():
        rows.sort(key=lambda r: (-D(r['estimated_gross_profit_before_gas_swap_slippage_usd']),
                                  r['collateral_address'].lower(), r['debt_address'].lower()))
        selected[key] = rows[0]
    pga = {}
    for row in tables['pga']:
        if int(row['num_liquidators']) != args.players:
            continue
        key = (row['scenario'], row['borrower_address'].lower())
        if key in pga:
            raise ValueError(f'Duplicate PGA row for {key}')
        pga[key] = row
    if set(pga) != set(selected):
        raise ValueError('Selected pairs and PGA results do not match')

    details = []
    counts = defaultdict(lambda: {'selected': 0, 'executed': 0, 'no_winner': 0,
                                   'gross_sale_usd': D(0), 'debt_repaid_usd': D(0),
                                   'fee_usd': D(0), 'post_hf_below_one': 0})
    for key in sorted(selected):
        scenario, user = key
        pair, game = selected[key], pga[key]
        if user not in assets_by_user:
            raise ValueError(f'Missing borrower {user}')
        if pair['collateral_symbol'] != game['collateral_symbol'] or pair['debt_symbol'] != game['debt_symbol']:
            raise ValueError(f'PGA pair mismatch for {key}')
        if abs(D(pair['estimated_debt_repaid_usd']) - D(game['estimated_debt_repaid_usd'])) > EPS:
            raise ValueError(f'PGA repayment mismatch for {key}')
        shocks = scenarios[scenario]
        old_hf, old_coll, old_debt = calculate_hf(assets_by_user[user], shocks, emode_lt, bitmap)
        expected_hf = D(stress_by_key[key]['modeled_hf'])
        if old_hf is None or abs(old_hf - expected_hf) > D('0.00001'):
            raise ValueError(f'Pre-liquidation HF mismatch for {key}: {old_hf} vs {expected_hf}')
        if old_hf >= 1:
            raise ValueError(f'Non-liquidatable borrower in selected pair: {key}')
        report = counts[scenario]
        report['selected'] += 1
        execute = args.mode == 'ALL_ELIGIBLE' or game['winner'] != 'NONE'
        if not execute:
            report['no_winner'] += 1
            continue
        updated, transfers = update_position(assets_by_user[user], pair, shocks)
        new_hf, new_coll, new_debt = calculate_hf(updated, shocks, emode_lt, bitmap)
        if abs(old_coll - new_coll - transfers['collateral_seized_usd']) > EPS:
            raise ValueError(f'Collateral USD conservation failure for {key}')
        if abs(old_debt - new_debt - transfers['debt_repaid_usd']) > EPS:
            raise ValueError(f'Debt USD conservation failure for {key}')
        report['executed'] += 1
        report['gross_sale_usd'] += transfers['liquidator_collateral_usd']
        report['debt_repaid_usd'] += transfers['debt_repaid_usd']
        report['fee_usd'] += transfers['protocol_fee_usd']
        report['post_hf_below_one'] += int(new_hf is not None and new_hf < 1)
        details.append({
            'scenario': scenario, 'snapshot_block': BLOCK, 'borrower_address': user,
            'execution_mode': args.mode, 'num_liquidators': args.players,
            'collateral_symbol': pair['collateral_symbol'], 'debt_symbol': pair['debt_symbol'],
            'winner': game['winner'], 'hf_before_liquidation': str(old_hf),
            'hf_after_liquidation': str(new_hf) if new_hf is not None else 'NO_DEBT',
            'collateral_usd_before': str(old_coll), 'collateral_usd_after': str(new_coll),
            'debt_usd_before': str(old_debt), 'debt_usd_after': str(new_debt),
            'debt_repaid_usd': str(transfers['debt_repaid_usd']),
            'collateral_removed_from_borrower_usd': str(transfers['collateral_seized_usd']),
            'collateral_received_by_liquidator_usd': str(transfers['liquidator_collateral_usd']),
            'protocol_fee_usd': str(transfers['protocol_fee_usd']),
            'gross_profit_before_costs_usd': str(transfers['gross_profit_before_costs_usd']),
            'post_liquidation_still_underwater': str(new_hf is not None and new_hf < 1),
        })

    detail_fields = [
        'scenario','snapshot_block','borrower_address','execution_mode','num_liquidators',
        'collateral_symbol','debt_symbol','winner','hf_before_liquidation',
        'hf_after_liquidation','collateral_usd_before','collateral_usd_after',
        'debt_usd_before','debt_usd_after','debt_repaid_usd',
        'collateral_removed_from_borrower_usd','collateral_received_by_liquidator_usd',
        'protocol_fee_usd','gross_profit_before_costs_usd','post_liquidation_still_underwater',
    ]
    suffix = f'{args.mode.lower()}_n{args.players}'
    output = args.directory / f'aave_liquidation_state_validation_{suffix}.csv'
    summary = args.directory / f'aave_liquidation_state_validation_summary_{suffix}.csv'
    summary_rows = []
    for scenario, report in sorted(counts.items()):
        summary_rows.append({'scenario': scenario, 'snapshot_block': BLOCK,
                             'execution_mode': args.mode, 'num_liquidators': args.players,
                             'selected_pairs': report['selected'], 'assumed_executed': report['executed'],
                             'no_winner': report['no_winner'],
                             'total_debt_repaid_usd': str(report['debt_repaid_usd']),
                             'total_borrower_collateral_removed_usd': str(report['gross_sale_usd'] + report['fee_usd']),
                             'total_liquidator_collateral_usd': str(report['gross_sale_usd']),
                             'total_protocol_fee_usd': str(report['fee_usd']),
                             'still_hf_below_1_after_liquidation': report['post_hf_below_one']})
        print(f'{scenario}: assumed_executed={report["executed"]}/{report["selected"]}, '
              f'debt_repaid=${report["debt_repaid_usd"]:,.2f}, '
              f'collateral_removed=${report["gross_sale_usd"] + report["fee_usd"]:,.2f}, '
              f'post_liquidation_hf_below_1={report["post_hf_below_one"]}')
    write_csv(output, details, detail_fields)
    write_csv(summary, summary_rows, list(summary_rows[0]))
    print('=== LIQUIDATION STATE UPDATE VALIDATION COMPLETE ===')
    print('EXECUTED ROWS:', len(details))
    print('DETAIL CSV:', output.name)
    print('SUMMARY CSV:', summary.name)
    print('NOTE: one selected pair per borrower per independent scenario; no price feedback yet.')
    print('NOTE: arithmetic check only, not Aave contract-equivalent transaction simulation.')


if __name__ == '__main__':
    main()

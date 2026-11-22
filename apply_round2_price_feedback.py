"""Apply hypothetical second-round liquidation sale price impact to updated balances.

Revalues all 530 borrowers in each scenario, preserving token balances from
execute_cascade_round2.py. Does NOT execute round-3 liquidations, renew PGA,
or estimate actual AMM liquidity. Each scenario and first-round mode is separate.

Usage:
  python apply_round2_price_feedback.py --self-test
  python apply_round2_price_feedback.py
  python apply_round2_price_feedback.py --mode ALL_ELIGIBLE
  python apply_round2_price_feedback.py --liquidity 5000000
"""
import argparse
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from validate_liquidation_state_update import D, BLOCK, load_csv, calculate_hf, write_csv
from prepare_cascade_round2 import classify

CATEGORIES = ('ELIGIBLE_ROUND2', 'UNBACKED_DEBT_MODEL', 'HEALTHY', 'NO_DEBT')


def price_impact(sale, depth, coefficient, cap):
    if sale < 0 or depth <= 0 or coefficient < 0 or not D(0) <= cap < D(1):
        raise ValueError('Invalid sale, liquidity, coefficient or cap')
    return min(cap, coefficient * sale / depth)


def load_states(root, tag):
    position_path = root / f'aave_round2_executed_{tag}_positions.csv'
    rows = load_csv(position_path, ['scenario', 'snapshot_block', 'borrower_address',
        'asset_address', 'symbol', 'reserve_id', 'emode_category', 'collateral_enabled',
        'base_lt_bps', 'baseline_price_usd', 'current_price_usd',
        'collateral_amount', 'debt_amount'])
    users = defaultdict(dict)
    for r in rows:
        key = (r['scenario'], r['borrower_address'].lower())
        address = r['asset_address'].lower()
        if int(r['snapshot_block']) != BLOCK or address in users[key]:
            raise ValueError(f'Invalid snapshot or duplicate token: {key} {address}')
        # Treat the post-round-1 price as the baseline for this extra feedback step.
        token = {
            'symbol': r['symbol'], 'reserve_id': int(r['reserve_id']),
            'emode_category': int(r['emode_category']),
            'baseline_price_usd': D(r['current_price_usd']),
            'collateral_amount': D(r['collateral_amount']),
            'debt_amount': D(r['debt_amount']),
            'collateral_enabled': str(r['collateral_enabled']).strip().lower() == 'true',
            'base_lt_bps': D(r['base_lt_bps']),
            'historical_price_usd': D(r['baseline_price_usd']),
        }
        if token['baseline_price_usd'] <= 0 or token['collateral_amount'] < 0 or token['debt_amount'] < 0:
            raise ValueError(f'Invalid token prices or balances: {key} {address}')
        users[key][address] = token
    return users


def run(args):
    root = args.directory
    tag = f'{args.mode.lower()}_then_{args.round2_mode.lower()}_n{args.players}'
    users = load_states(root, tag)
    original_scenarios = sorted({sc for sc, _ in users})
    if len(original_scenarios) != 6:
        raise ValueError('Expected six independent stress scenarios')
    for sc in original_scenarios:
        if len([user for s, user in users if s == sc]) != 530:
            raise ValueError(f'Expected 530 borrowers in {sc}')

    fee_rows = load_csv(root / f'aave_round2_executed_{tag}_sales.csv',
                        ['scenario', 'collateral_symbol', 'hypothetical_sale_usd'])
    summary_rows = load_csv(root / f'aave_round2_executed_{tag}_summary.csv',
                            ['scenario', 'eligible_after_round2_before_price_feedback',
                             'unbacked_debt_accounts', 'healthy_accounts', 'no_debt_accounts'])
    pre_summary = {r['scenario']: r for r in summary_rows}
    if set(pre_summary) != set(original_scenarios):
        raise ValueError('Missing original scenario summaries')

    sales = defaultdict(lambda: defaultdict(lambda: D(0)))
    seen = set()
    for r in fee_rows:
        key = r['scenario'], r['collateral_symbol']
        if key in seen:
            raise ValueError(f'Duplicate sale token: {key}')
        seen.add(key)
        if r['scenario'] not in original_scenarios:
            raise ValueError('Unexpected scenario in sales')
        sales[r['scenario']][r['collateral_symbol']] = D(r['hypothetical_sale_usd']) * args.sale_fraction

    emodes = load_csv(root / 'aave_emode_categories.csv',
                      ['emode_category', 'liquidation_threshold_bps'])
    mapping = load_csv(root / 'aave_emode_asset_mapping_resolved.csv',
                       ['emode_category', 'collateral_bitmap'])
    emode_lt = {int(r['emode_category']): D(r['liquidation_threshold_bps']) / D(10000)
                for r in emodes}
    bitmaps = {int(r['emode_category']): int(r['collateral_bitmap']) for r in mapping}

    summary_out, token_out, borrower_out, new_out, positions_out = [], [], [], [], []
    for scenario in original_scenarios:
        impacts = {sym: price_impact(amount, args.liquidity, args.lambda_impact, args.max_impact)
                   for sym, amount in sales[scenario].items()}
        for sym, amount in sorted(sales[scenario].items()):
            token_out.append({'scenario': scenario, 'collateral_symbol': sym,
                'hypothetical_sale_usd': str(amount), 'liquidity_usd': str(args.liquidity),
                'incremental_impact_fraction': str(impacts[sym]),
                'incremental_impact_percent': str(impacts[sym] * 100)})
        counts_before, counts_after = defaultdict(int), defaultdict(int)
        newly = 0
        for (sc, user), assets in sorted(users.items()):
            if sc != scenario:
                continue
            # Exactly one additional multiplicative price effect, applied to
            # already-shocked and already-round-1-impacted current prices.
            extra_shocks = {sym: -impact for sym, impact in impacts.items()}
            before_hf, before_coll, before_debt = calculate_hf(assets, {}, emode_lt, bitmaps)
            after_hf, after_coll, after_debt = calculate_hf(assets, extra_shocks, emode_lt, bitmaps)
            before = classify(before_hf, before_coll, before_debt)
            after = classify(after_hf, after_coll, after_debt)
            counts_before[before] += 1
            counts_after[after] += 1
            new = before != 'ELIGIBLE_ROUND2' and after == 'ELIGIBLE_ROUND2'
            # A zero-collateral account must never be counted as a newly liquidatable loan.
            if new:
                newly += 1
                new_out.append({'scenario': scenario, 'borrower_address': user,
                    'status_before': before, 'hf_before': str(before_hf),
                    'hf_after': str(after_hf), 'debt_after_usd': str(after_debt)})
            borrower_out.append({'scenario': scenario, 'snapshot_block': BLOCK,
                'borrower_address': user, 'status_before_feedback': before,
                'status_after_feedback': after,
                'hf_before_feedback': str(before_hf) if before_hf is not None else 'NO_DEBT',
                'hf_after_feedback': str(after_hf) if after_hf is not None else 'NO_DEBT',
                'debt_usd_after_feedback': str(after_debt),
                'collateral_usd_after_feedback': str(after_coll),
                'newly_eligible': str(new)})
            for addr, token in assets.items():
                now = token['baseline_price_usd']
                impact = impacts.get(token['symbol'], D(0))
                positions_out.append({'scenario': scenario, 'snapshot_block': BLOCK,
                    'borrower_address': user, 'asset_address': addr,
                    'symbol': token['symbol'], 'reserve_id': token['reserve_id'],
                    'emode_category': token['emode_category'],
                    'collateral_enabled': token['collateral_enabled'],
                    'base_lt_bps': str(token['base_lt_bps']),
                    'historical_price_usd': str(token['historical_price_usd']),
                    'price_before_feedback_usd': str(now),
                    'price_after_feedback_usd': str(now * (1 - impact)),
                    'collateral_amount': str(token['collateral_amount']),
                    'debt_amount': str(token['debt_amount'])})
        for status, field in [('ELIGIBLE_ROUND2', 'eligible_after_round2_before_price_feedback'),
                              ('UNBACKED_DEBT_MODEL', 'unbacked_debt_accounts'),
                              ('HEALTHY', 'healthy_accounts'), ('NO_DEBT', 'no_debt_accounts')]:
            expected = int(pre_summary[scenario][field])
            if counts_before[status] != expected:
                raise ValueError(f'{scenario}: round-2 pre-feedback {status} mismatch {counts_before[status]} vs {expected}')
        if sum(counts_after.values()) != 530:
            raise ValueError(f'{scenario}: incomplete borrower partition')
        if counts_after['UNBACKED_DEBT_MODEL'] < counts_before['UNBACKED_DEBT_MODEL']:
            raise ValueError('Feedback cannot erase unbacked debt without balance updates')
        summary_out.append({'scenario': scenario, 'snapshot_block': BLOCK,
            'first_round_mode': args.mode, 'second_round_mode': args.round2_mode,
            'num_liquidators': args.players, 'liquidity_usd_per_token': str(args.liquidity),
            'assumed_sale_fraction': str(args.sale_fraction),
            'assumed_lambda': str(args.lambda_impact),
            'assumed_cap': str(args.max_impact),
            'eligible_before_feedback': counts_before['ELIGIBLE_ROUND2'],
            'newly_eligible': newly, 'eligible_after_feedback': counts_after['ELIGIBLE_ROUND2'],
            'unbacked_accounts': counts_after['UNBACKED_DEBT_MODEL'],
            'total_hypothetical_sales_usd': str(sum(sales[scenario].values(), D(0))),
            'max_extra_price_impact_pct': str(max(impacts.values(), default=D(0)) * 100)})
        print(f'{scenario}: before={counts_before["ELIGIBLE_ROUND2"]}, '
              f'new={newly}, after={counts_after["ELIGIBLE_ROUND2"]}, '
              f'unbacked={counts_after["UNBACKED_DEBT_MODEL"]}, '
              f'sales=${sum(sales[scenario].values(), D(0)):,.2f}, '
              f'max_additional_impact={max(impacts.values(), default=D(0)) * 100:.3f}%')

    output_prefix = f'aave_round2_feedback_{tag}'
    outputs = {
        'summary': summary_out, 'token_impacts': token_out,
        'borrowers': borrower_out, 'newly_eligible': new_out,
        'positions': positions_out,
    }
    default_fields = {
        'token_impacts': ['scenario', 'collateral_symbol', 'hypothetical_sale_usd',
                          'liquidity_usd', 'incremental_impact_fraction', 'incremental_impact_percent'],
        'newly_eligible': ['scenario', 'borrower_address', 'status_before', 'hf_before', 'hf_after', 'debt_after_usd'],
    }
    for name, rows in outputs.items():
        fields = list(rows[0]) if rows else default_fields[name]
        path = root / f'{output_prefix}_{name}.csv'
        write_csv(path, rows, fields)
        print('CSV:', path.name)
    print('=== ROUND-2 PRICE FEEDBACK COMPLETE ===')
    print('NOTE: Second-round price impact is hypothetical; does not execute third-round liquidations.')


def self_test():
    assert price_impact(D(200), D(1000), D(1), D('0.2')) == D('0.2')
    assert price_impact(D(100), D(1000), D(1), D('0.2')) == D('0.1')
    assert D('0.85') * (1 - D('0.1')) == D('0.765')
    print('SELF-TEST PASS: price impact cap, linear scale, multiplicative application')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('.'))
    parser.add_argument('--mode', choices=['PGA_SPNE', 'ALL_ELIGIBLE'], default='PGA_SPNE')
    parser.add_argument('--round2-mode', choices=['PGA_SPNE', 'ALL_ELIGIBLE'], default='PGA_SPNE')
    parser.add_argument('--players', type=int, default=3)
    parser.add_argument('--liquidity', type=D, default=D('10000000'))
    parser.add_argument('--lambda-impact', type=D, default=D(1))
    parser.add_argument('--sale-fraction', type=D, default=D(1))
    parser.add_argument('--max-impact', type=D, default=D('0.20'))
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if not D(0) <= args.sale_fraction <= 1:
        parser.error('--sale-fraction must be between 0 and 1')
    if args.self_test:
        self_test()
    else:
        run(args)

if __name__ == '__main__':
    main()

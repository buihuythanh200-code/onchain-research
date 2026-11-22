"""Module 2.5, first feedback round of a hypothetical liquidation cascade.

Uses historical Aave V3 positions, hypothetical shock scenarios, and modeled PGA
results. This is NOT an executed liquidation, observed MEV auction, or calibrated
market price-impact estimate. Only tests for *new* eligibility after a first
hypothetical sale; it does not yet update liquidated borrower balances, run a
second auction, or loop across multiple rounds.
"""
import argparse
import csv
from collections import defaultdict
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 50
D = Decimal
BLOCK = 26402092

FILES = {
    'positions': 'aave_position_risk_analysis.csv',
    'assets': 'aave_user_assets_full.csv',
    'reserves': 'aave_reserves.csv',
    'emode': 'aave_emode_categories.csv',
    'mapping': 'aave_emode_asset_mapping_resolved.csv',
    'stress': 'aave_stress_test_details.csv',
    'scenarios': 'aave_stress_test_summary.csv',
    'pairs': 'aave_liquidation_reward_estimates.csv',
    'pga': 'aave_pga_spne_results.csv',
}


def load(path, required=()):
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'{path}: missing columns {sorted(missing)}')
        rows = list(reader)
    if not rows:
        raise ValueError(f'{path}: empty CSV')
    if 'snapshot_block' in rows[0]:
        if any(int(x['snapshot_block']) != BLOCK for x in rows):
            raise ValueError(f'{path}: snapshot block mismatch')
    return rows


def shocks_from_text(value):
    if value.strip().lower() in ('', 'none'):
        return {}
    shocks = {}
    for element in value.split(';'):
        symbol, change = element.strip().rsplit(':', 1)
        symbol, change = symbol.strip(), change.strip()
        if not change.endswith('%') or symbol in shocks:
            raise ValueError(f'Invalid price shock {element!r}')
        fraction = D(change[:-1]) / 100
        if not D(-1) < fraction <= 0:
            raise ValueError(f'Invalid shock: {element!r}')
        shocks[symbol] = fraction
    return shocks


def price_impacts(sales_usd, liquidity_usd, impact_coefficient, max_impact):
    """Simple linear *assumption*: impact=min(max, lambda*sale/liquidity).

    All amounts are in USD valued at the pre-sale, post-shock prices.
    This is not an empirically estimated AMM liquidity curve.
    """
    output = {}
    for symbol, sale in sales_usd.items():
        if sale < 0:
            raise ValueError('Negative hypothetical sale')
        impact = min(max_impact, impact_coefficient * sale / liquidity_usd)
        output[symbol] = impact
    return output


def hf_for_assets(parts, base_shocks, impacts):
    """Revalue unchanged token balances using scenario and extra sale impact."""
    collateral, debt = D(0), D(0)
    for symbol, collateral_usd, debt_usd, lt in parts:
        shock_factor = D(1) + base_shocks.get(symbol, D(0))
        impact_factor = D(1) - impacts.get(symbol, D(0))
        factor = shock_factor * impact_factor
        collateral += collateral_usd * lt * factor
        debt += debt_usd * factor
    return collateral / debt if debt > 0 else None


def write(path, rows, fields):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run(args):
    tables = {name: load(args.directory / path) for name, path in FILES.items()}
    position_by_user = {x['borrower_address'].lower(): x for x in tables['positions']}
    if len(position_by_user) != len(tables['positions']):
        raise ValueError('Duplicate positions')
    reserves = {r['asset_address'].lower(): r for r in tables['reserves']}
    emode_lt = {int(r['emode_category']): D(r['liquidation_threshold_bps']) / 10000
                for r in tables['emode']}
    collateral_bitmap = {int(r['emode_category']): int(r['collateral_bitmap'])
                         for r in tables['mapping']}
    parts_by_user = defaultdict(list)
    for row in tables['assets']:
        user = row['borrower_address'].lower()
        if user not in position_by_user:
            raise ValueError(f'Unexpected borrower {user}')
        reserve = reserves[row['asset_address'].lower()]
        category = int(row['emode_category'])
        eligible_emode = category != 0 and bool(
            (collateral_bitmap[category] >> int(row['reserve_id'])) & 1
        )
        lt = emode_lt[category] if eligible_emode else D(row['base_lt_bps']) / 10000
        collateral = D(row['collateral_usd']) if row['collateral_enabled'].lower() == 'true' else D(0)
        debt = D(row['debt_usd'])
        if collateral < 0 or debt < 0 or reserve['symbol'] != row['symbol']:
            raise ValueError(f'Invalid asset row for {user}')
        parts_by_user[user].append((row['symbol'], collateral, debt, lt))
    if set(parts_by_user) != set(position_by_user):
        raise ValueError('Incomplete account coverage')

    shock_by_scenario = {}
    for row in tables['scenarios']:
        shock_by_scenario[row['scenario']] = shocks_from_text(row['price_shocks'])

    stress_by_scenario = defaultdict(dict)
    for row in tables['stress']:
        scenario, user = row['scenario'], row['borrower_address'].lower()
        if user in stress_by_scenario[scenario]:
            raise ValueError('Duplicate stress borrower')
        stress_by_scenario[scenario][user] = row

    # Reapply exactly the pair-selection rule used in integrate_pga_spne.py.
    pairs = defaultdict(list)
    for r in tables['pairs']:
        pairs[(r['scenario'], r['borrower_address'].lower())].append(r)
    selected = {}
    for key, values in pairs.items():
        values.sort(key=lambda r: (-D(r['estimated_gross_profit_before_gas_swap_slippage_usd']),
                                   r['collateral_address'].lower(), r['debt_address'].lower()))
        selected[key] = values[0]

    pga_by_scenario = defaultdict(list)
    for row in tables['pga']:
        if int(row['num_liquidators']) != args.players:
            continue
        key = (row['scenario'], row['borrower_address'].lower())
        pair = selected.get(key)
        if pair is None:
            raise ValueError(f'Missing selected liquidation pair for {key}')
        if row['collateral_symbol'] != pair['collateral_symbol'] or row['debt_symbol'] != pair['debt_symbol']:
            raise ValueError(f'PGA/pair symbol mismatch for {key}')
        if abs(D(row['estimated_debt_repaid_usd']) - D(pair['estimated_debt_repaid_usd'])) > D('0.000001'):
            raise ValueError(f'PGA/pair repayment mismatch for {key}')
        pga_by_scenario[row['scenario']].append((row, pair))

    if not pga_by_scenario:
        raise ValueError(f'No PGA results for N={args.players}')
    for scenario, entries in pga_by_scenario.items():
        if len({r['borrower_address'].lower() for r, _ in entries}) != len(entries):
            raise ValueError(f'Duplicate PGA borrower in {scenario}')

    summary, token_rows, new_borrowers = [], [], []
    for scenario, records in sorted(pga_by_scenario.items()):
        shocks = shock_by_scenario[scenario]
        stress_rows = stress_by_scenario[scenario]
        if set(stress_rows) != set(parts_by_user):
            raise ValueError(f'Incomplete stress rows: {scenario}')
        initial_eligible = {user for user, r in stress_rows.items() if D(r['modeled_hf']) < 1}
        if len(records) != len(initial_eligible):
            raise ValueError(f'PGA/initially eligible count mismatch: {scenario}')
        modeled_winners = []
        sales = defaultdict(lambda: D(0))
        for game, pair in records:
            if game['winner'] == 'NONE':
                continue
            modeled_winners.append(game)
            symbol = pair['collateral_symbol']
            seized = D(pair['estimated_collateral_received_by_liquidator_usd'])
            if seized < 0:
                raise ValueError('Negative liquidator collateral')
            # Sell 100% of collateral RECEIVED by liquidator (hypothesis).
            sales[symbol] += seized * args.sale_fraction
        impact = price_impacts(sales, args.liquidity_usd, args.lambda_impact,
                               args.max_impact)
        new_cases = 0
        for user, previous in stress_rows.items():
            if user in initial_eligible:
                continue  # Do NOT recompute already liquidated balances without state update.
            original_hf = D(previous['modeled_hf'])
            followup_hf = hf_for_assets(parts_by_user[user], shocks, impact)
            if followup_hf is None:
                raise ValueError(f'Missing debt: {user}')
            # Confirm the computed initial HF agrees with stress-test input.
            baseline_hf = hf_for_assets(parts_by_user[user], shocks, {})
            if abs(baseline_hf - original_hf) > D('0.001'):
                raise ValueError(f'HF reconciliation failed for {scenario}/{user}')
            if followup_hf < 1:
                new_cases += 1
                new_borrowers.append({
                    'scenario': scenario, 'num_liquidators': args.players,
                    'borrower_address': user, 'hf_after_initial_shock': str(original_hf),
                    'hf_after_first_sale_impact': str(followup_hf),
                    'debt_usd_at_initial_shock': previous['modeled_debt_usd'],
                })
        for symbol, amount in sorted(sales.items()):
            token_rows.append({
                'scenario': scenario, 'num_liquidators': args.players,
                'collateral_symbol': symbol, 'hypothetical_sale_usd': str(amount),
                'assumed_liquidity_usd': str(args.liquidity_usd),
                'incremental_price_impact_fraction': str(impact[symbol]),
                'incremental_price_impact_percent': str(impact[symbol] * 100),
            })
        summary.append({
            'scenario': scenario, 'num_liquidators': args.players,
            'initial_eligible_borrowers': len(initial_eligible),
            'pga_modeled_winners': len(modeled_winners),
            'pga_no_winner': len(records) - len(modeled_winners),
            'hypothetical_total_sale_usd': str(sum(sales.values(), D(0))),
            'new_eligible_after_first_price_feedback': new_cases,
            'assumed_liquidity_usd_per_token': str(args.liquidity_usd),
            'assumed_price_impact_lambda': str(args.lambda_impact),
            'assumed_sale_fraction': str(args.sale_fraction),
            'note': 'First feedback round only; initial borrowers not revalued after debt/collateral changes',
        })
        print(f'{scenario}: initial_eligible={len(initial_eligible)}, '
              f'PGA_winner={len(modeled_winners)}, '
              f'hypothetical_sales=${sum(sales.values(), D(0)):,.2f}, '
              f'newly_eligible_after_price_impact={new_cases}')
    write(args.directory / 'aave_cascade_round1_summary.csv', summary, list(summary[0]))
    token_fields = ['scenario', 'num_liquidators', 'collateral_symbol', 'hypothetical_sale_usd',
                    'assumed_liquidity_usd', 'incremental_price_impact_fraction',
                    'incremental_price_impact_percent']
    write(args.directory / 'aave_cascade_round1_price_impacts.csv', token_rows, token_fields)
    new_fields = ['scenario', 'num_liquidators', 'borrower_address', 'hf_after_initial_shock',
                  'hf_after_first_sale_impact', 'debt_usd_at_initial_shock']
    write(args.directory / 'aave_cascade_round1_newly_eligible.csv', new_borrowers, new_fields)
    print('=== FIRST-ROUND FEEDBACK COMPLETE ===')
    print('CSV: aave_cascade_round1_summary.csv')
    print('CSV: aave_cascade_round1_price_impacts.csv')
    print('CSV: aave_cascade_round1_newly_eligible.csv')
    print('IMPORTANT: This is a hypothetical single feedback step, NOT a full liquidation cascade.')
    print('IMPORTANT: Price impact and token liquidity are assumed, NOT calibrated on-chain.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('.'))
    p.add_argument('--players', type=int, choices=(3, 5), default=3)
    p.add_argument('--liquidity-usd', type=D, default=D('10000000'),
                   help='Hypothetical available USD depth PER collateral token')
    p.add_argument('--lambda-impact', type=D, default=D('1'))
    p.add_argument('--max-impact', type=D, default=D('0.20'))
    p.add_argument('--sale-fraction', type=D, default=D('1'),
                   help='Fraction of liquidator-received collateral assumed to be sold')
    args = p.parse_args()
    if not args.liquidity_usd.is_finite() or args.liquidity_usd <= 0:
        p.error('liquidity-usd must be positive')
    if not args.lambda_impact.is_finite() or args.lambda_impact < 0:
        p.error('lambda-impact must be nonnegative')
    if not args.max_impact.is_finite() or not D(0) <= args.max_impact < D(1):
        p.error('max-impact must be in [0,1)')
    if not args.sale_fraction.is_finite() or not D(0) <= args.sale_fraction <= D(1):
        p.error('sale-fraction must be in [0,1]')
    run(args)


if __name__ == '__main__':
    main()

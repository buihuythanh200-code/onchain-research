"""Module 2.5.1: single-round price-impact sensitivity, PGA vs all-eligible.

Counterfactual experiment only: fixed borrower balances, estimated collateral received,
100% immediate sale, uniform hypothetical USD liquidity per token, simplified linear
price impact, no actual on-chain liquidation or empirical liquidity calibration.

Put next to liquidation_price_impact_round1.py and the historical Aave CSV files.
"""
import argparse
import csv
from collections import defaultdict
from decimal import Decimal, getcontext
from pathlib import Path

from liquidation_price_impact_round1 import (
    BLOCK, FILES, load, shocks_from_text, hf_for_assets, price_impacts, write,
)

getcontext().prec = 50
D = Decimal

DEFAULT_DEPTHS = '1000000,5000000,10000000,50000000,100000000'


def parse_depths(value):
    result = []
    for item in value.split(','):
        depth = D(item.strip())
        if not depth.is_finite() or depth <= 0:
            raise ValueError('Each liquidity depth must be finite and > 0')
        if depth in result:
            raise ValueError('Duplicate liquidity depth')
        result.append(depth)
    if not result:
        raise ValueError('No liquidity depths')
    return sorted(result)


def load_inputs(directory, players):
    tables = {name: load(directory / filename) for name, filename in FILES.items()}
    positions = {}
    for r in tables['positions']:
        user = r['borrower_address'].lower()
        if user in positions:
            raise ValueError('Duplicate borrower in positions')
        positions[user] = r

    reserves = {r['asset_address'].lower(): r for r in tables['reserves']}
    emode_lt = {int(r['emode_category']): D(r['liquidation_threshold_bps']) / D(10000)
                for r in tables['emode']}
    e_bitmap = {int(r['emode_category']): int(r['collateral_bitmap'])
                for r in tables['mapping']}
    parts = defaultdict(list)
    for r in tables['assets']:
        user = r['borrower_address'].lower()
        if user not in positions:
            raise ValueError('Asset row for unknown borrower')
        reserve = reserves[r['asset_address'].lower()]
        if reserve['symbol'] != r['symbol']:
            raise ValueError('Reserve symbol mismatch')
        cat, rid = int(r['emode_category']), int(r['reserve_id'])
        lt = (emode_lt[cat] if cat and ((e_bitmap[cat] >> rid) & 1)
              else D(r['base_lt_bps']) / 10000)
        collateral = (D(r['collateral_usd']) if r['collateral_enabled'].lower() == 'true'
                      else D(0))
        debt = D(r['debt_usd'])
        if collateral < 0 or debt < 0:
            raise ValueError('Negative token value')
        parts[user].append((r['symbol'], collateral, debt, lt))
    if set(parts) != set(positions):
        raise ValueError('Incomplete borrower asset coverage')

    scenarios = {r['scenario']: shocks_from_text(r['price_shocks'])
                 for r in tables['scenarios']}
    stress = defaultdict(dict)
    for r in tables['stress']:
        key, user = r['scenario'], r['borrower_address'].lower()
        if user in stress[key]:
            raise ValueError(f'Duplicate stress case {key}/{user}')
        stress[key][user] = r

    pairs_by_key = defaultdict(list)
    for r in tables['pairs']:
        key = (r['scenario'], r['borrower_address'].lower())
        pairs_by_key[key].append(r)
    selected = {}
    for key, choices in pairs_by_key.items():
        choices.sort(key=lambda r: (
            -D(r['estimated_gross_profit_before_gas_swap_slippage_usd']),
            r['collateral_address'].lower(), r['debt_address'].lower()))
        selected[key] = choices[0]

    pga = defaultdict(dict)
    for r in tables['pga']:
        if int(r['num_liquidators']) != players:
            continue
        scenario, user = r['scenario'], r['borrower_address'].lower()
        if user in pga[scenario]:
            raise ValueError(f'Duplicate PGA record {scenario}/{user}')
        pair = selected.get((scenario, user))
        if pair is None:
            raise ValueError('PGA entry has no estimated liquidation pair')
        if r['collateral_symbol'] != pair['collateral_symbol'] or r['debt_symbol'] != pair['debt_symbol']:
            raise ValueError('PGA selected pair does not match estimate')
        if abs(D(r['estimated_debt_repaid_usd']) - D(pair['estimated_debt_repaid_usd'])) > D('0.000001'):
            raise ValueError('PGA debt repayment mismatch')
        pga[scenario][user] = r

    for scenario, shocks in scenarios.items():
        if set(stress[scenario]) != set(parts):
            raise ValueError(f'Incomplete stress data for {scenario}')
        initial = {u for u, r in stress[scenario].items() if D(r['modeled_hf']) < 1}
        if not initial:
            # Baseline with no candidates is valid.
            if any(key[0] == scenario for key in selected):
                raise ValueError('Unexpected pair in baseline')
            continue
        if set(pga[scenario]) != initial:
            raise ValueError(f'PGA/candidate mismatch in {scenario}')
        if {u for sc, u in selected if sc == scenario} != initial:
            raise ValueError(f'Pair/candidate mismatch in {scenario}')
        for user in parts:
            original = D(stress[scenario][user]['modeled_hf'])
            baseline = hf_for_assets(parts[user], shocks, {})
            if baseline is None or abs(baseline - original) > D('0.001'):
                raise ValueError(f'Baseline HF check failed: {scenario}/{user}')
    return parts, scenarios, stress, selected, pga


def run(args):
    parts, scenarios, stress, selected, pga = load_inputs(args.directory, args.players)
    results, impact_rows, borrower_rows = [], [], []
    depths = parse_depths(args.depths)
    for scenario in sorted(scenarios):
        if scenario == 'S0_BASELINE':
            continue
        shocks = scenarios[scenario]
        initially_eligible = {u for u, r in stress[scenario].items()
                              if D(r['modeled_hf']) < 1}
        for mode in ('PGA_SPNE', 'ALL_ELIGIBLE'):
            sales = defaultdict(lambda: D(0))
            selected_count = 0
            for user in sorted(initially_eligible):
                if mode == 'PGA_SPNE' and pga[scenario][user]['winner'] == 'NONE':
                    continue
                pair = selected[(scenario, user)]
                received = D(pair['estimated_collateral_received_by_liquidator_usd'])
                if received < 0:
                    raise ValueError('Negative hypothetical seized collateral')
                sales[pair['collateral_symbol']] += received * args.sale_fraction
                selected_count += 1
            total_sale = sum(sales.values(), D(0))
            for depth in depths:
                impacts = price_impacts(sales, depth, args.lambda_impact, args.max_impact)
                new_cases = []
                for user in sorted(parts):
                    if user in initially_eligible:
                        continue
                    new_hf = hf_for_assets(parts[user], shocks, impacts)
                    if new_hf is None:
                        raise ValueError(f'No debt found for {user}')
                    if new_hf < 1:
                        new_cases.append(user)
                        borrower_rows.append({
                            'scenario': scenario, 'execution_mode': mode,
                            'num_liquidators': args.players, 'assumed_liquidity_usd_per_token': str(depth),
                            'borrower_address': user,
                            'hf_before_sale_impact': stress[scenario][user]['modeled_hf'],
                            'hf_after_sale_impact': str(new_hf),
                        })
                results.append({
                    'scenario': scenario,
                    'execution_mode': mode,
                    'num_liquidators': args.players,
                    'assumed_liquidity_usd_per_token': str(depth),
                    'initial_eligible_borrowers': len(initially_eligible),
                    'assumed_executed_pairs': selected_count,
                    'hypothetical_total_sale_usd': str(total_sale),
                    'newly_eligible_after_price_impact': len(new_cases),
                    'total_eligible_after_first_feedback': len(initially_eligible) + len(new_cases),
                    'maximum_token_impact_percent': str(max(impacts.values(), default=D(0))*100),
                    'impacted_token_count': len(impacts),
                    'lambda_impact': str(args.lambda_impact),
                    'sale_fraction': str(args.sale_fraction),
                    'max_impact_fraction': str(args.max_impact),
                })
                for token in sorted(sales):
                    impact_rows.append({
                        'scenario': scenario, 'execution_mode': mode,
                        'num_liquidators': args.players,
                        'assumed_liquidity_usd_per_token': str(depth),
                        'collateral_symbol': token,
                        'hypothetical_sale_usd': str(sales[token]),
                        'incremental_price_impact_percent': str(impacts[token]*100),
                    })
                print(f'{scenario} | {mode} | depth=${depth:,.0f} | '
                      f'executed={selected_count}/{len(initially_eligible)} | '
                      f'newly_eligible={len(new_cases)} | '
                      f'max_impact={max(impacts.values(), default=D(0))*100:.3f}%')

    if not results:
        raise ValueError('No sensitivity runs generated')
    write(args.directory / 'aave_cascade_sensitivity_summary.csv', results, list(results[0]))
    token_fields = ['scenario', 'execution_mode', 'num_liquidators', 'assumed_liquidity_usd_per_token',
                    'collateral_symbol', 'hypothetical_sale_usd', 'incremental_price_impact_percent']
    write(args.directory / 'aave_cascade_sensitivity_token_impacts.csv', impact_rows, token_fields)
    borrower_fields = ['scenario', 'execution_mode', 'num_liquidators', 'assumed_liquidity_usd_per_token',
                       'borrower_address', 'hf_before_sale_impact', 'hf_after_sale_impact']
    write(args.directory / 'aave_cascade_sensitivity_new_borrowers.csv', borrower_rows, borrower_fields)

    # Verify that the same assumptions reproduce previous PGA N=3, depth=$10m result.
    old_file = args.directory / 'aave_cascade_round1_summary.csv'
    if old_file.is_file() and args.players == 3 and D('10000000') in depths and args.lambda_impact == 1 and args.sale_fraction == 1 and args.max_impact == D('0.20'):
        previous = load(old_file)
        previous_by_sc = {r['scenario']: r for r in previous if int(r['num_liquidators']) == 3}
        for r in results:
            if r['execution_mode'] != 'PGA_SPNE' or D(r['assumed_liquidity_usd_per_token']) != D('10000000'):
                continue
            old = previous_by_sc.get(r['scenario'])
            if old is None:
                raise ValueError('Missing scenario in previous round-1 output')
            if int(old['new_eligible_after_first_price_feedback']) != int(r['newly_eligible_after_price_impact']):
                raise ValueError(f'REPRODUCIBILITY ERROR: {r["scenario"]}, new candidates differ')
            if abs(D(old['hypothetical_total_sale_usd']) - D(r['hypothetical_total_sale_usd'])) > D('0.000001'):
                raise ValueError(f'REPRODUCIBILITY ERROR: {r["scenario"]}, sale total differs')
        print('BASELINE REPRODUCIBILITY CHECK: PASS (N=3, $10m, PGA)')

    print('=== SENSITIVITY ANALYSIS COMPLETE ===')
    print('RUNS:', len(results))
    print('CSV: aave_cascade_sensitivity_summary.csv')
    print('CSV: aave_cascade_sensitivity_token_impacts.csv')
    print('CSV: aave_cascade_sensitivity_new_borrowers.csv')
    print('NOTE: ALL_ELIGIBLE means every selected pair is assumed executed; not proof of feasibility.')
    print('NOTE: each scenario is independent, and only the first feedback round is modeled.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('.'))
    p.add_argument('--players', type=int, choices=(3, 5), default=3)
    p.add_argument('--depths', default=DEFAULT_DEPTHS)
    p.add_argument('--lambda-impact', type=D, default=D('1'))
    p.add_argument('--max-impact', type=D, default=D('0.20'))
    p.add_argument('--sale-fraction', type=D, default=D('1'))
    args = p.parse_args()
    try:
        parse_depths(args.depths)
    except ValueError as exc:
        p.error(str(exc))
    if not args.lambda_impact.is_finite() or args.lambda_impact < 0:
        p.error('lambda-impact must be non-negative and finite')
    if not args.max_impact.is_finite() or not D(0) <= args.max_impact < 1:
        p.error('max-impact must be in [0, 1)')
    if not args.sale_fraction.is_finite() or not D(0) <= args.sale_fraction <= 1:
        p.error('sale-fraction must be in [0, 1]')
    run(args)


if __name__ == '__main__':
    main()

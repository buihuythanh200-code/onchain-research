"""Prepare consistent round-2 borrower states after one hypothetical liquidation
and the first price-feedback event. This is NOT a complete second liquidation.

Requires the validated CSV inputs and existing modules in the local project.
Does not fetch new prices or treat an unfunded position as executable.
"""
import argparse
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from validate_liquidation_state_update import (
    BLOCK, D, FILES, load_csv, parse_shocks, calculate_hf, update_position,
    write_csv, EPS,
)

NEEDED = {
    'assets': ['borrower_address','asset_address','symbol','reserve_id','emode_category',
               'price_usd','collateral_amount','debt_amount','collateral_enabled','base_lt_bps'],
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
IMPACT_FILE = 'aave_cascade_sensitivity_token_impacts.csv'
ROUND1_FILE = 'aave_cascade_round1_price_impacts.csv'


def load_impacts(directory, source, mode, players, liquidity):
    # Prefer the original first-round impacts for the exact original parameter set.
    # For alternative modes/depths, use sensitivity analysis impact records.
    if source == 'round1':
        path = directory / ROUND1_FILE
        rows = load_csv(path, ['scenario','num_liquidators','collateral_symbol',
                               'incremental_price_impact_fraction'])
        chosen = [r for r in rows if int(r['num_liquidators']) == players]
        if mode != 'PGA_SPNE' or liquidity != D('10000000'):
            raise ValueError('round1 source supports only PGA_SPNE, 10m liquidity')
        return make_impact_map(chosen)
    path = directory / IMPACT_FILE
    rows = load_csv(path, ['scenario','execution_mode','collateral_symbol',
                           'incremental_price_impact_percent',
                           'assumed_liquidity_usd_per_token', 'num_liquidators'])
    chosen = [r for r in rows if r['execution_mode'] == mode and
              D(r['assumed_liquidity_usd_per_token']) == liquidity and
              int(r.get('num_liquidators', players)) == players]
    if not chosen:
        raise ValueError('No matching price-impact rows in sensitivity CSV')
    return make_impact_map(chosen, percent=True)


def make_impact_map(rows, percent=False):
    impacts = defaultdict(dict)
    for row in rows:
        scenario, token = row['scenario'], row['collateral_symbol']
        if token in impacts[scenario]:
            raise ValueError(f'Duplicate impact token {scenario}: {token}')
        impact = (D(row['incremental_price_impact_percent']) / D(100)
                  if percent else D(row['incremental_price_impact_fraction']))
        if not D(0) <= impact < D(1):
            raise ValueError('Invalid price impact')
        impacts[scenario][token] = impact
    return impacts


def classify(hf, collateral, debt):
    if debt <= D('0.000001'):
        return 'NO_DEBT'
    if collateral <= D('0.000001'):
        return 'UNBACKED_DEBT_MODEL'
    if hf is not None and hf < 1:
        return 'ELIGIBLE_ROUND2'
    return 'HEALTHY'


def run(args):
    tables = {k: load_csv(args.directory / name, NEEDED[k]) for k, name in FILES.items()}
    emode_lt = {int(x['emode_category']): D(x['liquidation_threshold_bps'])/10000
                for x in tables['emodes']}
    bitmaps = {int(x['emode_category']): int(x['collateral_bitmap'])
               for x in tables['mapping']}
    shocks_map = {r['scenario']: parse_shocks(r['price_shocks']) for r in tables['scenarios']}
    impact_map = load_impacts(args.directory, args.source, args.mode, args.players, args.liquidity)
    assets = defaultdict(dict)
    for row in tables['assets']:
        user, address = row['borrower_address'].lower(), row['asset_address'].lower()
        if address in assets[user]:
            raise ValueError('Duplicate asset row')
        assets[user][address] = {
            'symbol': row['symbol'], 'reserve_id': int(row['reserve_id']),
            'emode_category': int(row['emode_category']),
            'baseline_price_usd': D(row['price_usd']),
            'collateral_amount': D(row['collateral_amount']),
            'debt_amount': D(row['debt_amount']),
            'collateral_enabled': row['collateral_enabled'].strip().lower() == 'true',
            'base_lt_bps': D(row['base_lt_bps']),
        }
    stress = defaultdict(dict)
    for r in tables['stress']:
        scenario, user = r['scenario'], r['borrower_address'].lower()
        if user in stress[scenario]:
            raise ValueError('Duplicate stress row')
        stress[scenario][user] = D(r['modeled_hf'])
    pairs = defaultdict(list)
    for r in tables['pairs']:
        pairs[(r['scenario'], r['borrower_address'].lower())].append(r)
    selected = {}
    for key, options in pairs.items():
        options.sort(key=lambda r: (-D(r['estimated_gross_profit_before_gas_swap_slippage_usd']),
                                     r['collateral_address'].lower(), r['debt_address'].lower()))
        selected[key] = options[0]
    games = {}
    for r in tables['pga']:
        if int(r['num_liquidators']) != args.players:
            continue
        key = (r['scenario'], r['borrower_address'].lower())
        if key in games:
            raise ValueError('Duplicate PGA result')
        games[key] = r
    if set(selected) != set(games):
        raise ValueError('PGA and selected pair keys do not match')
    detail, summary = [], []
    for scenario, base_shocks in sorted(shocks_map.items()):
        if scenario == 'S0_BASELINE':
            continue
        if scenario not in impact_map:
            raise ValueError(f'Missing price feedback for {scenario}')
        original = stress[scenario]
        if set(original) != set(assets):
            raise ValueError(f'Missing original HF values in {scenario}')
        impacts = impact_map[scenario]
        # Sequential application of shock then additional price impact.
        after = {symbol: (D(1)+base_shocks.get(symbol,D(0))) *
                 (D(1)-impacts.get(symbol,D(0))) - D(1)
                 for symbol in set(base_shocks) | set(impacts)}
        tally = defaultdict(int)
        new_count, executed_count = 0, 0
        for user, baseline in sorted(assets.items()):
            old_hf, _, _ = calculate_hf(baseline, base_shocks, emode_lt, bitmaps)
            if old_hf is None or abs(old_hf - original[user]) > D('0.00001'):
                raise ValueError(f'Baseline mismatch {scenario} {user}')
            initially_eligible = old_hf < 1
            pair = selected.get((scenario,user))
            game = games.get((scenario,user))
            if initially_eligible != (pair is not None):
                raise ValueError(f'Initial eligibility/pair mismatch for {scenario} {user}')
            executed = bool(pair and (args.mode == 'ALL_ELIGIBLE' or game['winner'] != 'NONE'))
            updated = baseline
            if executed:
                if pair['collateral_symbol'] != game['collateral_symbol'] or pair['debt_symbol'] != game['debt_symbol']:
                    raise ValueError('PGA pair mismatch')
                if abs(D(pair['estimated_debt_repaid_usd'])-D(game['estimated_debt_repaid_usd'])) > EPS:
                    raise ValueError('PGA estimated repayment mismatch')
                updated, _ = update_position(baseline, pair, base_shocks)
                executed_count += 1
            hf, coll, debt = calculate_hf(updated, after, emode_lt, bitmaps)
            status = classify(hf, coll, debt)
            newly = not initially_eligible and status == 'ELIGIBLE_ROUND2'
            new_count += int(newly)
            tally[status] += 1
            detail.append({
                'scenario': scenario, 'snapshot_block': BLOCK, 'borrower_address': user,
                'execution_mode': args.mode, 'num_liquidators': args.players,
                'initially_eligible': str(initially_eligible),
                'executed_round1': str(executed),
                'hf_after_round1_and_impact': str(hf) if hf is not None else 'NO_DEBT',
                'collateral_usd_after': str(coll), 'debt_usd_after': str(debt),
                'status': status, 'newly_eligible_due_to_feedback': str(newly),
            })
        summary.append({
            'scenario': scenario, 'execution_mode': args.mode, 'num_liquidators': args.players,
            'liquidity_usd_per_token': str(args.liquidity),
            'executed_round1': executed_count, 'newly_eligible_after_feedback': new_count,
            'eligible_round2_total': tally['ELIGIBLE_ROUND2'],
            'unbacked_debt_accounts': tally['UNBACKED_DEBT_MODEL'],
            'no_debt_accounts': tally['NO_DEBT'], 'healthy_accounts': tally['HEALTHY'],
        })
        print(f"{scenario}: executed={executed_count}, new={new_count}, "
              f"round2_eligible={tally['ELIGIBLE_ROUND2']}, "
              f"unbacked={tally['UNBACKED_DEBT_MODEL']}")
    suffix = f'{args.mode.lower()}_n{args.players}'
    output = args.directory / f'aave_cascade_round2_state_{suffix}.csv'
    sums = args.directory / f'aave_cascade_round2_state_summary_{suffix}.csv'
    write_csv(output, detail, list(detail[0]))
    write_csv(sums, summary, list(summary[0]))
    print('=== ROUND-2 STATE PREPARATION COMPLETE ===')
    print('CSV:',output.name)
    print('CSV:',sums.name)
    print('WARNING: No second-round auction or liquidation is executed.')
    print('WARNING: Price impact and first-round liquidations are hypothetical.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('.'))
    p.add_argument('--source', choices=('round1','sensitivity'), default='round1')
    p.add_argument('--mode', choices=('PGA_SPNE','ALL_ELIGIBLE'), default='PGA_SPNE')
    p.add_argument('--players', type=int, choices=(3,5), default=3)
    p.add_argument('--liquidity', type=D, default=D('10000000'))
    args = p.parse_args()
    run(args)


if __name__ == '__main__':
    main()

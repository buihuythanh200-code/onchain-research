"""Build round-2 liquidation pair inventory using UPDATED balances and prices.

This DOES NOT execute liquidation, compute rewards, or run a new PGA.
It reconstructs the validated round-1 state and reconciles it against the
round-2 per-borrower status file before exporting potential collateral/debt pairs.

Run:
    python build_round2_liquidation_inventory.py
    python build_round2_liquidation_inventory.py --mode ALL_ELIGIBLE --source sensitivity
"""
import argparse
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from validate_liquidation_state_update import (
    D, BLOCK, FILES, EPS, load_csv, parse_shocks, calculate_hf,
    update_position, write_csv,
)
from prepare_cascade_round2 import NEEDED, classify, load_impacts

TOL_HF = D('0.00001')
TOL_USD = D('0.0001')


def make_assets(rows):
    result = defaultdict(dict)
    for r in rows:
        user, addr = r['borrower_address'].lower(), r['asset_address'].lower()
        if addr in result[user]:
            raise ValueError(f'Duplicate reserve: {user} {addr}')
        result[user][addr] = {
            'symbol': r['symbol'], 'reserve_id': int(r['reserve_id']),
            'emode_category': int(r['emode_category']),
            'baseline_price_usd': D(r['price_usd']),
            'collateral_amount': D(r['collateral_amount']),
            'debt_amount': D(r['debt_amount']),
            'collateral_enabled': r['collateral_enabled'].lower() == 'true',
            'base_lt_bps': D(r['base_lt_bps']),
        }
    return result


def select_pairs(rows):
    by_user = defaultdict(list)
    for r in rows:
        by_user[(r['scenario'], r['borrower_address'].lower())].append(r)
    return {
        key: sorted(items, key=lambda r: (
            -D(r['estimated_gross_profit_before_gas_swap_slippage_usd']),
            r['collateral_address'].lower(), r['debt_address'].lower()))[0]
        for key, items in by_user.items()
    }


def main(args):
    root = args.directory
    tables = {key: load_csv(root / fname, NEEDED[key]) for key, fname in FILES.items()}
    assets = make_assets(tables['assets'])
    emode_lt = {int(r['emode_category']): D(r['liquidation_threshold_bps'])/10000
                for r in tables['emodes']}
    bitmaps = {int(r['emode_category']): int(r['collateral_bitmap'])
               for r in tables['mapping']}
    shocks = {r['scenario']: parse_shocks(r['price_shocks']) for r in tables['scenarios']}
    selected = select_pairs(tables['pairs'])
    games = {}
    for r in tables['pga']:
        if int(r['num_liquidators']) != args.players:
            continue
        key = (r['scenario'], r['borrower_address'].lower())
        if key in games:
            raise ValueError(f'Duplicate PGA result: {key}')
        games[key] = r
    if set(games) != set(selected):
        raise ValueError('Selected pairs and PGA rows differ')

    suffix = f'{args.mode.lower()}_n{args.players}'
    verified_rows = load_csv(root / f'aave_cascade_round2_state_{suffix}.csv',
                             ['scenario','borrower_address','status',
                              'hf_after_round1_and_impact','collateral_usd_after',
                              'debt_usd_after','executed_round1'])
    verified = {}
    for r in verified_rows:
        key = (r['scenario'], r['borrower_address'].lower())
        if key in verified:
            raise ValueError(f'Duplicate state: {key}')
        verified[key] = r

    impact = load_impacts(root, args.source, args.mode, args.players, args.liquidity)
    candidates, summaries = [], []
    total_checked = 0
    for scenario, base_shock in sorted(shocks.items()):
        if scenario == 'S0_BASELINE':
            continue
        impacts = impact.get(scenario)
        if impacts is None:
            raise ValueError(f'Missing impact values for {scenario}')
        final_shock = {
            sym: (1 + base_shock.get(sym, D(0))) * (1 - impacts.get(sym, D(0))) - 1
            for sym in set(base_shock) | set(impacts)
        }
        eligible_count = eligible_with_pairs = 0
        scenario_pairs = 0
        for user, original_tokens in sorted(assets.items()):
            key = (scenario, user)
            reference = verified.get(key)
            if reference is None:
                raise ValueError(f'Missing reconciled state: {key}')
            pair = selected.get(key)
            game = games.get(key)
            executed = bool(pair is not None and (args.mode == 'ALL_ELIGIBLE' or
                                                  game['winner'] != 'NONE'))
            if reference['executed_round1'].lower() != str(executed).lower():
                raise ValueError(f'Execution mismatch: {key}')
            updated = original_tokens
            if executed:
                updated, _ = update_position(original_tokens, pair, base_shock)
            hf, coll_usd, debt_usd = calculate_hf(updated, final_shock, emode_lt, bitmaps)
            status = classify(hf, coll_usd, debt_usd)
            if status != reference['status']:
                raise ValueError(f'Status mismatch: {key}: {status} != {reference["status"]}')
            if abs(coll_usd - D(reference['collateral_usd_after'])) > TOL_USD:
                raise ValueError(f'Collateral value mismatch: {key}')
            if abs(debt_usd - D(reference['debt_usd_after'])) > TOL_USD:
                raise ValueError(f'Debt value mismatch: {key}')
            if hf is None:
                if reference['hf_after_round1_and_impact'] != 'NO_DEBT':
                    raise ValueError(f'No-debt HF mismatch: {key}')
            elif abs(hf - D(reference['hf_after_round1_and_impact'])) > TOL_HF:
                raise ValueError(f'HF mismatch: {key}')
            total_checked += 1
            if status != 'ELIGIBLE_ROUND2':
                continue
            eligible_count += 1
            collateral = []
            debts = []
            for addr, token in updated.items():
                px = token['baseline_price_usd'] * (1 + final_shock.get(token['symbol'], D(0)))
                if token['collateral_enabled'] and token['collateral_amount'] > 0:
                    collateral.append((addr, token, px))
                if token['debt_amount'] > 0:
                    debts.append((addr, token, px))
            if not collateral or not debts:
                raise ValueError(f'Eligible user has no collateral/debt: {key}')
            eligible_with_pairs += 1
            for ca, c, cp in collateral:
                for da, d, dp in debts:
                    candidates.append({
                        'scenario': scenario, 'snapshot_block': BLOCK,
                        'execution_mode': args.mode, 'num_liquidators': args.players,
                        'borrower_address': user, 'hf_at_round2_start': str(hf),
                        'emode_category': c['emode_category'],
                        'collateral_address': ca, 'collateral_symbol': c['symbol'],
                        'collateral_reserve_id': c['reserve_id'],
                        'collateral_amount_remaining': str(c['collateral_amount']),
                        'collateral_price_round2_usd': str(cp),
                        'collateral_value_round2_usd': str(c['collateral_amount']*cp),
                        'debt_address': da, 'debt_symbol': d['symbol'],
                        'debt_amount_remaining': str(d['debt_amount']),
                        'debt_price_round2_usd': str(dp),
                        'debt_value_round2_usd': str(d['debt_amount']*dp),
                        'note': 'PAIR INVENTORY ONLY; not a feasible liquidation quote',
                    })
                    scenario_pairs += 1
        summaries.append({
            'scenario': scenario, 'execution_mode': args.mode,
            'num_liquidators': args.players,
            'eligible_round2': eligible_count,
            'borrowers_with_inventory_pairs': eligible_with_pairs,
            'alternative_pairs_round2': scenario_pairs,
        })
        print(f'{scenario}: eligible_round2={eligible_count}, '
              f'borrowers_with_pairs={eligible_with_pairs}, alternative_pairs={scenario_pairs}')

    if total_checked != len(assets)*len(summaries):
        raise ValueError('Borrower/scenario verification count mismatch')
    if not candidates:
        raise ValueError('No candidates; nothing to export')
    candidate_path = root / f'aave_round2_pair_inventory_{suffix}.csv'
    summary_path = root / f'aave_round2_pair_inventory_summary_{suffix}.csv'
    write_csv(candidate_path, candidates, list(candidates[0]))
    write_csv(summary_path, summaries, list(summaries[0]))
    print('=== ROUND-2 PAIR INVENTORY COMPLETE ===')
    print(f'CHECKED STATES: {total_checked}')
    print(f'TOTAL ALTERNATIVE PAIRS: {len(candidates)}')
    print(f'CSV: {candidate_path.name}')
    print(f'CSV: {summary_path.name}')
    print('WARNING: No liquidation reward, PGA, or new liquidation has been calculated.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path('.'))
    parser.add_argument('--players', type=int, choices=(3, 5), default=3)
    parser.add_argument('--mode', choices=('PGA_SPNE','ALL_ELIGIBLE'), default='PGA_SPNE')
    parser.add_argument('--source', choices=('round1','sensitivity'), default=None)
    parser.add_argument('--liquidity', type=D, default=D('10000000'))
    arguments = parser.parse_args()
    if arguments.source is None:
        arguments.source = 'round1' if arguments.mode == 'PGA_SPNE' else 'sensitivity'
    if arguments.source == 'round1' and (arguments.mode != 'PGA_SPNE' or
                                          arguments.liquidity != D('10000000')):
        parser.error('round1 impacts require PGA_SPNE and $10m liquidity')
    main(arguments)

"""Module 2.5.2b: audit residual HF<1 following one hypothetical liquidation.

Only reads CSV outputs from validate_liquidation_state_update.py and the PGA/stress
inputs. Distinguishes executed-but-still-unhealthy borrowers from borrowers whose
opportunity had no modeled PGA winner. No price feedback or second-round execution.

Usage:
  python inspect_residual_liquidation_risk.py
  python inspect_residual_liquidation_risk.py --mode ALL_ELIGIBLE
  python inspect_residual_liquidation_risk.py --players 5
"""
import argparse
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

D = Decimal


def read(path, required):
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    with path.open(newline='', encoding='utf-8-sig') as fp:
        r = csv.DictReader(fp)
        missing = set(required) - set(r.fieldnames or ())
        if missing:
            raise ValueError(f"{path.name}: missing columns {sorted(missing)}")
        return list(r)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('.'))
    p.add_argument('--mode', choices=['PGA_SPNE', 'ALL_ELIGIBLE'], default='PGA_SPNE')
    p.add_argument('--players', type=int, choices=[3, 5], default=3)
    args = p.parse_args()
    suffix = f'{args.mode.lower()}_n{args.players}'
    detail_path = args.directory / f'aave_liquidation_state_validation_{suffix}.csv'
    detail = read(detail_path, ['scenario', 'borrower_address', 'hf_before_liquidation', 'hf_after_liquidation',
                                'debt_usd_after', 'collateral_usd_after', 'debt_repaid_usd',
                                'post_liquidation_still_underwater'])
    stress = read(args.directory / 'aave_stress_test_details.csv',
                  ['scenario', 'borrower_address', 'modeled_hf', 'modeled_debt_usd'])
    pga = read(args.directory / 'aave_pga_spne_results.csv',
               ['scenario', 'borrower_address', 'num_liquidators', 'winner'])

    selected = {(r['scenario'], r['borrower_address'].lower()): r for r in detail}
    if len(selected) != len(detail):
        raise ValueError('Duplicate executed (scenario, borrower) in detail CSV')
    winners = {(r['scenario'], r['borrower_address'].lower()): r['winner']
               for r in pga if int(r['num_liquidators']) == args.players}
    initial = {(r['scenario'], r['borrower_address'].lower()): r for r in stress
               if D(r['modeled_hf']) < 1}
    if not initial:
        raise ValueError('No initially eligible borrowers')
    if set(winners) != set(initial):
        raise ValueError('PGA keys and initially eligible stress cases do not match')
    if not set(selected) <= set(initial):
        raise ValueError('Executed rows include borrowers not initially liquidatable')

    out = []
    count = defaultdict(lambda: defaultdict(int))
    for key in sorted(initial):
        scenario, borrower = key
        original = initial[key]
        executed = selected.get(key)
        winner = winners[key]
        should_execute = (args.mode == 'ALL_ELIGIBLE' or winner != 'NONE')
        if (executed is not None) != should_execute:
            raise ValueError(f'Execution mismatch: {key}, winner={winner}, mode={args.mode}')
        if executed is None:
            status = 'NOT_EXECUTED_NO_WINNER'
            current_hf = original['modeled_hf']
            remaining_debt = original['modeled_debt_usd']
            before_hf = original['modeled_hf']
            repaid = '0'
        else:
            before_hf = executed['hf_before_liquidation']
            current_hf = executed['hf_after_liquidation']
            remaining_debt = executed['debt_usd_after']
            repaid = executed['debt_repaid_usd']
            if current_hf == 'NO_DEBT':
                status = 'EXECUTED_NO_DEBT'
            elif D(current_hf) < 1:
                status = 'EXECUTED_STILL_HF_BELOW_1'
            else:
                status = 'EXECUTED_HF_AT_LEAST_1'
            if (status == 'EXECUTED_STILL_HF_BELOW_1') != (executed['post_liquidation_still_underwater'].lower() == 'true'):
                raise ValueError(f'Flag mismatch for {key}')
        count[scenario][status] += 1
        out.append({'scenario': scenario, 'borrower_address': borrower, 'mode': args.mode,
                    'num_liquidators': args.players, 'winner': winner,
                    'status': status, 'hf_before': before_hf, 'hf_after_or_unchanged': current_hf,
                    'debt_repaid_usd': repaid, 'remaining_debt_usd': remaining_debt})

    output = args.directory / f'aave_residual_risk_audit_{suffix}.csv'
    with output.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)

    print('=== POST-LIQUIDATION RESIDUAL RISK AUDIT ===')
    print('MODE:', args.mode, '| PLAYERS:', args.players)
    totals = defaultdict(int)
    for scenario in sorted(count):
        groups = count[scenario]
        for k, v in groups.items():
            totals[k] += v
        print(scenario, '| initial=', sum(groups.values()),
              '| not_executed=', groups['NOT_EXECUTED_NO_WINNER'],
              '| executed_still_hf_below_1=', groups['EXECUTED_STILL_HF_BELOW_1'],
              '| executed_hf_at_least_1=', groups['EXECUTED_HF_AT_LEAST_1'],
              '| executed_no_debt=', groups['EXECUTED_NO_DEBT'], sep='')
    print('=== TOTALS (SCENARIO OCCURRENCES) ===')
    for status in ('NOT_EXECUTED_NO_WINNER', 'EXECUTED_STILL_HF_BELOW_1',
                   'EXECUTED_HF_AT_LEAST_1', 'EXECUTED_NO_DEBT'):
        print(f'{status}: {totals[status]}')
    print('=== BORROWERS STILL ELIGIBLE AFTER FIRST HYPOTHETICAL UPDATE ===')
    for row in out:
        if row['status'] in ('NOT_EXECUTED_NO_WINNER', 'EXECUTED_STILL_HF_BELOW_1'):
            print(row['scenario'], row['borrower_address'], row['status'],
                  'HF=', row['hf_after_or_unchanged'],
                  'remaining_debt_usd=', row['remaining_debt_usd'])
    print('CSV SAVED:', output.name)
    print('NOTE: No price feedback, no fresh rewards, no second-round liquidation in this audit.')


if __name__ == '__main__':
    main()

"""Audit potential unbacked debt after the first modeled liquidation.

Uses *output files* from validate_liquidation_state_update.py.
It does not change user balances or attempt additional liquidations.

Usage:
    python audit_post_liquidation_insolvency.py
    python audit_post_liquidation_insolvency.py --mode ALL_ELIGIBLE
    python audit_post_liquidation_insolvency.py --self-test
"""
import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

D = Decimal
EPS_USD = D('0.000001')


def classify(hf_text, collateral_text, debt_text, eps=EPS_USD):
    collateral = D(collateral_text)
    debt = D(debt_text)
    if collateral < -eps or debt < -eps:
        raise ValueError('Invalid negative balance')
    if debt <= eps:
        return 'NO_REMAINING_DEBT'
    if collateral <= eps:
        return 'ZERO_COLLATERAL_POSITIVE_DEBT'
    hf = D(hf_text)
    if hf < 0:
        raise ValueError('Negative HF')
    if hf < 1:
        return 'COLLATERAL_REMAINS_HF_BELOW_1'
    return 'HF_AT_LEAST_1'


def self_test():
    assert classify('0', '0', '14383.03') == 'ZERO_COLLATERAL_POSITIVE_DEBT'
    assert classify('0.8', '200', '400') == 'COLLATERAL_REMAINS_HF_BELOW_1'
    assert classify('1.05', '200', '100') == 'HF_AT_LEAST_1'
    assert classify('NO_DEBT', '0', '0') == 'NO_REMAINING_DEBT'
    assert classify('0', '0.00000000001', '14383') == 'ZERO_COLLATERAL_POSITIVE_DEBT'
    print('SELF-TEST PASS: remaining debt / collateral / HF categories')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('.'))
    p.add_argument('--players', type=int, choices=(3, 5), default=3)
    p.add_argument('--mode', choices=('PGA_SPNE', 'ALL_ELIGIBLE'), default='PGA_SPNE')
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        self_test()
        return

    suffix = f'{args.mode.lower()}_n{args.players}'
    source = args.directory / f'aave_liquidation_state_validation_{suffix}.csv'
    if not source.exists():
        raise FileNotFoundError(f'Missing: {source} — run validate_liquidation_state_update.py first')
    with source.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        required = {'scenario','borrower_address','hf_after_liquidation',
                    'collateral_usd_after','debt_usd_after','debt_repaid_usd'}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'Input missing columns: {sorted(missing)}')
        raw = list(reader)
    if not raw:
        raise ValueError('Empty source file')
    keys = [(r['scenario'], r['borrower_address'].lower()) for r in raw]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate executed scenario/borrower entries')

    out = []
    counts = defaultdict(Counter)
    for r in raw:
        status = classify(r['hf_after_liquidation'], r['collateral_usd_after'], r['debt_usd_after'])
        scenario = r['scenario']
        counts[scenario][status] += 1
        debt = D(r['debt_usd_after'])
        collateral = D(r['collateral_usd_after'])
        out.append({
            'scenario': scenario,
            'borrower_address': r['borrower_address'].lower(),
            'status': status,
            'hf_after': r['hf_after_liquidation'],
            'collateral_usd_after': str(collateral),
            'debt_usd_after': str(debt),
            'unbacked_debt_usd_if_zero_collateral': str(debt if status == 'ZERO_COLLATERAL_POSITIVE_DEBT' else D(0)),
            'potential_next_liquidation': 'YES' if status == 'COLLATERAL_REMAINS_HF_BELOW_1' else 'NO',
            'model_warning': 'Modeled, not verified Aave bad debt' if status == 'ZERO_COLLATERAL_POSITIVE_DEBT' else '',
        })

    output = args.directory / f'aave_post_liquidation_insolvency_audit_{suffix}.csv'
    with output.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(out[0]))
        writer.writeheader()
        writer.writerows(out)
    print('=== INSOLVENCY / RESIDUAL COLLATERAL AUDIT ===')
    print('MODE:', args.mode, '| PLAYERS:', args.players, '| EXECUTED:', len(raw))
    for scenario in sorted(counts):
        c = counts[scenario]
        print(f'{scenario}: no_debt={c["NO_REMAINING_DEBT"]}, '
              f'zero_collateral_debt={c["ZERO_COLLATERAL_POSITIVE_DEBT"]}, '
              f'underwater_with_collateral={c["COLLATERAL_REMAINS_HF_BELOW_1"]}, '
              f'healthy={c["HF_AT_LEAST_1"]}')
    suspicious = [r for r in out if r['status'] in ('ZERO_COLLATERAL_POSITIVE_DEBT', 'COLLATERAL_REMAINS_HF_BELOW_1')]
    print('=== CASES NEEDING ATTENTION ===')
    for r in suspicious:
        print(f'{r["scenario"]} | {r["borrower_address"]} | {r["status"]} '
              f'| collateral=${D(r["collateral_usd_after"]):,.6f} '
              f'| debt=${D(r["debt_usd_after"]):,.6f} '
              f'| HF={r["hf_after"]}')
    if not suspicious:
        print('NONE')
    print('CSV SAVED:', output.name)
    print('WARNING: Zero collateral plus debt is *potential modeled unbacked debt*, not verified protocol bad debt.')
    print('WARNING: Only addresses with positive collateral and HF < 1 are potential repeat-liquidation candidates.')


if __name__ == '__main__':
    main()

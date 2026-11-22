"""Audit round-2 borrower state against the initial stress-test population.

Does not modify existing data. Produces an address-level reconciliation report.
Usage:
    python audit_round2_consistency.py
    python audit_round2_consistency.py --mode ALL_ELIGIBLE
"""
import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

EXPECTED_STATUSES = {'ELIGIBLE_ROUND2', 'UNBACKED_DEBT_MODEL', 'HEALTHY', 'NO_DEBT'}

def load(path, required):
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{path.name}: missing columns {sorted(missing)}')
        return list(reader)

def truth(v):
    if str(v).strip().lower() not in {'true', 'false'}:
        raise ValueError(f'Invalid boolean: {v}')
    return str(v).strip().lower() == 'true'

def audit(directory, mode, players):
    suffix = f'{mode.lower()}_n{players}'
    state_path = directory / f'aave_cascade_round2_state_{suffix}.csv'
    summary_path = directory / f'aave_cascade_round2_state_summary_{suffix}.csv'
    state = load(state_path, ['scenario','borrower_address','status','initially_eligible',
                              'executed_round1','newly_eligible_due_to_feedback',
                              'hf_after_round1_and_impact','collateral_usd_after','debt_usd_after'])
    summary = load(summary_path, ['scenario','executed_round1','newly_eligible_after_feedback',
                                   'eligible_round2_total','unbacked_debt_accounts',
                                   'no_debt_accounts','healthy_accounts'])
    initial = load(directory / 'aave_stress_test_details.csv',
                   ['scenario','borrower_address','modeled_hf','liquidatable_in_model'])
    # Do not trust aggregate counts alone: derive initial classifications by address.
    initial_lookup = {}
    for r in initial:
        k = (r['scenario'], r['borrower_address'].lower())
        if k in initial_lookup:
            raise ValueError(f'Duplicate stress-test borrower: {k}')
        initial_lookup[k] = truth(r['liquidatable_in_model'])

    grouped = defaultdict(list)
    seen = set()
    problems = []
    for r in state:
        scenario, user = r['scenario'], r['borrower_address'].lower()
        key = (scenario,user)
        if key in seen:
            problems.append((scenario, user, 'DUPLICATE_BORROWER'))
        seen.add(key)
        if key not in initial_lookup:
            problems.append((scenario,user,'NOT_IN_STRESS_TEST'))
        elif truth(r['initially_eligible']) != initial_lookup[key]:
            problems.append((scenario,user,'INITIAL_CLASSIFICATION_DIFFERS'))
        if r['status'] not in EXPECTED_STATUSES:
            problems.append((scenario,user,'UNKNOWN_STATUS'))
        if truth(r['newly_eligible_due_to_feedback']) and (truth(r['initially_eligible']) or r['status'] != 'ELIGIBLE_ROUND2'):
            problems.append((scenario,user,'INVALID_NEWLY_ELIGIBLE_FLAG'))
        if r['status'] == 'ELIGIBLE_ROUND2' and r['hf_after_round1_and_impact'] == 'NO_DEBT':
            problems.append((scenario,user,'ELIGIBLE_WITHOUT_HF'))
        if r['status'] in ('ELIGIBLE_ROUND2','UNBACKED_DEBT_MODEL') and float(r['debt_usd_after']) <= 0:
            problems.append((scenario,user,'RISK_STATUS_WITHOUT_DEBT'))
        grouped[scenario].append(r)

    summary_lookup = {r['scenario']: r for r in summary}
    output_rows = []
    print('=== ROUND-2 STATE CONSISTENCY AUDIT ===')
    print(f'MODE: {mode} | PLAYERS: {players}')
    for scenario, rows in sorted(grouped.items()):
        counts = Counter(r['status'] for r in rows)
        initial_count = sum(truth(r['initially_eligible']) for r in rows)
        new_count = sum(truth(r['newly_eligible_due_to_feedback']) for r in rows)
        executed_count = sum(truth(r['executed_round1']) for r in rows)
        # Partition eligible after feedback into old (residual) and new borrowers.
        old_eligible = [r for r in rows if r['status'] == 'ELIGIBLE_ROUND2' and truth(r['initially_eligible'])]
        new_eligible = [r for r in rows if r['status'] == 'ELIGIBLE_ROUND2' and not truth(r['initially_eligible'])]
        unexplained = [r for r in new_eligible if not truth(r['newly_eligible_due_to_feedback'])]
        if len(new_eligible) != new_count or unexplained:
            problems.append((scenario,'-', 'NEW_ELIGIBLE_COUNT_MISMATCH'))
        if scenario in summary_lookup:
            expected = summary_lookup[scenario]
            for actual, field in [
                (executed_count,'executed_round1'), (new_count,'newly_eligible_after_feedback'),
                (counts['ELIGIBLE_ROUND2'],'eligible_round2_total'),
                (counts['UNBACKED_DEBT_MODEL'],'unbacked_debt_accounts'),
                (counts['NO_DEBT'],'no_debt_accounts'),(counts['HEALTHY'],'healthy_accounts')]:
                if actual != int(expected[field]):
                    problems.append((scenario,'-',f'SUMMARY_MISMATCH_{field}'))
        else:
            problems.append((scenario,'-','MISSING_SUMMARY'))
        print(f'{scenario}: initial={initial_count} executed={executed_count} '
              f'old_eligible={len(old_eligible)} new_eligible={len(new_eligible)} '
              f'unbacked={counts["UNBACKED_DEBT_MODEL"]} total={len(rows)}')
        for kind, candidates in [('OLD_ELIGIBLE', old_eligible),('NEW_ELIGIBLE', new_eligible),
                                  ('UNBACKED', [r for r in rows if r['status'] == 'UNBACKED_DEBT_MODEL'])]:
            for r in candidates:
                item = {'scenario':scenario, 'classification':kind,
                        'borrower_address':r['borrower_address'],
                        'initially_eligible':r['initially_eligible'],
                        'executed_round1':r['executed_round1'],
                        'hf_after_round1_and_impact':r['hf_after_round1_and_impact'],
                        'collateral_usd_after':r['collateral_usd_after'],
                        'debt_usd_after':r['debt_usd_after']}
                output_rows.append(item)
                print(f'  {kind} {r["borrower_address"]} '
                      f'executed={r["executed_round1"]} '
                      f'HF={r["hf_after_round1_and_impact"]} debt=${float(r["debt_usd_after"]):,.2f}')
    if set(summary_lookup) != set(grouped):
        problems.append(('-', '-', 'SCENARIO_SET_MISMATCH'))
    output_path = directory / f'aave_round2_risk_reconciliation_{suffix}.csv'
    columns = ['scenario','classification','borrower_address','initially_eligible','executed_round1',
               'hf_after_round1_and_impact','collateral_usd_after','debt_usd_after']
    with output_path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f'REPORT: {output_path.name}')
    print(f'CONSISTENCY CHECK: {"PASS" if not problems else "FAIL"} ({len(problems)} issues)')
    for scenario,user,why in problems[:20]:
        print(f'  ISSUE: {scenario} {user} {why}')
    if problems:
        raise SystemExit(1)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('.'))
    parser.add_argument('--mode',choices=('PGA_SPNE','ALL_ELIGIBLE'),default='PGA_SPNE')
    parser.add_argument('--players',type=int,default=3)
    args=parser.parse_args()
    audit(args.directory,args.mode,args.players)

if __name__ == '__main__':
    main()

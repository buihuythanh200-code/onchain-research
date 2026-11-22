"""Module 2.4a: join stressed HF with historical Aave V3 token positions.

This only identifies *modeled* liquidation candidates. It does not estimate
actual debt repaid, collateral seized, liquidation rewards, or PGA outcomes.
"""
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

SNAPSHOT_BLOCK = 26402092
DETAILS_FILE = Path('aave_stress_test_details.csv')
ASSETS_FILE = Path('aave_user_assets_full.csv')
OUTPUT_FILE = Path('aave_liquidation_candidates.csv')
SUMMARY_FILE = Path('aave_liquidation_candidates_summary.csv')

FIELDS = [
    'scenario', 'snapshot_block', 'borrower_address',
    'modeled_hf', 'modeled_debt_usd', 'emode_category',
    'reserve_id', 'asset_address', 'symbol',
    'collateral_enabled', 'collateral_amount', 'debt_amount',
    'baseline_price_usd', 'baseline_collateral_usd',
    'baseline_debt_usd', 'base_bonus_bps',
]


def read_csv(path, required):
    if not path.is_file():
        raise FileNotFoundError(f'Missing file: {path}')
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'{path}: missing columns {sorted(missing)}')
        return list(reader)


def is_true(value):
    normalized = str(value).strip().lower()
    if normalized in ('true', '1', 'yes'):
        return True
    if normalized in ('false', '0', 'no', ''):
        return False
    raise ValueError(f'Invalid boolean value: {value!r}')


def write_csv(path, fieldnames, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    details = read_csv(DETAILS_FILE, [
        'scenario', 'snapshot_block', 'borrower_address',
        'modeled_hf', 'modeled_debt_usd', 'liquidatable_in_model'
    ])
    assets = read_csv(ASSETS_FILE, [
        'snapshot_block', 'borrower_address', 'emode_category',
        'reserve_id', 'asset_address', 'symbol', 'collateral_enabled',
        'collateral_amount', 'debt_amount', 'price_usd',
        'collateral_usd', 'debt_usd', 'base_bonus_bps'
    ])

    assets_by_user = defaultdict(list)
    for row in assets:
        if int(row['snapshot_block']) != SNAPSHOT_BLOCK:
            raise ValueError('Asset snapshot mismatch')
        assets_by_user[row['borrower_address'].lower()].append(row)

    rows_out = []
    stats = defaultdict(lambda: {'borrowers': set(), 'debt': Decimal('0'), 'asset_rows': 0})
    seen = set()
    scenarios = set()

    for d in details:
        if int(d['snapshot_block']) != SNAPSHOT_BLOCK:
            raise ValueError('Stress-test snapshot mismatch')
        scenario = d['scenario']
        user = d['borrower_address'].lower()
        scenarios.add(scenario)
        key = (scenario, user)
        if key in seen:
            raise ValueError(f'Duplicate borrower/scenario: {key}')
        seen.add(key)

        hf = Decimal(d['modeled_hf'])
        marked = is_true(d['liquidatable_in_model'])
        if marked != (hf < 1):
            raise ValueError(f'HF/flag mismatch: {scenario} {user} HF={hf}')
        if not marked:
            continue
        if user not in assets_by_user:
            raise ValueError(f'No asset positions for candidate: {user}')

        debt = Decimal(d['modeled_debt_usd'])
        if debt <= 0:
            raise ValueError(f'Candidate without positive debt: {user}')

        stats[scenario]['borrowers'].add(user)
        stats[scenario]['debt'] += debt
        for a in assets_by_user[user]:
            rows_out.append({
                'scenario': scenario,
                'snapshot_block': SNAPSHOT_BLOCK,
                'borrower_address': d['borrower_address'],
                'modeled_hf': d['modeled_hf'],
                'modeled_debt_usd': d['modeled_debt_usd'],
                'emode_category': a['emode_category'],
                'reserve_id': a['reserve_id'],
                'asset_address': a['asset_address'],
                'symbol': a['symbol'],
                'collateral_enabled': a['collateral_enabled'],
                'collateral_amount': a['collateral_amount'],
                'debt_amount': a['debt_amount'],
                'baseline_price_usd': a['price_usd'],
                'baseline_collateral_usd': a['collateral_usd'],
                'baseline_debt_usd': a['debt_usd'],
                'base_bonus_bps': a['base_bonus_bps'],
            })
            stats[scenario]['asset_rows'] += 1

    if not details or not assets:
        raise ValueError('Empty input data')
    write_csv(OUTPUT_FILE, FIELDS, rows_out)

    summary_rows = []
    for scenario in sorted(scenarios):
        s = stats[scenario]
        summary_rows.append({
            'scenario': scenario,
            'candidate_borrowers': len(s['borrowers']),
            'candidate_modeled_debt_usd': str(s['debt']),
            'asset_rows': s['asset_rows'],
        })
    write_csv(SUMMARY_FILE,
              ['scenario','candidate_borrowers','candidate_modeled_debt_usd','asset_rows'],
              summary_rows)

    print('=== LIQUIDATION CANDIDATE EXTRACTION ===')
    print('SNAPSHOT BLOCK:', SNAPSHOT_BLOCK)
    print('STRESS TEST ROWS:', len(details))
    print('BASELINE ASSET ROWS:', len(assets))
    for s in summary_rows:
        print(f"{s['scenario']}: candidates={s['candidate_borrowers']}, "
              f"modeled debt=${Decimal(s['candidate_modeled_debt_usd']):,.2f}, "
              f"token rows={s['asset_rows']}")
    print('CSV SAVED:', OUTPUT_FILE)
    print('SUMMARY SAVED:', SUMMARY_FILE)
    print('NOTE: Token amounts and baseline prices are historical; this is NOT a liquidation execution simulation.')

if __name__ == '__main__':
    main()

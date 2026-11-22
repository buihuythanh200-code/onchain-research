"""Aave V3 historical-position price stress test (fixed quantities; no liquidations).

Inputs are the CSV snapshots produced by the Module 2 data collection scripts.
All amounts are USD-denominated using historical Aave oracle prices.
This is an off-chain counterfactual model, not a replay of on-chain liquidations.
"""

import csv
from collections import defaultdict
from decimal import Decimal, getcontext
from pathlib import Path

getcontext().prec = 50
D = Decimal
SNAPSHOT_BLOCK = 26402092
POSITIONS_FILE = Path('aave_position_risk_analysis.csv')
ASSETS_FILE = Path('aave_user_assets_full.csv')
RESERVES_FILE = Path('aave_reserves.csv')
EMODE_FILE = Path('aave_emode_categories.csv')
MAPPING_FILE = Path('aave_emode_asset_mapping_resolved.csv')
OUTPUT_DETAILS = Path('aave_stress_test_details.csv')
OUTPUT_SUMMARY = Path('aave_stress_test_summary.csv')

# Price shocks are hypothetical fractions: D('-0.10') means price falls 10%.
# Assets not specified remain at their historical snapshot prices.
ETH_CORRELATED = {
    'WETH', 'wstETH', 'cbETH', 'rETH', 'weETH',
    'osETH', 'ETHx', 'rsETH', 'tETH', 'ezETH'
}
SCENARIOS = {
    'S0_BASELINE': {},
    'S1_ETH_WETH_ONLY_MINUS_10': {'WETH': D('-0.10')},
    'S2_ETH_CORRELATED_MINUS_10': {
        symbol: D('-0.10') for symbol in ETH_CORRELATED
    },
    'S3_LINK_MINUS_15': {'LINK': D('-0.15')},
    'S4_SUSDE_MINUS_10': {'sUSDe': D('-0.10')},
    'S5_ETH_CORRELATED_MINUS_15': {
        symbol: D('-0.15') for symbol in ETH_CORRELATED
    },
    'S6_LINK_MINUS_15_SUSDE_MINUS_10': {
        'LINK': D('-0.15'), 'sUSDe': D('-0.10')
    },
}


def load_csv(path):
    if not path.is_file():
        raise FileNotFoundError(f'Missing required input: {path}')
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def validate_block(rows, label):
    if not rows:
        raise ValueError(f'{label} is empty')
    for row in rows:
        if int(row['snapshot_block']) != SNAPSHOT_BLOCK:
            raise ValueError(f'{label}: historical snapshot mismatch')


def pct_error(predicted, onchain):
    if onchain == 0:
        return D(0) if predicted == 0 else D('Infinity')
    return abs(predicted - onchain) / abs(onchain)


def main():
    positions = load_csv(POSITIONS_FILE)
    assets = load_csv(ASSETS_FILE)
    reserves = load_csv(RESERVES_FILE)
    emodes = load_csv(EMODE_FILE)
    mappings = load_csv(MAPPING_FILE)
    for name, rows in [
        ('positions', positions), ('assets', assets), ('reserves', reserves),
        ('emode categories', emodes), ('emode mapping', mappings),
    ]:
        validate_block(rows, name)

    position_by_user = {}
    for row in positions:
        key = row['borrower_address'].lower()
        if key in position_by_user:
            raise ValueError(f'Duplicate position: {key}')
        if D(row['debt_usd']) <= 0:
            raise ValueError(f'Expected active borrower: {key}')
        position_by_user[key] = row

    reserve_by_address = {}
    for r in reserves:
        key = r['asset_address'].lower()
        if key in reserve_by_address:
            raise ValueError(f'Duplicate reserve: {key}')
        reserve_by_address[key] = r

    emode_lt = {}
    for r in emodes:
        key = int(r['emode_category'])
        if key in emode_lt:
            raise ValueError(f'Duplicate E-mode category: {key}')
        emode_lt[key] = D(r['liquidation_threshold_bps']) / D(10000)

    # Use bitmap membership by reserve ID rather than token symbol.
    category_bitmap = {}
    for r in mappings:
        key = int(r['emode_category'])
        if key in category_bitmap:
            raise ValueError(f'Duplicate E-mode bitmap: {key}')
        category_bitmap[key] = int(r['collateral_bitmap'])

    assets_by_user = defaultdict(list)
    for r in assets:
        user = r['borrower_address'].lower()
        if user not in position_by_user:
            raise ValueError(f'Unexpected borrower in assets: {user}')
        reserve = reserve_by_address.get(r['asset_address'].lower())
        if reserve is None:
            raise ValueError(f'Unknown asset address: {r["asset_address"]}')
        if reserve['symbol'] != r['symbol']:
            raise ValueError(f'Symbol mismatch for {r["asset_address"]}')
        assets_by_user[user].append(r)

    if set(assets_by_user) != set(position_by_user):
        missing = set(position_by_user) - set(assets_by_user)
        raise ValueError(f'Missing positions for {len(missing)} borrowers')

    symbols = {r['symbol'] for r in reserves}
    for scenario, shock_by_symbol in SCENARIOS.items():
        unknown = set(shock_by_symbol) - symbols
        if unknown:
            raise ValueError(f'{scenario}: unknown symbols {sorted(unknown)}')
        if any(value <= D(-1) or value > 0 for value in shock_by_symbol.values()):
            raise ValueError(f'{scenario}: shock values must be in (-1, 0]')

    # Precompute effective liquidation thresholds and USD valuation components.
    per_user = {}
    for user, rows in assets_by_user.items():
        position = position_by_user[user]
        categories = {int(r['emode_category']) for r in rows}
        if len(categories) != 1:
            raise ValueError(f'Conflicting E-mode categories for {user}')
        category = categories.pop()
        if category and (category not in emode_lt or category not in category_bitmap):
            raise ValueError(f'Missing E-mode threshold or mapping for {category}')

        parts = []
        baseline_collateral = D(0)
        baseline_debt = D(0)
        for r in rows:
            reserve_id = int(r['reserve_id'])
            is_collateral = r['collateral_enabled'].strip().lower() == 'true'
            collateral = D(r['collateral_usd']) if is_collateral else D(0)
            debt = D(r['debt_usd'])
            if collateral < 0 or debt < 0:
                raise ValueError(f'Negative balance for {user}')
            lt = D(r['base_lt_bps']) / D(10000)
            if category and ((category_bitmap[category] >> reserve_id) & 1):
                lt = emode_lt[category]
            parts.append((r['symbol'], collateral, debt, lt))
            baseline_collateral += collateral
            baseline_debt += debt
        if baseline_debt <= 0:
            raise ValueError(f'No modeled debt for active borrower {user}')
        expected_collateral = D(position['collateral_usd'])
        expected_debt = D(position['debt_usd'])
        if abs(baseline_collateral - expected_collateral) > D('0.00001'):
            raise ValueError(f'Collateral reconciliation failed for {user}')
        if abs(baseline_debt - expected_debt) > D('0.00001'):
            raise ValueError(f'Debt reconciliation failed for {user}')
        per_user[user] = parts

    def compute_hf(parts, shocks):
        weighted_collateral = D(0)
        total_debt = D(0)
        for symbol, collateral_usd, debt_usd, threshold in parts:
            factor = D(1) + shocks.get(symbol, D(0))
            weighted_collateral += collateral_usd * factor * threshold
            total_debt += debt_usd * factor
        return (weighted_collateral / total_debt, total_debt)

    # Stop if the zero-shock model does not reproduce near-risk on-chain HF.
    mismatches = []
    baseline_errors = []
    for user, parts in per_user.items():
        baseline_hf, _ = compute_hf(parts, {})
        onchain_hf = D(position_by_user[user]['health_factor'])
        rel_err = pct_error(baseline_hf, onchain_hf)
        baseline_errors.append((rel_err, user, onchain_hf, baseline_hf))
        if (baseline_hf < 1) != (onchain_hf < 1):
            mismatches.append(user)
    baseline_errors.sort(reverse=True)
    close_to_threshold_errors = [
        (e, u, a, p) for e, u, a, p in baseline_errors if a < D('1.5')
    ]
    max_near_error = max((x[0] for x in close_to_threshold_errors), default=D(0))

    print('=== BASELINE VALIDATION ===')
    print('BORROWERS:', len(per_user))
    print('HF CLASSIFICATION MISMATCHES:', len(mismatches))
    print('MAX RELATIVE HF ERROR (ALL):', baseline_errors[0][0])
    print('MAX RELATIVE HF ERROR (ONCHAIN HF < 1.5):', max_near_error)
    print('TOP 5 BASELINE RELATIVE ERRORS:')
    for rel, user, actual, calc in baseline_errors[:5]:
        print(f'  {user}: onchain={actual:.9f}, model={calc:.9f}, relative={rel:.9%}')
    if mismatches or max_near_error > D('0.005'):
        raise RuntimeError(
            'Baseline validation failed; do NOT interpret stress results. '
            'Inspect E-mode eligibility and position valuation.'
        )

    details = []
    summary = []
    for scenario_name, shocks in SCENARIOS.items():
        affected = 0
        eligible_debt = D(0)
        minimum_hf = None
        for user, parts in per_user.items():
            hf, debt_usd = compute_hf(parts, shocks)
            eligible = hf < 1
            affected += int(eligible)
            if eligible:
                eligible_debt += debt_usd
            minimum_hf = hf if minimum_hf is None else min(minimum_hf, hf)
            details.append({
                'scenario': scenario_name,
                'snapshot_block': SNAPSHOT_BLOCK,
                'borrower_address': position_by_user[user]['borrower_address'],
                'onchain_hf_baseline': str(position_by_user[user]['health_factor']),
                'modeled_hf': str(hf),
                'modeled_debt_usd': str(debt_usd),
                'liquidatable_in_model': str(eligible),
            })
        summary.append({
            'scenario': scenario_name,
            'snapshot_block': SNAPSHOT_BLOCK,
            'borrowers': len(per_user),
            'liquidatable_count': affected,
            'liquidatable_debt_usd': str(eligible_debt),
            'minimum_modeled_hf': str(minimum_hf),
            'price_shocks': '; '.join(
                f'{k}:{v * 100}%' for k, v in sorted(shocks.items())
            ) or 'none',
        })
        print(f'{scenario_name}: eligible={affected}, affected debt=${eligible_debt:,.2f}, min HF={minimum_hf:.6f}')

    for path, records in [(OUTPUT_DETAILS, details), (OUTPUT_SUMMARY, summary)]:
        with path.open('w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
    print('CSV SAVED:', OUTPUT_SUMMARY)
    print('CSV SAVED:', OUTPUT_DETAILS)
    print('NOTE: Fixed token balances, hypothetical prices, no actual liquidation, no price impact.')


if __name__ == '__main__':
    main()

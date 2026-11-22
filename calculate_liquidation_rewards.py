"""Module 2.4.2 — Illustrative Aave V3 liquidation opportunity valuation.

INPUT: CSV snapshots already collected at Ethereum block 26402092.
OUTPUT: estimates per (scenario, borrower, collateral asset, debt asset).

IMPORTANT:
* These are counterfactual, NOT executed transactions and NOT precise Aave
  liquidationCall simulations. Prices and HF are under hypothetical shocks.
* Close factor is an explicit research approximation (50% vs 100%), not
  guaranteed executable under every deployed Aave V3 implementation.
* The candidates for one borrower are ALTERNATIVE pairs: do not sum their
  potential repayment/rewards as if all happened simultaneously.
* Protocol fee is read at the historical block from reserve configuration.
* No gas, exchange slippage, MEV, leftover/dust checks, liquidity, rounding
  to ERC20 units or dynamic state updates are modeled.
"""

import csv
import time
from collections import defaultdict
from decimal import Decimal, getcontext
from pathlib import Path
from requests.exceptions import HTTPError
from web3 import Web3

getcontext().prec = 50
D = Decimal
BLOCK = 26402092
POOL = Web3.to_checksum_address('0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2')
RPC_URL = 'https://rpc.mevblocker.io'

CANDIDATES = Path('aave_liquidation_candidates.csv')
STRESS_SUMMARY = Path('aave_stress_test_summary.csv')
RESERVES = Path('aave_reserves.csv')
EMODE_CATEGORIES = Path('aave_emode_categories.csv')
EMODE_MAPPING = Path('aave_emode_asset_mapping_resolved.csv')
OUTPUT = Path('aave_liquidation_reward_estimates.csv')
SUMMARY = Path('aave_liquidation_reward_estimates_summary.csv')

HF_CLOSE_FACTOR_THRESHOLD = D('0.95')
SMALL_POSITION_THRESHOLD_USD = D('2000')
DEFAULT_CLOSE_FACTOR = D('0.50')
FULL_CLOSE_FACTOR = D('1.00')

CONFIG_ABI = [{
    'name': 'getConfiguration', 'type': 'function', 'stateMutability': 'view',
    'inputs': [{'name': 'asset', 'type': 'address'}],
    'outputs': [{'name': '', 'type': 'tuple',
                 'components': [{'name': 'data', 'type': 'uint256'}]}],
}]


def load_csv(path, required):
    if not path.is_file():
        raise FileNotFoundError(f'Missing {path}')
    with path.open(newline='', encoding='utf-8-sig') as file:
        rd = csv.DictReader(file)
        missing = set(required) - set(rd.fieldnames or ())
        if missing:
            raise ValueError(f'{path}: missing columns: {sorted(missing)}')
        rows = list(rd)
    if not rows:
        raise ValueError(f'{path}: empty data')
    if 'snapshot_block' in (rd.fieldnames or ()):
        if any(int(r['snapshot_block']) != BLOCK for r in rows):
            raise ValueError(f'{path}: snapshot mismatch')
    return rows


def true_flag(v):
    val = str(v).lower().strip()
    if val in ('true', '1', 'yes'):
        return True
    if val in ('false', '0', 'no'):
        return False
    raise ValueError(f'Unknown boolean: {v}')


def parse_shocks(text):
    if text.strip().lower() in ('none', ''):
        return {}
    out = {}
    for token in text.split(';'):
        symbol, percentage = token.strip().rsplit(':', 1)
        symbol = symbol.strip()
        if symbol in out or not percentage.strip().endswith('%'):
            raise ValueError(f'Invalid shock entry: {token}')
        frac = D(percentage.strip()[:-1]) / D('100')
        if not (D('-1') < frac <= 0):
            raise ValueError(f'Invalid price change {frac} for {symbol}')
        out[symbol] = frac
    return out


def write_csv(path, rows, names):
    with path.open('w', newline='', encoding='utf-8') as f:
        wr = csv.DictWriter(f, fieldnames=names)
        wr.writeheader()
        wr.writerows(rows)


def fetch_protocol_fees(assets):
    """Read Aave reserve configuration at BLOCK; fee occupies bits [152,167]."""
    w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={'timeout': 60}))
    if not w3.is_connected() or w3.eth.chain_id != 1:
        raise RuntimeError('Expected Ethereum Mainnet archive RPC')
    contract = w3.eth.contract(address=POOL, abi=CONFIG_ABI)
    fee_bps = {}
    for i, address in enumerate(sorted(assets), 1):
        for attempt in range(6):
            try:
                time.sleep(1)
                raw = contract.functions.getConfiguration(
                    Web3.to_checksum_address(address)
                ).call(block_identifier=BLOCK)
                data = int(raw[0])
                fee = (data >> 152) & 0xFFFF
                if fee > 10000:
                    raise ValueError(f'Invalid liquidation protocol fee: {fee}')
                fee_bps[address] = fee
                print(f'PROTOCOL FEE [{i}/{len(assets)}]: {address[:10]}... = {fee} bps')
                break
            except HTTPError as exc:
                if (exc.response is None or exc.response.status_code != 429
                        or attempt == 5):
                    raise
                ra = exc.response.headers.get('Retry-After', '')
                delay = max(5 * (2 ** attempt), int(ra) if ra.isdigit() else 0)
                print(f'429 rate limit: retry in {delay}s')
                time.sleep(delay)
    return fee_bps


def close_factor_estimate(hf, total_collateral_usd, total_debt_usd, reserve_debt_usd):
    """Research approximation to Aave V3 close factor rules.

    At HF > .95 and both total collateral and selected reserve debt are
    >=$2000, cap is 50% of TOTAL debt (not 50% of each reserve debt).
    Otherwise we allow up to 100% of the selected reserve debt.
    """
    if (hf > HF_CLOSE_FACTOR_THRESHOLD
            and total_collateral_usd >= SMALL_POSITION_THRESHOLD_USD
            and reserve_debt_usd >= SMALL_POSITION_THRESHOLD_USD):
        return DEFAULT_CLOSE_FACTOR, min(reserve_debt_usd, total_debt_usd * DEFAULT_CLOSE_FACTOR)
    return FULL_CLOSE_FACTOR, reserve_debt_usd


def estimate_pair(scenario, user, hf, category, collateral, debt, total_coll, total_debt,
                  shocks, emode_bonus, collateral_bits, fee_bps):
    cp = D(collateral['baseline_price_usd']) * (1 + shocks.get(collateral['symbol'], D(0)))
    dp = D(debt['baseline_price_usd']) * (1 + shocks.get(debt['symbol'], D(0)))
    coll_qty = D(collateral['collateral_amount'])
    debt_qty = D(debt['debt_amount'])
    coll_usd = cp * coll_qty
    debt_usd = dp * debt_qty
    if coll_usd <= 0 or debt_usd <= 0:
        return None

    bonus = int(collateral['base_bonus_bps'])
    if category > 0 and ((collateral_bits[category] >> int(collateral['reserve_id'])) & 1):
        bonus = emode_bonus[category]
    if bonus < 10000:
        return None  # non-collateralizable reserve
    factor = D(bonus) / D(10000)
    share, max_repay = close_factor_estimate(hf, total_coll, total_debt, debt_usd)
    # Available collateral caps repay to its value divided by bonus.
    repay = min(max_repay, coll_usd / factor)
    if repay <= 0:
        return None
    gross_seized = repay * factor
    bonus_value = gross_seized - repay
    protocol_fee = bonus_value * D(fee_bps) / D(10000)
    liquidator_value = gross_seized - protocol_fee
    gross_profit = liquidator_value - repay
    # USD-denominated estimates; no ERC20 rounding or transaction execution.
    return {
        'scenario': scenario, 'snapshot_block': BLOCK, 'borrower_address': user,
        'modeled_hf': str(hf), 'emode_category': category,
        'collateral_symbol': collateral['symbol'], 'collateral_address': collateral['asset_address'],
        'debt_symbol': debt['symbol'], 'debt_address': debt['asset_address'],
        'collateral_price_after_shock_usd': str(cp), 'debt_price_after_shock_usd': str(dp),
        'collateral_available_usd': str(coll_usd), 'reserve_debt_usd': str(debt_usd),
        'total_modeled_debt_usd': str(total_debt),
        'close_factor_approx': str(share),
        'liquidation_bonus_bps': bonus, 'protocol_fee_bps_of_bonus': fee_bps,
        'estimated_debt_repaid_usd': str(repay),
        'estimated_collateral_seized_before_protocol_fee_usd': str(gross_seized),
        'estimated_protocol_fee_usd': str(protocol_fee),
        'estimated_collateral_received_by_liquidator_usd': str(liquidator_value),
        'estimated_gross_profit_before_gas_swap_slippage_usd': str(gross_profit),
        'collateral_limited': str(repay < max_repay),
    }


def main():
    cands = load_csv(CANDIDATES, [
        'scenario','snapshot_block','borrower_address','modeled_hf','modeled_debt_usd',
        'emode_category','reserve_id','asset_address','symbol','collateral_enabled',
        'collateral_amount','debt_amount','baseline_price_usd','base_bonus_bps'])
    stress = load_csv(STRESS_SUMMARY, ['scenario','snapshot_block','price_shocks'])
    reserves = load_csv(RESERVES, ['snapshot_block','asset_address','symbol'])
    emodes = load_csv(EMODE_CATEGORIES, ['snapshot_block','emode_category','liquidation_bonus_bps'])
    mappings = load_csv(EMODE_MAPPING, ['snapshot_block','emode_category','collateral_bitmap'])

    scenarios = {r['scenario']: parse_shocks(r['price_shocks']) for r in stress}
    if len(scenarios) != len(stress):
        raise ValueError('Duplicate stress scenario')
    reserve_names = {r['asset_address'].lower(): r['symbol'] for r in reserves}
    if len(reserve_names) != len(reserves):
        raise ValueError('Duplicate reserve')
    emode_bonus = {int(r['emode_category']): int(r['liquidation_bonus_bps']) for r in emodes}
    collateral_bits = {int(r['emode_category']): int(r['collateral_bitmap']) for r in mappings}

    grouped = defaultdict(list)
    for r in cands:
        if r['scenario'] not in scenarios:
            raise ValueError(f'Unknown scenario: {r["scenario"]}')
        if D(r['modeled_hf']) >= 1 or D(r['modeled_debt_usd']) <= 0:
            raise ValueError('Candidate not eligible')
        address = r['asset_address'].lower()
        if reserve_names.get(address) != r['symbol']:
            raise ValueError(f'Unmatched token {address}')
        grouped[(r['scenario'], r['borrower_address'].lower())].append(r)

    collateral_assets = set()
    for rows in grouped.values():
        for r in rows:
            if true_flag(r['collateral_enabled']) and D(r['collateral_amount']) > 0:
                collateral_assets.add(r['asset_address'].lower())
    print('=== HISTORICAL LIQUIDATION PROTOCOL FEES ===')
    fees = fetch_protocol_fees(collateral_assets)

    outputs = []
    stats = defaultdict(lambda: {'borrowers': set(), 'pairs': 0})
    for (scenario, user), rows in sorted(grouped.items()):
        hf = D(rows[0]['modeled_hf'])
        category = int(rows[0]['emode_category'])
        reported_total_debt = D(rows[0]['modeled_debt_usd'])
        if category and (category not in emode_bonus or category not in collateral_bits):
            raise ValueError(f'Missing E-mode data for category {category}')
        if any(r['modeled_hf'] != rows[0]['modeled_hf'] or
               r['modeled_debt_usd'] != rows[0]['modeled_debt_usd'] or
               int(r['emode_category']) != category for r in rows):
            raise ValueError(f'Inconsistent candidate data for {scenario}, {user}')
        shock = scenarios[scenario]
        total_coll = sum((D(r['collateral_amount']) * D(r['baseline_price_usd']) *
                         (1 + shock.get(r['symbol'], D(0)))
                          for r in rows if true_flag(r['collateral_enabled'])), D(0))
        total_debt = sum((D(r['debt_amount']) * D(r['baseline_price_usd']) *
                         (1 + shock.get(r['symbol'], D(0))) for r in rows), D(0))
        if abs(total_debt - reported_total_debt) > D('0.001'):
            raise ValueError(f'Modeled debt mismatch for {scenario} {user}: {total_debt} vs {reported_total_debt}')
        collateral = [r for r in rows if true_flag(r['collateral_enabled']) and D(r['collateral_amount']) > 0]
        debts = [r for r in rows if D(r['debt_amount']) > 0]
        for c in collateral:
            for debt in debts:
                record = estimate_pair(scenario, user, hf, category, c, debt,
                                       total_coll, total_debt, shock,
                                       emode_bonus, collateral_bits,
                                       fees[c['asset_address'].lower()])
                if record:
                    outputs.append(record)
                    stats[scenario]['borrowers'].add(user)
                    stats[scenario]['pairs'] += 1

    if not outputs:
        raise RuntimeError('No pair estimates. Check input data.')
    write_csv(OUTPUT, outputs, list(outputs[0]))

    summary = []
    for scenario in scenarios:
        pairs = [r for r in outputs if r['scenario'] == scenario]
        # Choose only ONE best hypothetical pair per borrower for comparison;
        # do not sum mutually exclusive alternatives.
        best_by_user = {}
        for r in pairs:
            user = r['borrower_address'].lower()
            best = best_by_user.get(user)
            if best is None or D(r['estimated_gross_profit_before_gas_swap_slippage_usd']) > D(best['estimated_gross_profit_before_gas_swap_slippage_usd']):
                best_by_user[user] = r
        selected = list(best_by_user.values())
        total_best = sum((D(r['estimated_gross_profit_before_gas_swap_slippage_usd']) for r in selected), D(0))
        summary.append({
            'scenario': scenario,
            'snapshot_block': BLOCK,
            'borrowers_with_estimated_pairs': len(best_by_user),
            'alternative_pairs': len(pairs),
            'sum_one_best_pair_gross_profit_usd_non_executable': str(total_best),
        })
        print(f'{scenario}: borrowers={len(best_by_user)}, alternative_pairs={len(pairs)}, '
              f'one_best_pair_gross_profit=${total_best:,.2f}')
    write_csv(SUMMARY, summary, list(summary[0]))
    print('=== LIQUIDATION REWARD ESTIMATION COMPLETE ===')
    print('PAIR ESTIMATES:', len(outputs))
    print('CSV SAVED:', OUTPUT)
    print('SUMMARY SAVED:', SUMMARY)
    print('WARNING: these are ALTERNATIVE off-chain pair estimates, not executed liquidations or net profits.')
    print('WARNING: close-factor and collateral amounts are simplified; dust/rounding rules are not simulated.')


if __name__ == '__main__':
    main()

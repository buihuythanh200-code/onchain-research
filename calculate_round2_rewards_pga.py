"""Round-2 counterfactual Aave V3 liquidation valuation + finite PGA SPNE.

Uses existing round-2 pair inventory (updated balances and prices) and historical
reserve configuration. Does NOT execute transactions or model a second price impact.

Usage:
    python calculate_round2_rewards_pga.py --self-test
    python calculate_round2_rewards_pga.py
    python calculate_round2_rewards_pga.py --mode ALL_ELIGIBLE

Notes:
* 'ALL_ELIGIBLE' is the ROUND-1 execution assumption used to construct its
  starting state. The file ALSO calculates a counterfactual round-2 PGA for
  comparison. It does not silently apply a round-2 auction in ALL_ELIGIBLE.
* The fee for each collateral reserve is taken from the previously verified
  historical first-round quotation; missing reserves trigger explicit validation error.
* Calculations use the same approximate Aave V3 close-factor logic as round 1.
"""
import argparse
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

def read_rows(path, required):
    if not path.is_file():
        raise FileNotFoundError(f'Missing CSV: {path}')
    with path.open(newline='', encoding='utf-8-sig') as fh:
        reader = csv.DictReader(fh)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'{path}: missing columns {sorted(missing)}')
        result = list(reader)
    if not result:
        raise ValueError(f'{path}: empty CSV')
    if 'snapshot_block' in result[0] and any(int(r['snapshot_block']) != 26402092 for r in result):
        raise ValueError(f'{path}: snapshot block mismatch')
    return result


def write_csv(path, records, columns):
    with path.open('w', newline='', encoding='utf-8') as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)


def estimate_pair(scenario, user, hf, category, collateral, debt, total_coll,
                  total_debt, shocks, emode_bonus, bits, fee_bps):
    """Same approximate USD-unit valuation and close-factor rule as round 1."""
    cp = D(collateral['baseline_price_usd']) * (1 + shocks.get(collateral['symbol'], D(0)))
    dp = D(debt['baseline_price_usd']) * (1 + shocks.get(debt['symbol'], D(0)))
    coll_usd = cp * D(collateral['collateral_amount'])
    debt_usd = dp * D(debt['debt_amount'])
    if coll_usd <= 0 or debt_usd <= 0:
        return None
    bonus = int(collateral['base_bonus_bps'])
    if category > 0 and (bits[category] >> int(collateral['reserve_id'])) & 1:
        bonus = emode_bonus[category]
    if bonus < 10000:
        return None
    bonus_factor = D(bonus) / D(10000)
    # Reproduce simplified rule used for first-round estimates.
    if hf > D('0.95') and total_coll >= D(2000) and debt_usd >= D(2000):
        share, max_repay = D('0.5'), min(debt_usd, total_debt * D('0.5'))
    else:
        share, max_repay = D(1), debt_usd
    repay = min(max_repay, coll_usd / bonus_factor)
    if repay <= 0:
        return None
    seized = repay * bonus_factor
    fee = (seized - repay) * D(fee_bps) / D(10000)
    liquidator = seized - fee
    return {
        'scenario':scenario, 'snapshot_block':26402092, 'borrower_address':user,
        'modeled_hf':str(hf), 'emode_category':category,
        'collateral_symbol':collateral['symbol'], 'collateral_address':collateral['asset_address'],
        'debt_symbol':debt['symbol'], 'debt_address':debt['asset_address'],
        'collateral_price_after_shock_usd':str(cp), 'debt_price_after_shock_usd':str(dp),
        'collateral_available_usd':str(coll_usd), 'reserve_debt_usd':str(debt_usd),
        'total_modeled_debt_usd':str(total_debt), 'close_factor_approx':str(share),
        'liquidation_bonus_bps':bonus,'protocol_fee_bps_of_bonus':fee_bps,
        'estimated_debt_repaid_usd':str(repay),
        'estimated_collateral_seized_before_protocol_fee_usd':str(seized),
        'estimated_protocol_fee_usd':str(fee),
        'estimated_collateral_received_by_liquidator_usd':str(liquidator),
        'estimated_gross_profit_before_gas_swap_slippage_usd':str(liquidator-repay),
        'collateral_limited':str(repay < max_repay),
    }

D = Decimal
BLOCK = 26402092


def get_historical_fees(root, collateral_addresses):
    """Reuse audited first-round fees and fetch only missing fees at the same block.

    Fetched values are cached for reproducible subsequent runs. We never
    assume a missing fee is zero or copy one token's fee to another.
    """
    old = read_rows(root / 'aave_liquidation_reward_estimates.csv',
                    ['collateral_address', 'protocol_fee_bps_of_bonus', 'snapshot_block'])
    fees = {}

    def insert(address, value, source):
        address = address.lower()
        value = int(value)
        if not (0 <= value <= 10000):
            raise ValueError(f'Invalid historical fee from {source}: {address} {value}')
        if address in fees and fees[address] != value:
            raise ValueError(f'Conflicting historical fee from {source}: {address}')
        fees[address] = value

    for row in old:
        insert(row['collateral_address'], row['protocol_fee_bps_of_bonus'], 'round-1')

    cache = root / 'aave_historical_protocol_fees_round2.csv'
    if cache.exists():
        for row in read_rows(cache, ['snapshot_block', 'asset_address', 'protocol_fee_bps_of_bonus']):
            insert(row['asset_address'], row['protocol_fee_bps_of_bonus'], 'cache')

    missing = set(collateral_addresses) - set(fees)
    if missing:
        print('Missing collateral reserve fees:', ', '.join(sorted(missing)))
        print('Reading Aave Pool reserve configuration at historical block', BLOCK)
        try:
            # Reuse the already tested function: getConfiguration(asset),
            # extracting liquidationProtocolFee from configuration bits 152..167.
            from calculate_liquidation_rewards import BLOCK as FEE_BLOCK, fetch_protocol_fees
            if FEE_BLOCK != BLOCK:
                raise ValueError(f'Historical block mismatch: {FEE_BLOCK} != {BLOCK}')
            fetched = fetch_protocol_fees(missing)
        except Exception as exc:
            raise RuntimeError(
                'Could not verify missing fees at the snapshot. Check archive RPC '
                'availability and retry. No fallback fee has been assumed.'
            ) from exc
        if set(fetched) != missing:
            raise ValueError(f'Incomplete fetched fee coverage: {missing - set(fetched)}')
        for address, value in fetched.items():
            insert(address, value, 'historical RPC')
        write_csv(cache,
                  [{'snapshot_block': BLOCK, 'asset_address': address,
                    'protocol_fee_bps_of_bonus': fees[address]}
                   for address in sorted(set(collateral_addresses))],
                  ['snapshot_block', 'asset_address', 'protocol_fee_bps_of_bonus'])
        print('VERIFIED FEE CACHE:', cache.name)
    return {addr: fees[addr] for addr in collateral_addresses}


def calculate(root, mode, players):
    suffix = f'{mode.lower()}_n{players}'
    inventory = read_rows(root / f'aave_round2_pair_inventory_{suffix}.csv',
        ['scenario','snapshot_block','borrower_address','hf_at_round2_start',
         'emode_category','collateral_address','collateral_symbol','collateral_reserve_id',
         'collateral_amount_remaining','collateral_price_round2_usd',
         'debt_address','debt_symbol','debt_amount_remaining','debt_price_round2_usd'])
    states = read_rows(root / f'aave_cascade_round2_state_{suffix}.csv',
        ['scenario','snapshot_block','borrower_address','status',
         'collateral_usd_after','debt_usd_after','hf_after_round1_and_impact'])
    reserves = read_rows(root / 'aave_reserves.csv',
                         ['snapshot_block','asset_address','liquidation_bonus_bps'])
    emodes = read_rows(root / 'aave_emode_categories.csv',
                       ['snapshot_block','emode_category','liquidation_bonus_bps'])
    mapping = read_rows(root / 'aave_emode_asset_mapping_resolved.csv',
                        ['snapshot_block','emode_category','collateral_bitmap'])

    state_map = {}
    for r in states:
        key = (r['scenario'], r['borrower_address'].lower())
        if key in state_map:
            raise ValueError(f'Duplicate state: {key}')
        state_map[key] = r
    reserve_bonus = {r['asset_address'].lower(): int(r['liquidation_bonus_bps']) for r in reserves}
    if len(reserve_bonus) != len(reserves):
        raise ValueError('Duplicate reserves')
    emode_bonus = {int(r['emode_category']): int(r['liquidation_bonus_bps']) for r in emodes}
    bits = {int(r['emode_category']): int(r['collateral_bitmap']) for r in mapping}
    collateral_addresses = {r['collateral_address'].lower() for r in inventory}
    fees = get_historical_fees(root, collateral_addresses)
    print(f'Historical fee coverage: {len(fees)}/{len(collateral_addresses)} collateral reserves')

    grouped = defaultdict(list)
    seen = set()
    for r in inventory:
        key = (r['scenario'], r['borrower_address'].lower())
        pair_key = (*key, r['collateral_address'].lower(), r['debt_address'].lower())
        if pair_key in seen:
            raise ValueError(f'Duplicate round-2 pair {pair_key}')
        seen.add(pair_key)
        grouped[key].append(r)

    quoted = []
    for key, records in sorted(grouped.items()):
        state = state_map.get(key)
        if state is None or state['status'] != 'ELIGIBLE_ROUND2':
            raise ValueError(f'Not eligible at round 2: {key}')
        hf = D(state['hf_after_round1_and_impact'])
        total_coll = D(state['collateral_usd_after'])
        total_debt = D(state['debt_usd_after'])
        if not (D(0) <= hf < D(1) and total_coll > 0 and total_debt > 0):
            raise ValueError(f'Invalid round-2 account state: {key}')
        for r in records:
            if abs(D(r['hf_at_round2_start']) - hf) > D('0.00001'):
                raise ValueError(f'Inventory HF mismatch: {key}')
            category = int(r['emode_category'])
            addr = r['collateral_address'].lower()
            if addr not in reserve_bonus:
                raise ValueError(f'Missing reserve bonus for {addr}')
            if category > 0 and (category not in emode_bonus or category not in bits):
                raise ValueError(f'Missing E-mode data for {category}')
            collateral = {
                'symbol': r['collateral_symbol'], 'asset_address': r['collateral_address'],
                'reserve_id': r['collateral_reserve_id'],
                'collateral_amount': r['collateral_amount_remaining'],
                'baseline_price_usd': r['collateral_price_round2_usd'],
                'base_bonus_bps': reserve_bonus[addr],
            }
            debt = {
                'symbol': r['debt_symbol'], 'asset_address': r['debt_address'],
                'debt_amount': r['debt_amount_remaining'],
                'baseline_price_usd': r['debt_price_round2_usd'],
            }
            item = estimate_pair(key[0], key[1], hf, category, collateral, debt,
                total_coll, total_debt, {}, emode_bonus, bits, fees[addr])
            if item is None:
                raise ValueError(f'Nonpositive round-2 pair estimate: {pair_key}')
            item['round'] = 2
            item['first_round_execution_mode'] = mode
            item['valuation_note'] = 'Round-2 approximate quote; prices and balances updated, not executable'
            quoted.append(item)

    if len(quoted) != len(inventory):
        raise ValueError('Round-2 inventory/valuation row count mismatch')
    # Select ONE alternative for each borrower; same deterministic ordering as Module 1.
    selected_map = defaultdict(list)
    for r in quoted:
        selected_map[(r['scenario'], r['borrower_address'])].append(r)
    selected = []
    for key, rows in sorted(selected_map.items()):
        rows.sort(key=lambda r: (-D(r['estimated_gross_profit_before_gas_swap_slippage_usd']),
                                    r['collateral_address'].lower(),r['debt_address'].lower()))
        selected.append(rows[0])

    # The existing game model uses the SAME cost calibration as in round 1.
    # This returns only counterfactual winners: no round-2 liquidation is executed.
    # Lazy import: user already has the original game solver in their repository.
    from integrate_pga_spne import solve_pairs
    games = solve_pairs(selected, players)
    for r in games:
        r['round'] = 2
        r['first_round_execution_mode'] = mode
        r['model_note'] = 'Counterfactual round-2 PGA only; no liquidation or new price impact'

    per_scenario = defaultdict(lambda: {'pairs': 0, 'users': 0, 'wins': 0, 'gross': D(0)})
    for r in quoted:
        per_scenario[r['scenario']]['pairs'] += 1
    for r in selected:
        per_scenario[r['scenario']]['users'] += 1
        per_scenario[r['scenario']]['gross'] += D(r['estimated_gross_profit_before_gas_swap_slippage_usd'])
    for r in games:
        per_scenario[r['scenario']]['wins'] += r['winner'] != 'NONE'
    summary = []
    for scenario, v in sorted(per_scenario.items()):
        summary.append({
            'scenario': scenario, 'snapshot_block': BLOCK,
            'first_round_execution_mode': mode, 'num_liquidators': players,
            'alternative_pairs_round2': v['pairs'],
            'one_selected_pair_per_borrower': v['users'],
            'round2_counterfactual_pga_winners': v['wins'],
            'round2_no_winner': v['users']-v['wins'],
            'sum_selected_gross_reward_usd_non_executable': str(v['gross']),
        })
        print(f"{scenario}: pairs={v['pairs']}, users={v['users']}, "
              f"pga_winner={v['wins']}/{v['users']}, "
              f"best_pair_gross_reward=${v['gross']:,.2f}")

    outputs = [
        (root / f'aave_round2_reward_estimates_{suffix}.csv', quoted),
        (root / f'aave_round2_pga_spne_results_{suffix}.csv', games),
        (root / f'aave_round2_pga_spne_summary_{suffix}.csv', summary),
    ]
    for path, rows in outputs:
        write_csv(path, rows, list(rows[0]))
        print('CSV:', path.name)
    print('=== ROUND-2 REWARD AND PGA ESTIMATION COMPLETE ===')
    print('WARNING: No round-2 liquidation executed and no additional price impact applied.')
    print('WARNING: These are simplified Aave quotes; dust, gas, slippage and real PGA not validated.')


def self_test():
    c = {'symbol':'WETH', 'asset_address':'0x123', 'reserve_id':0,
         'collateral_amount':'10','baseline_price_usd':'100','base_bonus_bps':10500}
    d = {'symbol':'DAI','asset_address':'0x456','debt_amount':'500',
         'baseline_price_usd':'1'}
    r = estimate_pair('TEST','0xuser',D('0.94'),0,c,d,D(1000),D(500),{},
                      {},{},2000)
    assert r is not None and D(r['estimated_debt_repaid_usd']) == D(500)
    assert D(r['estimated_gross_profit_before_gas_swap_slippage_usd']) == D(20)
    x = dict(r)
    x['estimated_gross_profit_before_gas_swap_slippage_usd'] = '5'
    assert D(max([r,x], key=lambda q: D(q['estimated_gross_profit_before_gas_swap_slippage_usd']))['estimated_gross_profit_before_gas_swap_slippage_usd']) == D(20)
    print('SELF-TEST PASS: quote economics and deterministic pair selection')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', type=Path, default=Path('.'))
    p.add_argument('--mode', choices=('PGA_SPNE','ALL_ELIGIBLE'), default='PGA_SPNE')
    p.add_argument('--players', type=int, choices=(3,5), default=3)
    p.add_argument('--self-test', action='store_true')
    args = p.parse_args()
    if args.self_test:
        self_test()
    else:
        calculate(args.directory, args.mode, args.players)

if __name__ == '__main__':
    main()

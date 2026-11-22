"""Module 2.4.3: illustrative liquidation opportunity -> sequential PGA SPNE.

Input: aave_liquidation_reward_estimates.csv from historical Aave V3
positions under hypothetical price shocks. No RPC calls are required here.

Select ONE estimated collateral/debt pair per (scenario, borrower), since
pairs for the same borrower are ALTERNATIVES. Select by largest estimated
pre-gas gross profit, using deterministic tie-breaking. This is NOT an
executable optimization across all liquidations or a validated winning pair.

The PGA solver is the previously developed finite sequential game with
perfect information; fee grid, player ordering and loser-pays-zero are
simplifications. No transaction execution or price-impact feedback occurs.
"""
import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from multi_liquidator_pga import D, Liquidator, PGAConfig, solve_spne

BLOCK = 26402092
INPUT_FILE = Path('aave_liquidation_reward_estimates.csv')
OUTPUT_FILE = Path('aave_pga_spne_results.csv')
SUMMARY_FILE = Path('aave_pga_spne_summary.csv')

# Historical transaction example used for *illustrative* fee calibration;
# it is not a measured fee quote from the selected liquidators.
BASE_FEE_GWEI = D('0.043994217')
ETH_PRICE_USD = D('2016.53359576')
PRIORITY_BIDS_GWEI = tuple(D(x) for x in ('0', '0.01', '0.02', '0.05', '0.10', '0.20'))
LIQUIDATORS = (
    Liquidator('A', 480000, D('10')),
    Liquidator('B', 350000, D('5')),
    Liquidator('C', 420000, D('8')),
    Liquidator('D', 390000, D('6')),
    Liquidator('E', 410000, D('7')),
)

REQUIRED = {
    'scenario', 'snapshot_block', 'borrower_address', 'modeled_hf',
    'collateral_symbol', 'collateral_address', 'debt_symbol', 'debt_address',
    'estimated_debt_repaid_usd',
    'estimated_gross_profit_before_gas_swap_slippage_usd',
}


def load_pairs(path):
    with path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f'Missing CSV columns: {sorted(missing)}')
        pairs = list(reader)
    if not pairs:
        raise ValueError('No pair estimates found')
    known_keys = set()
    for row in pairs:
        if int(row['snapshot_block']) != BLOCK:
            raise ValueError('Snapshot block mismatch')
        row['borrower_address'] = row['borrower_address'].lower()
        row['collateral_address'] = row['collateral_address'].lower()
        row['debt_address'] = row['debt_address'].lower()
        try:
            hf = Decimal(row['modeled_hf'])
            reward = Decimal(row['estimated_gross_profit_before_gas_swap_slippage_usd'])
            repay = Decimal(row['estimated_debt_repaid_usd'])
        except (InvalidOperation, KeyError) as exc:
            raise ValueError(f'Invalid numeric data for {row["borrower_address"]}') from exc
        if any(not value.is_finite() for value in (hf, reward, repay)):
            raise ValueError('Non-finite input')
        if hf < 0 or hf >= 1:
            raise ValueError(f'Unexpected non-liquidatable HF: {hf}')
        if reward < 0 or repay < 0:
            raise ValueError('Negative reward or repayment')
        key = (row['scenario'], row['borrower_address'], row['collateral_address'], row['debt_address'])
        if key in known_keys:
            raise ValueError(f'Duplicate liquidation pair: {key}')
        known_keys.add(key)
    return pairs


def pick_one_pair_per_borrower(pairs):
    grouped = defaultdict(list)
    for row in pairs:
        grouped[(row['scenario'], row['borrower_address'])].append(row)
    chosen = []
    for group_key in sorted(grouped):
        variants = grouped[group_key]
        variants.sort(key=lambda row: (
            -Decimal(row['estimated_gross_profit_before_gas_swap_slippage_usd']),
            row['collateral_address'], row['debt_address']
        ))
        chosen.append(variants[0])
    return chosen


def solve_pairs(selected, num_players):
    if num_players < 2 or num_players > len(LIQUIDATORS):
        raise ValueError('num_players must be between 2 and 5')
    players = LIQUIDATORS[:num_players]
    results = []
    for pair in selected:
        reward = Decimal(pair['estimated_gross_profit_before_gas_swap_slippage_usd'])
        cfg = PGAConfig(
            health_factor=Decimal(pair['modeled_hf']),
            reward_usd=reward,
            base_fee_gwei=BASE_FEE_GWEI,
            eth_price_usd=ETH_PRICE_USD,
            priority_bids_gwei=PRIORITY_BIDS_GWEI,
            liquidators=players,
            max_turns=2 * num_players,
        )
        game = solve_spne(cfg)
        winner_id = game['winner']
        winner_name = players[winner_id].name if winner_id is not None else 'NONE'
        profit = game['payoffs'][winner_id] if winner_id is not None else D(0)
        winning_bid = game['winning_bid']
        results.append({
            'scenario': pair['scenario'],
            'snapshot_block': BLOCK,
            'borrower_address': pair['borrower_address'],
            'modeled_hf': pair['modeled_hf'],
            'collateral_symbol': pair['collateral_symbol'],
            'debt_symbol': pair['debt_symbol'],
            'estimated_debt_repaid_usd': pair['estimated_debt_repaid_usd'],
            'estimated_gross_reward_usd': str(reward),
            'num_liquidators': num_players,
            'winner': winner_name,
            'winning_priority_fee_gwei': str(winning_bid) if winning_bid is not None else '',
            'winner_model_profit_usd': str(profit),
            'path': ' > '.join(
                f'{players[player_id].name}:{"PASS" if bid is None else str(bid)}'
                for _, player_id, bid in game['path']
            ),
            'states_evaluated': game['states_evaluated'],
            'model_note': 'Counterfactual finite PGA, one alternative pair per borrower, not executed',
        })
    return results


def write_csv(path, rows):
    if not rows:
        raise ValueError('Cannot save empty result CSV')
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row['scenario'], row['num_liquidators'])].append(row)
    output = []
    for (scenario, n), batch in sorted(groups.items()):
        executed = [r for r in batch if r['winner'] != 'NONE']
        output.append({
            'scenario': scenario,
            'num_liquidators': n,
            'modeled_opportunities': len(batch),
            'pga_winner_selected_count': len(executed),
            'pga_no_winner_count': len(batch) - len(executed),
            'sum_selected_gross_reward_usd_non_executable': str(sum(
                (Decimal(r['estimated_gross_reward_usd']) for r in batch), D(0)
            )),
            'sum_winner_model_profit_usd_non_executable': str(sum(
                (Decimal(r['winner_model_profit_usd']) for r in executed), D(0)
            )),
            'winner_distribution': '; '.join(
                f'{name}:{count}' for name, count in sorted(Counter(r['winner'] for r in batch).items())
            ),
        })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=INPUT_FILE)
    parser.add_argument('--output', type=Path, default=OUTPUT_FILE)
    parser.add_argument('--summary', type=Path, default=SUMMARY_FILE)
    args = parser.parse_args()
    pairs = load_pairs(args.input)
    selected = pick_one_pair_per_borrower(pairs)
    print('=== AAVE V3 -> MULTI-LIQUIDATOR PGA SPNE ===')
    print('SNAPSHOT BLOCK:', BLOCK)
    print('ALTERNATIVE PAIRS:', len(pairs))
    print('ONE PAIR PER (SCENARIO, BORROWER):', len(selected))
    all_results = []
    for n in (3, 5):
        rows = solve_pairs(selected, n)
        all_results.extend(rows)
        print(f'PGA WITH {n} LIQUIDATORS: {len(rows)} modeled opportunities')
    summary = summarize(all_results)
    write_csv(args.output, all_results)
    write_csv(args.summary, summary)
    for row in summary:
        print(f"{row['scenario']} | N={row['num_liquidators']} | "
              f"winner={row['pga_winner_selected_count']}/{row['modeled_opportunities']} | "
              f"no_winner={row['pga_no_winner_count']}")
    print('CSV SAVED:', args.output)
    print('SUMMARY SAVED:', args.summary)
    print('WARNING: no actual PGA observed; losers have zero cost; fee limits and costs are hypothetical.')
    print('WARNING: winner and gross reward are not proof of executable liquidations or realized profits.')


if __name__ == '__main__':
    main()

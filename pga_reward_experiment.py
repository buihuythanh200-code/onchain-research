
import csv

from multi_liquidator_pga import (
    D, Liquidator, PGAConfig, solve_spne
)

players = (
    Liquidator("A", 480000, D("0.01")),
    Liquidator("B", 350000, D("0.005")),
    Liquidator("C", 420000, D("0.008"))
)

rewards = [
    D("0.05"),
    D("0.10"),
    D("0.20"),
    D("0.50"),
    D("1.00")
]

rows = []

print("=== PGA REWARD SENSITIVITY ===")

for reward in rewards:
    config = PGAConfig(
        health_factor=D("0.95"),
        reward_usd=reward,
        base_fee_gwei=D("0.043994217"),
        eth_price_usd=D("2016.53359576"),
        priority_bids_gwei=(
            D("0"),
            D("0.01"),
            D("0.02"),
            D("0.05"),
            D("0.10"),
            D("0.20")
        ),
        liquidators=players,
        max_turns=6
    )

    result = solve_spne(config)
    winner_id = result["winner"]

    if winner_id is None:
        winner = "NONE"
        profit = D(0)
    else:
        winner = players[winner_id].name
        profit = result["payoffs"][winner_id]

    row = {
        "reward_usd": str(reward),
        "winner": winner,
        "winning_bid_gwei": (
            str(result["winning_bid"])
            if result["winning_bid"] is not None
            else "NONE"
        ),
        "winner_profit_usd": str(profit),
        "states_evaluated": result["states_evaluated"],
        "path_length": len(result["path"])
    }

    rows.append(row)

    print(f"\nREWARD: {reward} USD")
    print("WINNER:", winner)
    print("WINNING BID:", result["winning_bid"])
    print("PROFIT:", profit)
    print("STATES:", result["states_evaluated"])

    print("SPNE PATH:")
    for turn, player_id, bid in result["path"]:
        name = players[player_id].name
        action = (
            "PASS"
            if bid is None
            else f"BID {bid} GWEI"
        )
        print(f"  TURN {turn}: {name} -> {action}")

output = "pga_reward_results.csv"

with open(output, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(
        f, fieldnames=rows[0].keys()
    )
    writer.writeheader()
    writer.writerows(rows)

print("\nCSV SAVED:", output)

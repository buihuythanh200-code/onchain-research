
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

D = lambda x: Decimal(str(x))


@dataclass(frozen=True)
class Liquidator:
    name: str
    gas_used: int
    other_cost_usd: Decimal


@dataclass(frozen=True)
class PGAConfig:
    health_factor: Decimal
    reward_usd: Decimal
    base_fee_gwei: Decimal
    eth_price_usd: Decimal
    priority_bids_gwei: tuple
    liquidators: tuple
    max_turns: int = 6


def validate(config):
    if config.health_factor < 0:
        raise ValueError("Invalid Health Factor")

    if config.health_factor >= 1:
        raise ValueError(
            "HF >= 1: liquidation is not eligible"
        )

    if len(config.liquidators) < 2:
        raise ValueError("At least 2 liquidators required")

    if config.max_turns < 1:
        raise ValueError("max_turns must be positive")

    if not config.priority_bids_gwei:
        raise ValueError("Empty priority fee bids")

    numbers = [
        config.health_factor,
        config.reward_usd,
        config.base_fee_gwei,
        config.eth_price_usd,
        *config.priority_bids_gwei
    ]

    for liq in config.liquidators:
        if liq.gas_used <= 0:
            raise ValueError("Invalid gas usage")
        numbers.append(liq.other_cost_usd)

    if any(
        not isinstance(x, Decimal)
        or not x.is_finite()
        or x < 0
        for x in numbers
    ):
        raise ValueError("Invalid numeric parameters")

    if config.eth_price_usd <= 0:
        raise ValueError("ETH price must be positive")


def winning_profit(config, player_id, priority_fee):
    liq = config.liquidators[player_id]

    gas_cost = (
        D(liq.gas_used)
        * (config.base_fee_gwei + priority_fee)
        * D("1e-9")
        * config.eth_price_usd
    )

    return (
        config.reward_usd
        - gas_cost
        - liq.other_cost_usd
    )


def solve_spne(config):
    validate(config)

    players = config.liquidators
    n = len(players)
    bids = tuple(sorted(set(config.priority_bids_gwei)))
    zero = D(0)

    # Store the equilibrium action for each game state.
    policy = {}

    def terminal_payoffs(leader, bid_index):
        result = [zero] * n

        if leader != -1:
            result[leader] = winning_profit(
                config, leader, bids[bid_index]
            )

        return tuple(result)

    @lru_cache(maxsize=None)
    def backward(turn, leader, bid_index, consecutive_passes):

        # A game ends at the turn limit or after
        # N consecutive passes.
        if (
            turn >= config.max_turns
            or consecutive_passes >= n
        ):
            return terminal_payoffs(leader, bid_index), ()

        current_player = turn % n

        # Action = None means pass.
        available = [None]

        # A new bid must be higher than the current bid.
        for i, bid in enumerate(bids):
            if i > bid_index:
                available.append(i)

        candidates = []

        for action in available:

            if action is None:
                next_leader = leader
                next_bid = bid_index
                next_passes = consecutive_passes + 1
            else:
                next_leader = current_player
                next_bid = action
                next_passes = 0

            future_payoffs, future_path = backward(
                turn + 1,
                next_leader,
                next_bid,
                next_passes
            )

            candidates.append(
                (action, future_payoffs, future_path)
            )

        # Backward induction:
        # maximize the current player's terminal payoff.
        best_profit = max(
            item[1][current_player]
            for item in candidates
        )

        best_choices = [
            item for item in candidates
            if item[1][current_player] == best_profit
        ]

        # Tie-breaking:
        # prefer passing, then the lowest valid bid.
        chosen = min(
            best_choices,
            key=lambda item: (
                item[0] is not None,
                item[0] if item[0] is not None else -1
            )
        )

        action, payoffs, future_path = chosen

        policy[
            (turn, leader, bid_index, consecutive_passes)
        ] = action

        move = (
            turn + 1,
            current_player,
            None if action is None else bids[action]
        )

        return payoffs, (move,) + future_path

    payoffs, path = backward(0, -1, -1, 0)

    # Recover the winning bidder on the equilibrium path.
    winner = None
    winning_bid = None

    for _, player_id, bid in path:
        if bid is not None:
            winner = player_id
            winning_bid = bid

    return {
        "winner": winner,
        "winning_bid": winning_bid,
        "payoffs": payoffs,
        "path": path,
        "policy": policy,
        "states_evaluated": backward.cache_info().currsize
    }


if __name__ == "__main__":
    config = PGAConfig(
        health_factor=D("0.95"),
        reward_usd=D("0.2"),
        base_fee_gwei=D("0.043994217"),
        eth_price_usd=D("2016.53359576"),
        priority_bids_gwei=(
            D("0"),
            D("0.01"),
            D("0.02")
        ),
        liquidators=(
            Liquidator("A", 480000, D("0.01")),
            Liquidator("B", 350000, D("0.005")),
            Liquidator("C", 420000, D("0.008"))
        ),
        max_turns=6
    )

    result = solve_spne(config)

    print("=== MULTI-LIQUIDATOR PGA ===")
    print("HEALTH FACTOR:", config.health_factor)
    print("NUMBER OF LIQUIDATORS:",
          len(config.liquidators))
    print("MAX TURNS:", config.max_turns)

    print("\n=== SPNE EQUILIBRIUM PATH ===")

    for turn, player_id, bid in result["path"]:
        name = config.liquidators[player_id].name

        action = (
            "PASS"
            if bid is None
            else f"BID {bid} GWEI"
        )

        print(f"TURN {turn} | {name} | {action}")

    print("\n=== EQUILIBRIUM RESULT ===")

    if result["winner"] is None:
        print("WINNER: NONE")
    else:
        winner = config.liquidators[
            result["winner"]
        ].name

        print("WINNER:", winner)
        print("WINNING BID:", result["winning_bid"])

    for player, profit in zip(
        config.liquidators,
        result["payoffs"]
    ):
        print(
            f"PROFIT {player.name} USD:",
            profit
        )

    print(
        "STATES EVALUATED:",
        result["states_evaluated"]
    )

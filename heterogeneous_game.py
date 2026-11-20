
from dataclasses import dataclass
from decimal import Decimal

D = lambda value: Decimal(str(value))


@dataclass(frozen=True)
class Liquidator:
    name: str
    gas_used: int
    other_cost_usd: Decimal


@dataclass(frozen=True)
class GameConfig:
    reward_usd: Decimal
    base_fee_gwei: Decimal
    eth_price_usd: Decimal
    priority_bids_gwei: tuple[Decimal, ...]
    liquidator_a: Liquidator
    liquidator_b: Liquidator


def validate(config):
    if not config.priority_bids_gwei:
        raise ValueError("Priority bid set cannot be empty")

    values = [
        config.reward_usd,
        config.base_fee_gwei,
        config.eth_price_usd,
        config.liquidator_a.other_cost_usd,
        config.liquidator_b.other_cost_usd,
        *config.priority_bids_gwei
    ]

    if any(not isinstance(v, Decimal) for v in values):
        raise TypeError("Monetary values must use Decimal")

    if any(not v.is_finite() or v < 0 for v in values):
        raise ValueError("Values must be finite and non-negative")

    if config.eth_price_usd == 0:
        raise ValueError("ETH price must be positive")

    if (
        config.liquidator_a.gas_used <= 0
        or config.liquidator_b.gas_used <= 0
    ):
        raise ValueError("Gas used must be positive")


def actions(config):
    return [
        None,
        *sorted(set(config.priority_bids_gwei))
    ]


def gas_cost_usd(liquidator, priority_bid, config):
    if priority_bid is None:
        return D("0")

    total_gwei = config.base_fee_gwei + priority_bid

    return (
        D(liquidator.gas_used)
        * total_gwei
        * D("1e-9")
        * config.eth_price_usd
    )


def winning_profit(liquidator, priority_bid, config):
    return (
        config.reward_usd
        - gas_cost_usd(liquidator, priority_bid, config)
        - liquidator.other_cost_usd
    )


def payoffs(action_a, action_b, config):
    # None means not participating.
    if action_a is None and action_b is None:
        return D("0"), D("0")

    if action_a is None:
        return (
            D("0"),
            winning_profit(
                config.liquidator_b, action_b, config
            )
        )

    # A wins ties by assumption.
    if action_b is None or action_a >= action_b:
        return (
            winning_profit(
                config.liquidator_a, action_a, config
            ),
            D("0")
        )

    return (
        D("0"),
        winning_profit(
            config.liquidator_b, action_b, config
        )
    )


def action_order(action):
    # Prefer abstaining, then lower priority fee.
    return (action is not None, action or D("0"))


def solve_spne(config):
    validate(config)
    available = actions(config)

    # Step 1: B chooses a best response after every A action.
    responses_b = {}

    for action_a in available:
        candidates = [
            (action_b, payoffs(action_a, action_b, config)[1])
            for action_b in available
        ]

        best_profit = max(p for _, p in candidates)

        best_actions = [
            action for action, profit in candidates
            if profit == best_profit
        ]

        responses_b[action_a] = min(
            best_actions,
            key=action_order
        )

    # Step 2: A anticipates B's response.
    outcomes = []

    for action_a in available:
        action_b = responses_b[action_a]

        profit_a, profit_b = payoffs(
            action_a, action_b, config
        )

        outcomes.append({
            "a": action_a,
            "b": action_b,
            "profit_a": profit_a,
            "profit_b": profit_b
        })

    best_profit_a = max(
        result["profit_a"] for result in outcomes
    )

    optimal_outcomes = [
        result for result in outcomes
        if result["profit_a"] == best_profit_a
    ]

    equilibrium = min(
        optimal_outcomes,
        key=lambda result: action_order(result["a"])
    )

    return {
        "equilibrium": equilibrium,
        "responses_b": responses_b,
        "outcomes": outcomes
    }


def display(action):
    return (
        "Khong tham gia"
        if action is None
        else f"{action} gwei"
    )


if __name__ == "__main__":
    config = GameConfig(
        reward_usd=D("0.05"),
        base_fee_gwei=D("0.043994217"),
        eth_price_usd=D("2016.53359576"),
        priority_bids_gwei=(
            D("0"),
            D("0.00015936"),
            D("0.01")
        ),
        liquidator_a=Liquidator(
            name="A",
            gas_used=480000,
            other_cost_usd=D("0.01")
        ),
        liquidator_b=Liquidator(
            name="B",
            gas_used=350000,
            other_cost_usd=D("0.005")
        )
    )

    result = solve_spne(config)

    print("=== HETEROGENEOUS LIQUIDATOR GAME ===")

    for outcome in result["outcomes"]:
        print(
            "A:", display(outcome["a"]),
            "| B:", display(outcome["b"]),
            "| PROFIT A:", round(outcome["profit_a"], 8),
            "| PROFIT B:", round(outcome["profit_b"], 8)
        )

    eq = result["equilibrium"]

    print("\n=== SPNE EQUILIBRIUM ===")
    print("A:", display(eq["a"]))
    print("B:", display(eq["b"]))
    print("PROFIT A:", eq["profit_a"])
    print("PROFIT B:", eq["profit_b"])

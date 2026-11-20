
from dataclasses import dataclass
from decimal import Decimal

D = lambda x: Decimal(str(x))

# None = Khong tham gia
# Decimal = Priority Fee (gwei)


@dataclass(frozen=True)
class PriorityGameConfig:
    reward_usd: Decimal
    other_cost_usd: Decimal
    gas_used: int
    base_fee_gwei: Decimal
    eth_price_usd: Decimal
    priority_bids_gwei: tuple


def validate(config):
    if config.gas_used <= 0:
        raise ValueError("gas_used must be positive")

    values = (
        config.reward_usd,
        config.other_cost_usd,
        config.base_fee_gwei,
        config.eth_price_usd,
    )

    if any(v < 0 or not v.is_finite() for v in values):
        raise ValueError("Invalid economic parameters")

    if not config.priority_bids_gwei:
        raise ValueError("No priority fee bids")

    if any(
        v < 0 or not v.is_finite()
        for v in config.priority_bids_gwei
    ):
        raise ValueError("Invalid priority fee bid")


def actions(config):
    return [
        None,
        *sorted(set(config.priority_bids_gwei))
    ]


def gas_cost_usd(bid, config):
    if bid is None:
        return D(0)

    return (
        D(config.gas_used)
        * (config.base_fee_gwei + bid)
        * D("1e-9")
        * config.eth_price_usd
    )


def payoffs(a, b, config):
    # Two players abstain
    if a is None and b is None:
        return D(0), D(0)

    # B wins when A abstains
    if a is None:
        return (
            D(0),
            config.reward_usd
            - gas_cost_usd(b, config)
            - config.other_cost_usd
        )

    # A wins ties; B wins only with a higher bid
    if b is None or a >= b:
        return (
            config.reward_usd
            - gas_cost_usd(a, config)
            - config.other_cost_usd,
            D(0)
        )

    return (
        D(0),
        config.reward_usd
        - gas_cost_usd(b, config)
        - config.other_cost_usd
    )


def action_order(action):
    # Prefer abstaining in a tie.
    return (action is not None, action or D(0))


def solve_spne(config):
    validate(config)
    available = actions(config)

    # Backward induction: B optimizes at every node.
    responses = {}

    for a in available:
        profits = {
            b: payoffs(a, b, config)[1]
            for b in available
        }

        maximum = max(profits.values())

        best_responses = [
            b for b in available
            if profits[b] == maximum
        ]

        responses[a] = min(
            best_responses,
            key=action_order
        )

    # A anticipates the optimal responses of B.
    outcomes = []

    for a in available:
        b = responses[a]
        profit_a, profit_b = payoffs(a, b, config)

        outcomes.append({
            "a": a,
            "b": b,
            "profit_a": profit_a,
            "profit_b": profit_b
        })

    maximum_a = max(x["profit_a"] for x in outcomes)

    best_a = [
        x for x in outcomes
        if x["profit_a"] == maximum_a
    ]

    equilibrium = min(
        best_a,
        key=lambda x: action_order(x["a"])
    )

    return {
        "equilibrium": equilibrium,
        "responses_b": responses,
        "outcomes": outcomes
    }


def show(action):
    return (
        "Khong tham gia"
        if action is None
        else f"{action} gwei"
    )


if __name__ == "__main__":
    common = dict(
        other_cost_usd=D("0"),
        gas_used=479925,
        base_fee_gwei=D("0.043994217"),
        eth_price_usd=D("2016.53359576"),
        priority_bids_gwei=(
            D("0"),
            D("0.00015936"),
            D("0.01")
        )
    )

    scenarios = {
        "OBSERVED REWARD": D(
            "0.04042429966799773568372944"
        ),
        "HYPOTHETICAL HIGH REWARD": D("0.2")
    }

    for name, reward in scenarios.items():
        config = PriorityGameConfig(
            reward_usd=reward,
            **common
        )

        result = solve_spne(config)
        eq = result["equilibrium"]

        print("\n===", name, "===")
        print("REWARD USD:", config.reward_usd)

        for outcome in result["outcomes"]:
            print(
                "A:", show(outcome["a"]),
                "| B:", show(outcome["b"]),
                "| PROFIT A:", outcome["profit_a"],
                "| PROFIT B:", outcome["profit_b"]
            )

        print("SPNE A:", show(eq["a"]))
        print("SPNE B:", show(eq["b"]))
        print("PROFIT A USD:", eq["profit_a"])
        print("PROFIT B USD:", eq["profit_b"])

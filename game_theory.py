
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GameConfig:
    reward: float = 100.0
    other_cost: float = 10.0
    gas_bids: tuple[float, ...] = (10.0, 30.0, 50.0)


# None nghĩa là không tham gia.
Action = Optional[float]


def get_actions(config: GameConfig) -> list[Action]:
    bids = tuple(sorted(set(config.gas_bids)))

    if config.reward < 0 or config.other_cost < 0:
        raise ValueError("Reward and costs must be non-negative")

    if not bids or any(bid < 0 for bid in bids):
        raise ValueError("Gas bids must be non-negative and non-empty")

    return [None, *bids]


def payoff(
    action_a: Action,
    action_b: Action,
    config: GameConfig
) -> tuple[float, float]:
    """Tính lợi nhuận (A, B).

    Giả định A thắng khi hai người đặt phí bằng nhau.
    Người thua hoặc không tham gia nhận lợi nhuận 0.
    """

    if action_a is None and action_b is None:
        return 0.0, 0.0

    if action_a is None:
        return 0.0, config.reward - action_b - config.other_cost

    if action_b is None or action_a >= action_b:
        return config.reward - action_a - config.other_cost, 0.0

    return 0.0, config.reward - action_b - config.other_cost


def solve_spne(config: GameConfig) -> dict:
    """Giải trò chơi tuần tự bằng Backward Induction."""

    actions = get_actions(config)

    # Giai đoạn 1: B tìm phản ứng tối ưu cho từng lựa chọn A.
    response_b = {}

    for action_a in actions:
        candidates = []

        for action_b in actions:
            profit_a, profit_b = payoff(
                action_a, action_b, config
            )
            candidates.append((action_b, profit_a, profit_b))

        best_profit_b = max(item[2] for item in candidates)

        best_candidates = [
            item for item in candidates
            if abs(item[2] - best_profit_b) < 1e-9
        ]

        # Nếu B có nhiều lựa chọn tối ưu ngang nhau,
        # ưu tiên không tham gia, rồi mức phí thấp nhất.
        best = min(
            best_candidates,
            key=lambda item: (
                item[0] is not None,
                item[0] if item[0] is not None else 0
            )
        )

        response_b[action_a] = best[0]

    # Giai đoạn 2: A dự đoán phản ứng của B
    # và chọn hành động tối ưu.
    outcomes = []

    for action_a in actions:
        action_b = response_b[action_a]
        profit_a, profit_b = payoff(action_a, action_b, config)

        outcomes.append({
            "action_a": action_a,
            "action_b": action_b,
            "profit_a": profit_a,
            "profit_b": profit_b
        })

    best_profit_a = max(item["profit_a"] for item in outcomes)

    optimal_outcomes = [
        item for item in outcomes
        if abs(item["profit_a"] - best_profit_a) < 1e-9
    ]

    # Quy tắc phụ: A ưu tiên mức phí thấp hơn khi
    # có nhiều lựa chọn cho cùng lợi nhuận tối đa.
    equilibrium = min(
        optimal_outcomes,
        key=lambda item: (
            item["action_a"] is not None,
            item["action_a"] if item["action_a"] is not None else 0
        )
    )

    return {
        "equilibrium": equilibrium,
        "responses_b": response_b,
        "outcomes": outcomes
    }


def display_action(action: Action) -> str:
    return "Không tham gia" if action is None else f"{action:g} USD"


if __name__ == "__main__":
    config = GameConfig()
    result = solve_spne(config)

    print("=== BACKWARD INDUCTION ===")

    for outcome in result["outcomes"]:
        print(
            f"A: {display_action(outcome['action_a']):16} | "
            f"B: {display_action(outcome['action_b']):16} | "
            f"Profit A: {outcome['profit_a']:6.1f} | "
            f"Profit B: {outcome['profit_b']:6.1f}"
        )

    eq = result["equilibrium"]

    print("\n=== SPNE EQUILIBRIUM PATH ===")
    print("A chooses:", display_action(eq["action_a"]))
    print("B chooses:", display_action(eq["action_b"]))
    print("Profit A:", eq["profit_a"])
    print("Profit B:", eq["profit_b"])

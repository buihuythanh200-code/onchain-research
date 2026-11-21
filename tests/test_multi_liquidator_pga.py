
import unittest
from dataclasses import replace

from multi_liquidator_pga import (
    D, Liquidator, PGAConfig,
    solve_spne, winning_profit
)


class TestMultiLiquidatorPGA(unittest.TestCase):

    def setUp(self):
        self.config = PGAConfig(
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

    def test_three_liquidators(self):
        result = solve_spne(self.config)

        self.assertEqual(result["winner"], 0)
        self.assertEqual(result["winning_bid"], D("0.02"))
        self.assertEqual(len(result["payoffs"]), 3)

    def test_winner_profit(self):
        result = solve_spne(self.config)

        expected = winning_profit(
            self.config, 0, D("0.02")
        )

        self.assertEqual(result["payoffs"][0], expected)
        self.assertEqual(result["payoffs"][1], D(0))
        self.assertEqual(result["payoffs"][2], D(0))

    def test_low_reward(self):
        config = replace(
            self.config,
            reward_usd=D("0.001")
        )

        result = solve_spne(config)

        self.assertIsNone(result["winner"])
        self.assertEqual(result["payoffs"], (D(0),) * 3)

    def test_health_factor_not_liquidatable(self):
        config = replace(
            self.config,
            health_factor=D("1.05")
        )

        with self.assertRaises(ValueError):
            solve_spne(config)

    def test_five_liquidators(self):
        config = replace(
            self.config,
            liquidators=self.config.liquidators + (
                Liquidator("D", 390000, D("0.006")),
                Liquidator("E", 410000, D("0.007"))
            ),
            max_turns=8
        )

        result = solve_spne(config)

        self.assertEqual(len(result["payoffs"]), 5)
        self.assertGreater(result["states_evaluated"], 0)

    def test_optimality_at_every_state(self):
        config = self.config
        result = solve_spne(config)
        policy = result["policy"]
        bids = tuple(sorted(set(config.priority_bids_gwei)))
        n = len(config.liquidators)

        def continuation(state):
            turn, leader, bid_index, passes = state

            if turn >= config.max_turns or passes >= n:
                values = [D(0)] * n
                if leader != -1:
                    values[leader] = winning_profit(
                        config, leader, bids[bid_index]
                    )
                return tuple(values)

            action = policy[state]
            return outcome_after(state, action)

        def outcome_after(state, action):
            turn, leader, bid_index, passes = state
            player = turn % n

            if action is None:
                next_state = (
                    turn + 1, leader,
                    bid_index, passes + 1
                )
            else:
                next_state = (
                    turn + 1, player,
                    action, 0
                )

            return continuation(next_state)

        for state, chosen in policy.items():
            turn, leader, bid_index, passes = state
            player = turn % n

            legal_actions = [
                None,
                *range(bid_index + 1, len(bids))
            ]

            chosen_value = outcome_after(
                state, chosen
            )[player]

            alternative_values = [
                outcome_after(state, action)[player]
                for action in legal_actions
            ]

            self.assertEqual(
                chosen_value,
                max(alternative_values),
                msg=f"Non-optimal action at state {state}"
            )


if __name__ == "__main__":
    unittest.main()

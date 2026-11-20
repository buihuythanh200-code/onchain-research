
import unittest

from heterogeneous_game import (
    D,
    Liquidator,
    GameConfig,
    payoffs,
    solve_spne,
)


class TestHeterogeneousGame(unittest.TestCase):

    def make_config(
        self,
        gas_a=480000,
        gas_b=350000,
        cost_a="0.01",
        cost_b="0.005",
        reward="0.05",
    ):
        return GameConfig(
            reward_usd=D(reward),
            base_fee_gwei=D("0.043994217"),
            eth_price_usd=D("2016.53359576"),
            priority_bids_gwei=(
                D("0"),
                D("0.00015936"),
                D("0.01"),
            ),
            liquidator_a=Liquidator(
                name="A",
                gas_used=gas_a,
                other_cost_usd=D(cost_a),
            ),
            liquidator_b=Liquidator(
                name="B",
                gas_used=gas_b,
                other_cost_usd=D(cost_b),
            ),
        )

    def test_b_has_cost_advantage(self):
        config = self.make_config()
        eq = solve_spne(config)["equilibrium"]

        self.assertIsNone(eq["a"])
        self.assertEqual(eq["b"], D("0"))
        self.assertGreater(eq["profit_b"], D("0"))

    def test_a_has_cost_advantage(self):
        config = self.make_config(
            gas_a=350000,
            gas_b=480000,
            cost_a="0.005",
            cost_b="0.01",
        )

        eq = solve_spne(config)["equilibrium"]

        self.assertEqual(eq["a"], D("0"))
        self.assertIsNone(eq["b"])
        self.assertGreater(eq["profit_a"], D("0"))

    def test_equal_costs(self):
        config = self.make_config(
            gas_a=350000,
            gas_b=350000,
            cost_a="0.005",
            cost_b="0.005",
        )

        eq = solve_spne(config)["equilibrium"]

        self.assertEqual(eq["a"], D("0.01"))
        self.assertIsNone(eq["b"])
        self.assertGreater(eq["profit_a"], D("0"))

    def test_both_unprofitable(self):
        config = self.make_config(
            reward="0.01"
        )

        eq = solve_spne(config)["equilibrium"]

        self.assertIsNone(eq["a"])
        self.assertIsNone(eq["b"])

    def test_backward_induction_optimality(self):
        config = self.make_config()
        result = solve_spne(config)

        actions = [
            None,
            *config.priority_bids_gwei
        ]

        for a, selected_b in result["responses_b"].items():
            selected_profit = payoffs(
                a, selected_b, config
            )[1]

            maximum_profit = max(
                payoffs(a, b, config)[1]
                for b in actions
            )

            self.assertEqual(
                selected_profit, maximum_profit
            )

        equilibrium_profit = (
            result["equilibrium"]["profit_a"]
        )

        maximum_a = max(
            item["profit_a"]
            for item in result["outcomes"]
        )

        self.assertEqual(
            equilibrium_profit, maximum_a
        )


if __name__ == "__main__":
    unittest.main()


import unittest

from priority_fee_game import (
    D,
    PriorityGameConfig,
    gas_cost_usd,
    payoffs,
    solve_spne
)


class TestPriorityFeeGame(unittest.TestCase):

    def config(self, reward):
        return PriorityGameConfig(
            reward_usd=D(reward),
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

    def test_observed_gas_cost(self):
        config = self.config("0.2")

        self.assertEqual(
            gas_cost_usd(D("0.00015936"), config),
            D("0.04273116448101398538708600")
        )

    def test_unprofitable_case(self):
        config = self.config(
            "0.04042429966799773568372944"
        )

        eq = solve_spne(config)["equilibrium"]

        self.assertIsNone(eq["a"])
        self.assertIsNone(eq["b"])

    def test_profitable_case(self):
        config = self.config("0.2")

        eq = solve_spne(config)["equilibrium"]

        self.assertEqual(eq["a"], D("0.01"))
        self.assertIsNone(eq["b"])
        self.assertGreater(eq["profit_a"], D(0))

    def test_b_optimal_in_every_subgame(self):
        config = self.config("0.2")
        result = solve_spne(config)

        bids = [None, *config.priority_bids_gwei]

        for a, chosen_b in result["responses_b"].items():
            chosen = payoffs(a, chosen_b, config)[1]
            maximum = max(
                payoffs(a, b, config)[1]
                for b in bids
            )
            self.assertEqual(chosen, maximum)

    def test_a_optimal_given_b_responses(self):
        config = self.config("0.2")
        result = solve_spne(config)

        chosen = result["equilibrium"]["profit_a"]
        maximum = max(
            x["profit_a"] for x in result["outcomes"]
        )

        self.assertEqual(chosen, maximum)


if __name__ == "__main__":
    unittest.main()

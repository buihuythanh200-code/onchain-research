
import unittest

from game_theory import GameConfig, payoff, solve_spne


class TestGameTheory(unittest.TestCase):

    def setUp(self):
        self.config = GameConfig()

    def test_winner_payoff(self):
        self.assertEqual(
            payoff(50, 30, self.config),
            (30.0, 0.0) if False else (40.0, 0.0)
        )

    def test_b_wins_with_higher_bid(self):
        self.assertEqual(
            payoff(10, 30, self.config),
            (0.0, 60.0)
        )

    def test_tie_favors_a(self):
        self.assertEqual(
            payoff(30, 30, self.config),
            (60.0, 0.0)
        )

    def test_spne_equilibrium(self):
        result = solve_spne(self.config)
        eq = result["equilibrium"]

        self.assertEqual(eq["action_a"], 50.0)
        self.assertIsNone(eq["action_b"])
        self.assertEqual(eq["profit_a"], 40.0)
        self.assertEqual(eq["profit_b"], 0.0)

    def test_b_responses(self):
        result = solve_spne(self.config)

        expected = {
            None: 10.0,
            10.0: 30.0,
            30.0: 50.0,
            50.0: None
        }

        self.assertEqual(result["responses_b"], expected)

    def test_b_optimal_in_every_subgame(self):
        result = solve_spne(self.config)
        actions = [None, *self.config.gas_bids]

        for a, chosen_b in result["responses_b"].items():
            chosen_profit = payoff(a, chosen_b, self.config)[1]
            best_profit = max(
                payoff(a, b, self.config)[1]
                for b in actions
            )
            self.assertAlmostEqual(
                chosen_profit, best_profit
            )
    
    def test_unprofitable_liquidation(self):
        config = GameConfig(
            reward=5,
            other_cost=10,
            gas_bids=(10, 30, 50)
        )

        result = solve_spne(config)
        eq = result["equilibrium"]

        self.assertIsNone(eq["action_a"])
        self.assertIsNone(eq["action_b"])
        self.assertEqual(eq["profit_a"], 0)
        self.assertEqual(eq["profit_b"], 0)

    def test_lower_reward_changes_equilibrium(self):
        config = GameConfig(
            reward=50,
            other_cost=10,
            gas_bids=(10, 30, 50)
        )

        eq = solve_spne(config)["equilibrium"]

        self.assertEqual(eq["action_a"], 30)
        self.assertEqual(eq["profit_a"], 10)

    def test_equilibrium_is_optimal_for_a(self):
        config = GameConfig()
        result = solve_spne(config)

        selected_profit = result["equilibrium"]["profit_a"]

        for outcome in result["outcomes"]:
            self.assertGreaterEqual(
                selected_profit,
                outcome["profit_a"]
            )



if __name__ == "__main__":
    unittest.main()

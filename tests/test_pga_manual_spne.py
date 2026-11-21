
import unittest

from multi_liquidator_pga import (
    D, Liquidator, PGAConfig, solve_spne
)


class TestManualSPNE(unittest.TestCase):

    def setUp(self):
        # Simplified parameters for independent
        # mathematical verification.
        self.config = PGAConfig(
            health_factor=D("0.95"),
            reward_usd=D("0.10"),
            base_fee_gwei=D("0"),
            eth_price_usd=D("1000000000"),
            priority_bids_gwei=(
                D("0"),
                D("0.05")
            ),
            liquidators=(
                Liquidator("A", 1, D("0.02")),
                Liquidator("B", 1, D("0"))
            ),
            max_turns=2
        )

        self.result = solve_spne(self.config)

    def test_equilibrium_result(self):
        # Manual solution: A bids 0.05 and wins.
        self.assertEqual(self.result["winner"], 0)
        self.assertEqual(
            self.result["winning_bid"], D("0.05")
        )
        self.assertEqual(
            self.result["payoffs"],
            (D("0.03"), D("0"))
        )

    def test_b_response_when_a_passes(self):
        # After A passes, B prefers bidding 0.
        state = (1, -1, -1, 1)
        self.assertEqual(
            self.result["policy"][state], 0
        )

    def test_b_response_when_a_bids_zero(self):
        # B prefers outbidding A with 0.05.
        state = (1, 0, 0, 0)
        self.assertEqual(
            self.result["policy"][state], 1
        )

    def test_b_response_when_a_bids_maximum(self):
        # No legal higher bid exists.
        state = (1, 0, 1, 0)
        self.assertIsNone(
            self.result["policy"][state]
        )


if __name__ == "__main__":
    unittest.main()

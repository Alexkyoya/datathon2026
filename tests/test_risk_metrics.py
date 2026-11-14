"""Run with python -m unittest discover -s tests -p test_risk_metrics.py."""
import importlib
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambda"))
risk = importlib.import_module("lambda_function")


class RiskMetricsTest(unittest.TestCase):
    def setUp(self):
        self.prices = pd.DataFrame(
            {"NVDA": [100, 120, 90, 125], "BOND": [100, 100, 100, 100]},
            index=pd.to_datetime(["2021-12-31", "2022-01-03", "2022-06-01", "2022-12-30"]),
        )

    def run_metrics(self, **event):
        with patch.object(risk, "prices_cad", return_value=self.prices), patch.object(
            risk, "asset_class", side_effect={"NVDA": "equity", "BOND": "fixed_income"}.__getitem__
        ):
            return risk.risk_metrics(event)

    def test_drawdown_and_recovery(self):
        result = self.run_metrics(tickers=["NVDA"])["assets"]["NVDA"]
        self.assertEqual(result["max_drawdown"], -0.25)
        self.assertEqual(result["recovery_date"], "2022-12-30")
        expected_vol = self.prices["NVDA"].pct_change().std() * math.sqrt(252)
        self.assertAlmostEqual(result["vol_annual"], expected_vol, places=4)

    def test_decision_date_limits_data(self):
        result = self.run_metrics(
            tickers=["NVDA"], weights={"NVDA": 1.0}, decision_date="2022-06-01"
        )
        asset = result["assets"]["NVDA"]
        self.assertFalse(asset["recovered"])
        self.assertIsNone(asset["months_peak_to_recovery"])
        self.assertIsNone(result["scenarios"][0]["portfolio_return"])

    def test_scenarios(self):
        result = self.run_metrics(
            weights={"NVDA": 0.25, "BOND": 0.75},
            shocks=[{"name": "News shock", "shock": {"equity": -0.15, "NVDA": -0.30}}],
        )
        replay, rate, news, baseline = result["scenarios"]
        self.assertEqual(replay["portfolio_return"], 0.0625)
        self.assertEqual(rate["portfolio_return"], -0.09)
        self.assertEqual(news["portfolio_return"], -0.075)
        self.assertIsNone(baseline["portfolio_return"])

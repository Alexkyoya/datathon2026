"""Runs the tools on the real starter data, no AWS needed.
Run from the project folder:  SECOND_LOOK_LOCAL=1 DATA_DIR=data python tests/test_tools_local.py
"""
import os
import sys

os.environ.setdefault("SECOND_LOOK_LOCAL", "1")
os.environ.setdefault("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
import lambda_function as L  # noqa: E402


def check(client, ticker, amount, expected):
    r = L.lambda_handler({"action": "rule_check", "client_id": client, "ticker": ticker, "amount_cad": amount})
    assert "error" not in r, r
    status = "OK " if r["verdict"] == expected else "FAIL"
    print(f"{status} {client:7s} buys {ticker:5s} {amount:>9,.0f} CAD -> {r['verdict']:6s} (expected {expected})",
          [f["rule"] for f in r["findings"] if f["level"] != "green"])
    return r["verdict"] == expected


ok = all([
    check("robert", "SPY", 2000, "green"),
    check("mateo", "ESGV", 12000, "yellow"),
    check("robert", "NVDA", 24000, "red"),
    check("mateo", "XOM", 3000, "red"),
    check("amira", "ETH-USD", 3000, "red"),
    check("amira", "QQQ", 5000, "green"),
])

r = L.lambda_handler({"action": "rule_check", "client_id": "robert", "ticker": "NVDA", "amount_cad": 100000})
assert "Insufficient cash" in r.get("error", ""), r
print("OK  robert NVDA 100,000 rejected: only 24,000 CAD cash (purchases are cash-funded)")

r = L.lambda_handler({"action": "risk_metrics", "tickers": ["NVDA"], "client_id": "robert",
                      "ticker": "NVDA", "amount_cad": 24000})
assert "error" not in r, r
print("NVDA max drawdown:", r["assets"]["NVDA"]["max_drawdown"],
      "| Robert after trade, 2022 replay:", r["scenarios"][0]["portfolio_return"])
for a in ["macro_snapshot", "news_check", "get_profile"]:
    out = L.lambda_handler({"action": a, "client_id": "robert", "ticker": "NVDA", "decision_date": "2026-11-12"})
    assert "error" not in out, out
    print("OK ", a)
print("\nALL GOOD" if ok else "\nSOME CASES FAILED")

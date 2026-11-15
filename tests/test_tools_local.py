"""Runs the tools on the real starter data, no AWS needed.

    uv run --with pytest python -m pytest tests          # as tests
    uv run python tests/test_tools_local.py               # as a printed acceptance table

The price CSVs in data/ are not committed (Yahoo terms); these tests skip when they are missing.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SECOND_LOOK_LOCAL"] = "1"
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "lambda"))
import lambda_function as L  # noqa: E402

try:
    import pytest
    pytestmark = pytest.mark.skipif(not (ROOT / "data" / "prices_daily.csv").exists(),
                                    reason="starter CSVs not copied into data/")
except ImportError:  # plain-script run
    pytest = None

# case, client, ticker, amount, expected verdict, rule family that must fire (None = no alert needed)
ACCEPTANCE = [
    ("robert SPY green", "robert", "SPY", 30000, "green", None),
    ("robert NVDA red", "robert", "NVDA", 360000, "red", "single_stock"),
    ("robert XBB.TO red (goal rule)", "robert", "XBB.TO", 340000, "red", "goal"),
    ("mateo ESGV yellow", "mateo", "ESGV", 12000, "yellow", None),
    ("mateo XOM red (ESG exclusion)", "mateo", "XOM", 3000, "red", "esg.excluded"),
    ("mateo XIU.TO yellow (ESG look-through)", "mateo", "XIU.TO", 5000, "yellow", "esg.fund_lookthrough"),
    ("amira QQQ green", "amira", "QQQ", 5000, "green", None),
    ("amira ETH-USD red", "amira", "ETH-USD", 3000, "red", "crypto"),
    ("amira NVDA red", "amira", "NVDA", 5000, "red", "single_stock"),
]


def rule_check(client, ticker, amount):
    r = L.lambda_handler({"action": "rule_check", "client_id": client, "ticker": ticker, "amount_cad": amount})
    assert "error" not in r, r
    return r


def changed_goals(r):
    g = r["goal_check"]
    if not g.get("applied"):
        return "no goals"
    rows = [i for i in g["installments"] if i["coverage_before"] != i["coverage_after"]]
    return "; ".join(f"{i['name']} {i['date'][:7]} {i['coverage_before']:.0%}->{i['coverage_after']:.0%}"
                     for i in rows) or "unchanged (all 100%)"


def test_acceptance_cases():
    for case, client, ticker, amount, expected, family in ACCEPTANCE:
        r = rule_check(client, ticker, amount)
        assert r["verdict"] == expected, (case, r["verdict"], r["findings"])
        if family:
            assert any(f["rule"].startswith(family) and f["level"] == expected for f in r["findings"]), case


def test_goal_rule_is_the_only_red_in_goal_demo():
    r = rule_check("robert", "XBB.TO", 340000)
    reds = {f["rule"].split(".")[0] for f in r["findings"] if f["level"] == "red"}
    assert reds == {"goal"}


def test_only_esg_rules_flag_the_esg_demo():
    r = rule_check("mateo", "XIU.TO", 5000)
    # Mateo's limit is 0% in Energy, so the fund rule and the whole-portfolio rule both fire; nothing else.
    assert r["verdict"] == "yellow"
    assert {f["rule"] for f in r["findings"]} == {"esg.fund_lookthrough.XIU.TO", "esg.portfolio_lookthrough"}


def test_cash_funded_and_outside_universe_rejected():
    r = L.lambda_handler({"action": "rule_check", "client_id": "robert", "ticker": "NVDA", "amount_cad": 400000})
    assert "Insufficient cash" in r.get("error", ""), r
    r = L.lambda_handler({"action": "rule_check", "client_id": "robert", "ticker": "XLK", "amount_cad": 1000})
    assert "not in the asset universe" in r.get("error", ""), r


def test_goal_check_tool():
    g = L.lambda_handler({"action": "goal_check", "client_id": "robert", "ticker": "XBB.TO",
                          "amount_cad": 340000, "side": "buy"})
    assert "error" not in g, g
    first = next(i for i in g["installments"] if i["name"] == "Retirement income")
    assert first["date"] == "2027-07-01" and first["coverage_before"] == 1 and first["coverage_after"] < 1
    assert first["counted_before"] and {"ticker", "counted_cad"} <= set(first["counted_before"][0])
    assert g["findings"] and g["findings"][0]["level"] == "red"


def test_other_tools():
    r = L.lambda_handler({"action": "risk_metrics", "tickers": ["NVDA"], "client_id": "robert",
                          "ticker": "NVDA", "amount_cad": 360000})
    assert "error" not in r, r
    p = L.lambda_handler({"action": "get_profile", "client_id": "mateo"})
    assert p["goals"] and p["income"] and p["esg_preferences"]["excluded_sectors"] == ["Energy"]
    for a in ["macro_snapshot", "news_check"]:
        out = L.lambda_handler({"action": a, "client_id": "robert", "ticker": "NVDA", "decision_date": "2026-11-12"})
        assert "error" not in out, out


def table():
    print(f"\n{'case':42} {'verdict':8} {'rules fired':58} goal coverage before -> after")
    ok = True
    for case, client, ticker, amount, expected, _ in ACCEPTANCE:
        r = rule_check(client, ticker, amount)
        fired = ", ".join(f"{f['rule']}({f['level']})" for f in r["findings"]) or "-"
        good = r["verdict"] == expected
        ok &= good
        print(f"{case:42} {r['verdict'] + ('' if good else ' FAIL'):8} {fired:58} {changed_goals(r)}")
    return ok


if __name__ == "__main__":
    import builtins
    _print = builtins.print
    builtins.print = lambda *a, **k: None if a and str(a[0]).startswith('{"log"') else _print(*a, **k)
    print("\nALL GOOD" if table() else "\nSOME CASES FAILED")

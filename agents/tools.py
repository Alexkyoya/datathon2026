"""Strands tools. Each one calls the tools Lambda (or the same code locally) and records a trace step.

The trace is our explainability evidence: every number the user sees must come from one of these calls.
"""
import json
import os
import sys
import time
import uuid

from strands import tool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

TRACE = []          # list of {step, tool, input, output, seconds}
RUN_ID = uuid.uuid4().hex[:8]


def reset_trace():
    global RUN_ID
    TRACE.clear()
    RUN_ID = uuid.uuid4().hex[:8]


def call_tool(action, **kwargs):
    payload = {"action": action, **kwargs}
    t0 = time.time()
    if config.LOCAL:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda"))
        os.environ.setdefault("DATA_DIR", config.DATA_DIR)
        import lambda_function
        out = lambda_function.lambda_handler(payload, None)
    else:
        import boto3
        resp = boto3.client("lambda", region_name=config.AWS_REGION).invoke(
            FunctionName=config.LAMBDA_NAME, Payload=json.dumps(payload).encode())
        out = json.loads(resp["Payload"].read())
    TRACE.append({"step": len(TRACE) + 1, "tool": action, "input": kwargs, "output": out,
                  "seconds": round(time.time() - t0, 2)})
    return out


# ---------------------------------------------------------------- tools the agents can use
@tool
def get_profile(client_id: str) -> dict:
    """Get a client's risk profile, preferences, holdings and the limits of their profile.

    Args:
        client_id: client identifier, e.g. "robert", "mateo", "amira"
    """
    return call_tool("get_profile", client_id=client_id)


@tool
def risk_metrics(tickers: list[str], client_id: str = "", ticker: str = "", amount_cad: float = 0.0,
                 shocks: list[dict] | None = None) -> dict:
    """Historical risk in CAD (2021-11-12 to 2026-11-12): volatility, max drawdown, recovery time,
    a 2022 replay of the portfolio after the trade, and optional hypothetical shocks.
    Historical numbers illustrate the past. They are not forecasts.

    Args:
        tickers: assets to describe individually, e.g. ["NVDA", "QQQ"]
        client_id: if given, also analyse this client's portfolio
        ticker: the asset being bought (portfolio is analysed after the trade)
        amount_cad: trade size in CAD
        shocks: optional list like [{"name": "AI capex slowdown", "shock": {"NVDA": -0.30}}]
                or [{"name": "rates +2%", "shock": {"fixed_income": -0.10, "equity": -0.15}}]
    """
    kw = {"tickers": tickers}
    if client_id:
        kw.update(client_id=client_id)
    if ticker:
        kw.update(ticker=ticker, amount_cad=amount_cad)
    if shocks:
        kw.update(shocks=shocks)
    return call_tool("risk_metrics", **kw)


@tool
def macro_snapshot(decision_date: str) -> dict:
    """Latest rates, yields, inflation and unemployment as they were known on the decision date
    (Bank of Canada and FRED; monthly series lagged one month).

    Args:
        decision_date: YYYY-MM-DD
    """
    return call_tool("macro_snapshot", decision_date=decision_date)


@tool
def news_check(ticker: str, decision_date: str) -> dict:
    """Official items (filings, company releases, central bank announcements) for a ticker,
    published on or before the decision date, from the saved snapshot.

    Args:
        ticker: e.g. "NVDA"
        decision_date: YYYY-MM-DD
    """
    return call_tool("news_check", ticker=ticker, decision_date=decision_date)

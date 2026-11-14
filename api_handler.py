"""Second Look API (Lambda + Function URL). Called by the dashboard.

POST JSON, one of:
  {"client_id": "robert", "ticker": "NVDA", "amount_cad": 100000}
      -> green / yellow, or red with "needs_reason": true (no agents run yet)
  {"client_id": "robert", "ticker": "NVDA", "amount_cad": 100000, "reason": "..."}
      -> red: full agent explanation (about 1-2 minutes)
  {"action": "get_profile", "client_id": "robert"}            (any tools-Lambda action, for dashboard data)
  {"action": "risk_metrics", "client_id": "robert", "ticker": "NVDA", "amount_cad": 100000, "tickers": ["NVDA"]}

Response: {"result": {...}, "trace": [...]}  -- trace = every tool call (the evidence panel)
"""
import base64
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "agents"))
sys.path.insert(0, HERE)

import second_look as S  # noqa: E402
import tools as T  # noqa: E402

TOOL_ACTIONS = {"get_profile", "risk_metrics", "macro_snapshot", "news_check", "rule_check"}


def _resp(code, body):
    return {"statusCode": code, "headers": {"content-type": "application/json"},
            "body": json.dumps(body, default=str)}


USAGE = {
    "service": "Second Look - pre-trade check for self-directed investors (PolyFinances Datathon 2026)",
    "note": "Simulation only. Not investment advice. Clients and holdings are fictional.",
    "how_to_use": "POST JSON to this URL.",
    "requests": {
        "check_a_trade": {"client_id": "robert", "ticker": "NVDA", "amount_cad": 100000},
        "red_with_reason": {"client_id": "robert", "ticker": "NVDA", "amount_cad": 100000,
                            "reason": "AI demand is exploding and NVIDIA will double next year"},
        "profile": {"action": "get_profile", "client_id": "robert"},
    },
    "flow": ["green: passes without interruption", "yellow: short warning, client confirms",
             "red: returns needs_reason; send the client's reason to get the agents' explanation (1-2 min)"],
    "demo_clients": {"robert": "59, conservative, retiring in 3-5 years",
                     "mateo": "41, balanced, ESG preferences", "amira": "26, growth, long horizon"},
    "response": {"result": "verdict + explanation", "trace": "every tool call, for verification"},
}


def handler(event, context=None):
    try:
        method = (event.get("requestContext", {}).get("http", {}).get("method") or "POST").upper()
        if method == "GET" or not event.get("body"):
            return _resp(200, USAGE)
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode()
        req = json.loads(raw) if isinstance(raw, str) else raw
        if req.get("action"):
            if req["action"] not in TOOL_ACTIONS:
                return _resp(400, {"error": f"unknown action {req['action']}"})
            T.reset_trace()
            a = req.pop("action")
            return _resp(200, {"result": T.call_tool(a, **req), "trace": T.TRACE})
        for k in ("client_id", "ticker", "amount_cad"):
            if k not in req:
                return _resp(400, {"error": f"missing {k}"})
        res = S.run(req["client_id"], req["ticker"], float(req["amount_cad"]),
                    reason=req.get("reason"), interactive=False)
        return _resp(200, {"result": res, "trace": T.TRACE})
    except Exception as e:  # keep the dashboard informed instead of a bare 502
        return _resp(500, {"error": f"{type(e).__name__}: {e}"})

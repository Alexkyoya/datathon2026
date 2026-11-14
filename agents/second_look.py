"""
Second Look - agents + flow.

Flow
  1. rule_check runs first (plain code, no LLM). Green -> pass. Yellow -> short message + confirm.
  2. Red -> ask "Why do you want to buy this?" -> coordinator agent.
  3. Coordinator asks three specialist agents (profile, financial data, news), then writes the
     explanation as JSON for the dashboard. It cannot change the verdict.
  4. Result + full trace saved (S3 on AWS, ./runs locally).

Run
  uv run python agents/second_look.py --client robert --ticker NVDA --amount 100000 --local
  uv run python agents/second_look.py --client robert --ticker NVDA --amount 100000 --reason "AI demand is exploding"
"""
import argparse
import json
import os
import re
import sys

if "--local" in sys.argv:  # must be set before config is imported (works on Windows too)
    os.environ["SECOND_LOOK_LOCAL"] = "1"

from strands import Agent, tool
from strands.models import BedrockModel

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402
import tools as T  # noqa: E402

DECISION_DATE = "2026-11-12"


def model():
    return BedrockModel(model_id=config.MODEL_ID, region_name=config.AWS_REGION, temperature=0.2)


COMMON_RULES = """
Rules for every answer:
- Use only numbers returned by your tools. Never invent or estimate numbers, dates or sources.
- Say where each number comes from (tool + source) and its date or period.
- Historical numbers describe the past. Never present them as forecasts. No price predictions.
- This is a simulation. Never give investment advice or tell the client what they should do.
- If information is missing, say so instead of guessing.
"""

PROFILE_PROMPT = """You are the Profile agent of Second Look, a pre-trade check for self-directed investors.
Use get_profile, then turn the client's profile into plain-language constraints: horizon, loss tolerance,
asset-class ranges, single-stock and crypto limits, exclusions and preferences. Then read the client's stated
reason for the trade and say, in one or two sentences, how that reason relates to their objective and horizon.
Label which limits come from National Bank Financial's model portfolios and which are team assumptions.
Answer in short bullet points.""" + COMMON_RULES

DATA_PROMPT = """You are the Financial Data agent of Second Look.
Use risk_metrics to describe the asset being bought and the client's portfolio after the trade (always pass
client_id, ticker and amount_cad), including the 2022 replay. Run any scenarios you are given as shocks.
Use macro_snapshot to describe the current rate and inflation context in one or two factual sentences.
Compare the portfolio's past drawdown and 2022 replay with the client's loss tolerance and horizon.
Answer in short bullet points with the numbers and their period.""" + COMMON_RULES

NEWS_PROMPT = """You are the News agent of Second Look.
Use news_check for the ticker and the decision date. Treat the client's stated reason as a claim to check.
Split it into parts and label each part as: fact, prediction, opinion or unverified.
- fact = supported by an official item returned by the tool (cite it)
- prediction = a statement about the future (e.g. a price target)
- opinion = a judgement with no checkable content
- unverified = could be checked but no returned item supports it (this does NOT mean false)
If an item is marked PLACEHOLDER, say the evidence is a placeholder and treat the claim as unverified.
Suggest at most two hypothetical shocks worth stress-testing, written as JSON like
{"name": "...", "shock": {"NVDA": -0.30}} or {"name": "...", "shock": {"equity": -0.15}}.
Return JSON only:
{"decision_date": "...", "claims": [{"claim": "...", "type": "...", "evidence": "...",
  "source": {"name": "...", "url": "..."}, "date": "...", "limitations": "..."}],
 "suggested_scenarios": [ ... ]}""" + COMMON_RULES

COORDINATOR_PROMPT = """You are the Coordinator of Second Look. A trade was flagged RED by the rule engine.
The verdict is final: you explain it, you never change it.
Steps:
1. Ask the profile agent about the client and their reason.
2. Ask the news agent to check the client's reason; get its suggested scenarios.
3. Ask the data agent for risk metrics, the 2022 replay, the news agent's scenarios and the macro context.
4. Propose 1 or 2 alternatives that keep the client's intent (smaller position, a diversified fund on the
   same theme, or spreading the purchase). Do not default to bonds or cash. You may call check_alternative
   to run the rule engine on an alternative; only present alternatives that come back green or yellow.
5. Write the final answer.
Return JSON only, in this shape:
{"verdict": "red", "headline": "one plain sentence",
 "reasons": ["short reason with numbers + source", "..."],
 "client_reason_response": "1-2 sentences responding to why they want to buy",
 "risk": {"asset": {...}, "portfolio_after": {...}, "scenarios": [...]},
 "macro_context": "1-2 factual sentences",
 "news_check": [...claims from the news agent...],
 "alternatives": [{"description": "...", "ticker": "...", "amount_cad": 0, "rule_check_verdict": "..."}],
 "assumptions": ["..."], "sources": ["..."],
 "disclaimer": "Simulation only. Not investment advice."}""" + COMMON_RULES


# ---------------------------------------------------------------- agents as tools for the coordinator
def _ask(system_prompt, tools_, question):
    agent = Agent(model=model(), system_prompt=system_prompt, tools=tools_, callback_handler=None)
    return str(agent(question))


@tool
def ask_profile_agent(question: str) -> str:
    """Ask the Profile agent about the client's constraints and their stated reason.

    Args:
        question: include client_id and the client's reason
    """
    return _ask(PROFILE_PROMPT, [T.get_profile], question)


@tool
def ask_data_agent(question: str) -> str:
    """Ask the Financial Data agent for risk metrics, 2022 replay, scenario shocks and macro context.

    Args:
        question: include client_id, ticker, amount_cad, decision date and any scenarios to run
    """
    return _ask(DATA_PROMPT, [T.risk_metrics, T.macro_snapshot], question)


@tool
def ask_news_agent(question: str) -> str:
    """Ask the News agent to check the client's reason against official sources.

    Args:
        question: include ticker, decision date and the client's reason
    """
    return _ask(NEWS_PROMPT, [T.news_check], question)


@tool
def check_alternative(client_id: str, ticker: str, amount_cad: float) -> dict:
    """Run the rule engine on an alternative trade.

    Args:
        client_id: the client
        ticker: alternative asset
        amount_cad: alternative amount in CAD
    """
    return T.call_tool("rule_check", client_id=client_id, ticker=ticker, amount_cad=amount_cad,
                       decision_date=DECISION_DATE)


# ---------------------------------------------------------------- flow
def _parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else {"raw": text}
    except json.JSONDecodeError:
        return {"raw": text}


def save_run(result):
    record = {"run_id": T.RUN_ID, "result": result, "trace": T.TRACE}
    body = json.dumps(record, indent=2, default=str)
    if config.LOCAL:
        os.makedirs("runs", exist_ok=True)
        path = os.path.join("runs", f"{T.RUN_ID}.json")
        with open(path, "w") as f:
            f.write(body)
        return path
    import boto3
    key = f"{config.RUNS_PREFIX}{T.RUN_ID}.json"
    boto3.client("s3", region_name=config.AWS_REGION).put_object(
        Bucket=config.DATA_BUCKET, Key=key, Body=body.encode(), ContentType="application/json")
    return f"s3://{config.DATA_BUCKET}/{key}"


def run(client_id, ticker, amount_cad, reason=None, interactive=True):
    T.reset_trace()
    rc = T.call_tool("rule_check", client_id=client_id, ticker=ticker, amount_cad=amount_cad,
                     decision_date=DECISION_DATE)
    if "error" in rc:
        return {"stage": "rule_check", "error": rc["error"]}

    if rc["verdict"] == "green":
        result = {"verdict": "green", "message": "Checked. Fits your profile.", "rule_check": rc}
    elif rc["verdict"] == "yellow":
        msgs = [f"{f['rule']}: {f['before']:.0%} -> {f['after']:.0%} (limit {f.get('limit_low', 0):.0%}-{f['limit_high']:.0%})"
                for f in rc["findings"] if f["level"] == "yellow"]
        result = {"verdict": "yellow", "message": "Heads-up before you confirm: " + "; ".join(msgs),
                  "needs_confirmation": True, "rule_check": rc}
    else:
        if reason is None and interactive:
            reason = input("This trade conflicts with your profile. Why do you want to buy it? > ")
        reason = reason or "(no reason given)"
        coordinator = Agent(model=model(), system_prompt=COORDINATOR_PROMPT,
                            tools=[ask_profile_agent, ask_news_agent, ask_data_agent, check_alternative])
        prompt = (f"Client: {client_id}. Proposed trade: buy {ticker} for {amount_cad} CAD. "
                  f"Decision date: {DECISION_DATE}.\nClient's reason: {reason}\n"
                  f"Rule engine result (final verdict):\n{json.dumps(rc)}")
        out = _parse_json(str(coordinator(prompt)))
        out["verdict"] = "red"  # the rule engine decides, never the LLM
        out["rule_check"] = rc
        out["client_reason"] = reason
        result = out

    result["saved_to"] = save_run(result)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", required=True)
    ap.add_argument("--ticker", required=True)
    ap.add_argument("--amount", type=float, required=True)
    ap.add_argument("--reason", default=None)
    ap.add_argument("--local", action="store_true", help="run tools on local data instead of Lambda/S3")
    a = ap.parse_args()
    res = run(a.client, a.ticker, a.amount, a.reason)
    print("\n" + json.dumps(res, indent=2, default=str))

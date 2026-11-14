"""Time a small tool-calling task on each candidate model in THIS AWS account.

  uv run python scripts/bench_models.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402
from strands import Agent, tool  # noqa: E402
from strands.models import BedrockModel  # noqa: E402

CANDIDATES = [m.strip() for m in config.MODEL_ID_FAST.split(",")] + [
    "us.amazon.nova-micro-v1:0", "us.amazon.nova-pro-v1:0", config.MODEL_ID]


@tool
def get_weight(ticker: str) -> dict:
    """Return the client's current portfolio weight for a ticker."""
    return {"ticker": ticker, "weight": 0.04, "source": "profiles.json"}


for mid in dict.fromkeys(CANDIDATES):
    try:
        agent = Agent(model=BedrockModel(model_id=mid, region_name=config.AWS_REGION),
                      tools=[get_weight], callback_handler=None,
                      system_prompt="Use the tool, then answer in one sentence with the number and its source.")
        t = time.time()
        out = str(agent("What is the client's NVDA weight?")).strip().replace("\n", " ")
        print(f"OK   {time.time() - t:5.1f}s  {mid}\n       {out[:120]}")
    except Exception as e:
        print(f"FAIL        {mid}  ({type(e).__name__}: {str(e)[:90]})")

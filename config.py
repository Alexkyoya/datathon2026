"""Shared settings. Edit these once, everything else reads from here."""
import os

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# S3 bucket that holds the data (bucket names are global, so add your name or a random suffix)
DATA_BUCKET = os.getenv("DATA_BUCKET", "second-look-data-alexzhao")
DATA_PREFIX = os.getenv("DATA_PREFIX", "data/")   # where the CSV/JSON files live inside the bucket
RUNS_PREFIX = "runs/"                              # where each run's trace + result is saved

# Name of the Lambda function that holds all the tools
LAMBDA_NAME = os.getenv("LAMBDA_NAME", "second-look-tools")

# Bedrock model. Copy the exact ID (or inference profile ID) from the Bedrock console
# -> Model catalog / Cross-region inference. The "us." prefix = US inference profile.
MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-sonnet-5-5")          # coordinator (writes the explanation)
# profile / news / data agents: comma-separated candidates, the first one this account can use wins
# (Haiku needs a Marketplace subscription the event account cannot make; Amazon Nova needs none)
MODEL_ID_FAST = os.getenv("MODEL_ID_FAST", "us.amazon.nova-2-lite-v1:0,us.amazon.nova-lite-v1:0")

# Local mode: run the tools from your laptop without deploying Lambda/S3.
# SECOND_LOOK_LOCAL=1 and DATA_DIR=<folder with the starter CSVs + profiles.json>
LOCAL = os.getenv("SECOND_LOOK_LOCAL", "0") == "1"
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

# Chat speed: skip the coordinator's written summary before the first chat reply (the chat never shows it;
# the chat agent still gets the three specialist reports and can check alternatives itself).
CHAT_FAST_START = os.getenv("CHAT_FAST_START", "1") == "1"

# Amazon Bedrock Guardrails (scripts/setup_guardrails.py creates them and writes guardrails.json).
# Empty = guardrails off.
_GR = {}
try:
    with open(os.path.join(os.path.dirname(__file__), "guardrails.json")) as _f:
        import json as _json
        _GR = _json.load(_f)
except (OSError, ValueError):
    pass
GUARDRAIL_INPUT_ID = os.getenv("GUARDRAIL_INPUT_ID", _GR.get("input", {}).get("id", ""))
GUARDRAIL_INPUT_VERSION = os.getenv("GUARDRAIL_INPUT_VERSION", _GR.get("input", {}).get("version", ""))
GUARDRAIL_OUTPUT_ID = os.getenv("GUARDRAIL_OUTPUT_ID", _GR.get("output", {}).get("id", ""))
GUARDRAIL_OUTPUT_VERSION = os.getenv("GUARDRAIL_OUTPUT_VERSION", _GR.get("output", {}).get("version", ""))

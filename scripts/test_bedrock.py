"""Step 1 check: can this computer call Bedrock? Run: python scripts/test_bedrock.py"""
import os
import sys

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

client = boto3.client("bedrock-runtime", region_name=config.AWS_REGION)
resp = client.converse(
    modelId=config.MODEL_ID,
    messages=[{"role": "user", "content": [{"text": "Say hello to the Second Look team in one sentence."}]}],
)
print(resp["output"]["message"]["content"][0]["text"])
print("OK - Bedrock works with model", config.MODEL_ID, "in", config.AWS_REGION)

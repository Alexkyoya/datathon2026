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
MODEL_ID = os.getenv("MODEL_ID", "global.anthropic.claude-sonnet-5-5")

# Local mode: run the tools from your laptop without deploying Lambda/S3.
# SECOND_LOOK_LOCAL=1 and DATA_DIR=<folder with the starter CSVs + profiles.json>
LOCAL = os.getenv("SECOND_LOOK_LOCAL", "0") == "1"
DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))

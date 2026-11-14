"""Run the tools Lambda on local files (no AWS). Must be set before lambda_function is imported."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["SECOND_LOOK_LOCAL"] = "1"
os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
for p in (ROOT / "lambda", ROOT / "agents", ROOT / "second_look"):
    sys.path.insert(0, str(p))

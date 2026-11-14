"""Step 2: create the bucket (if needed) and upload everything in ./data to s3://DATA_BUCKET/DATA_PREFIX.
Run: python scripts/upload_to_s3.py
"""
import os
import sys

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

s3 = boto3.client("s3", region_name=config.AWS_REGION)
try:
    s3.head_bucket(Bucket=config.DATA_BUCKET)
    print("Bucket exists:", config.DATA_BUCKET)
except ClientError:
    kw = {} if config.AWS_REGION == "us-east-1" else {
        "CreateBucketConfiguration": {"LocationConstraint": config.AWS_REGION}}
    s3.create_bucket(Bucket=config.DATA_BUCKET, **kw)
    print("Created bucket:", config.DATA_BUCKET)

data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
needed = ["prices_daily.csv", "fx_usdcad_daily.csv", "macro_rates.csv", "assets_universe.csv",
          "profiles.json", "news_snapshot.json"]
for name in sorted(os.listdir(data_dir)):
    path = os.path.join(data_dir, name)
    if os.path.isfile(path):
        s3.upload_file(path, config.DATA_BUCKET, config.DATA_PREFIX + name)
        print("uploaded", name)
missing = [n for n in needed if not os.path.exists(os.path.join(data_dir, n))]
if missing:
    print("WARNING - missing files in ./data:", missing)
else:
    print("OK - all data files are in S3")

"""Deploy (or update) the Second Look API: the agents in a Lambda with a public HTTPS URL for the dashboard.
Safe to re-run after every change to agents/ or api_handler.py.

    uv run python scripts/deploy_api.py

Steps: install strands-agents for Linux into build/, zip it with our code, upload the zip to S3,
create/update the Lambda (role: Bedrock + invoke tools Lambda + S3), create the Function URL,
then test it with the green case. Prints the URL to give to the dashboard.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, ParamValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

API_NAME = "second-look-api"
ROLE_NAME = "second-look-api-role"
BUILD = ROOT / "build" / "api"
ZIP = ROOT / "build" / "api.zip"
ZIP_KEY = "deploy/api.zip"

iam = boto3.client("iam", region_name=config.AWS_REGION)
lam = boto3.client("lambda", region_name=config.AWS_REGION)
s3 = boto3.client("s3", region_name=config.AWS_REGION)
account = boto3.client("sts", region_name=config.AWS_REGION).get_caller_identity()["Account"]


def build_zip():
    print("Installing strands-agents for Lambda (Linux, Python 3.12)...")
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    subprocess.run(["uv", "pip", "install", "--target", str(BUILD), "--python-platform", "x86_64-manylinux2014",
                    "--python-version", "3.12", "--only-binary", ":all:", "strands-agents", "boto3"], check=True)
    shutil.copy(ROOT / "config.py", BUILD)
    shutil.copy(ROOT / "api_handler.py", BUILD)
    # dashboard + team rule engine (second_look/), bundled under sl_* names
    comp = ROOT / "second_look"
    snap = comp / "data" / "runtime" / "snapshots" / "2026-11-13.json"
    if not snap.exists():
        print("Building the price snapshot (python -m second_look demo)...")
        subprocess.run([sys.executable, "-m", "second_look", "demo"], cwd=comp, check=True)
    for src, dst, pattern in [(comp / "second_look", "sl_engine", "*.py"), (comp / "config", "sl_config", "*.json"),
                              (comp / "web", "sl_web", "*"), (comp / "examples" / "clients", "sl_clients", "*.json"),
                              (snap.parent, "sl_snapshots", "*.json")]:
        (BUILD / dst).mkdir(parents=True, exist_ok=True)
        for f in src.glob(pattern):
            if f.is_file():
                shutil.copy(f, BUILD / dst / f.name)
    shutil.copy(comp / "examples" / "client.json", BUILD / "sl_clients" / "demo_balanced.json")
    (BUILD / "agents").mkdir(exist_ok=True)
    for f in ("second_look.py", "tools.py"):
        shutil.copy(ROOT / "agents" / f, BUILD / "agents")
    print("Zipping...")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in BUILD.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(BUILD).as_posix())
    print(f"api.zip: {ZIP.stat().st_size / 1e6:.1f} MB -> s3://{config.DATA_BUCKET}/{ZIP_KEY}")
    s3.upload_file(str(ZIP), config.DATA_BUCKET, ZIP_KEY)


def role_arn():
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                                       "bedrock:Converse", "bedrock:ConverseStream"], "Resource": "*"},
        {"Effect": "Allow", "Action": "lambda:InvokeFunction",
         "Resource": f"arn:aws:lambda:{config.AWS_REGION}:{account}:function:{config.LAMBDA_NAME}"},
        {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
         "Resource": f"arn:aws:s3:::{config.DATA_BUCKET}/*"}]}
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        new = False
    except iam.exceptions.NoSuchEntityException:
        print("Creating IAM role", ROLE_NAME)
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": "sts:AssumeRole",
                                                        "Principal": {"Service": "lambda.amazonaws.com"}}]}
        arn = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        iam.attach_role_policy(RoleName=ROLE_NAME,
                               PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        new = True
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="second-look-api", PolicyDocument=json.dumps(policy))
    if new:
        print("Waiting 15 s for the role to propagate...")
        time.sleep(15)
    return arn


def deploy_function(role):
    settings = dict(Role=role, Handler="api_handler.handler", Runtime="python3.12", Timeout=600, MemorySize=1024,
                    Environment={"Variables": {"DATA_BUCKET": config.DATA_BUCKET, "DATA_PREFIX": config.DATA_PREFIX,
                                               "LAMBDA_NAME": config.LAMBDA_NAME, "MODEL_ID": config.MODEL_ID,
                                               "MODEL_ID_FAST": config.MODEL_ID_FAST}})
    code = {"S3Bucket": config.DATA_BUCKET, "S3Key": ZIP_KEY}
    try:
        lam.get_function(FunctionName=API_NAME)
        print("Updating", API_NAME)
        lam.update_function_code(FunctionName=API_NAME, **code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=API_NAME)
        lam.update_function_configuration(FunctionName=API_NAME, **settings)
        lam.get_waiter("function_updated_v2").wait(FunctionName=API_NAME)
    except lam.exceptions.ResourceNotFoundException:
        print("Creating", API_NAME)
        for attempt in range(6):
            try:
                lam.create_function(FunctionName=API_NAME, Code=code, Description="Second Look API (agents)",
                                    **settings)
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if "role" in str(e).lower() and attempt < 5:
                    time.sleep(10)
                    continue
                raise
        lam.get_waiter("function_active_v2").wait(FunctionName=API_NAME)


def function_url():
    cors = {"AllowOrigins": ["*"], "AllowMethods": ["GET", "POST"], "AllowHeaders": ["content-type"], "MaxAge": 3600}
    try:
        url = lam.get_function_url_config(FunctionName=API_NAME)["FunctionUrl"]
        lam.update_function_url_config(FunctionName=API_NAME, AuthType="NONE", Cors=cors)
    except lam.exceptions.ResourceNotFoundException:
        url = lam.create_function_url_config(FunctionName=API_NAME, AuthType="NONE", Cors=cors)["FunctionUrl"]
    perms = [dict(StatementId="public-url", Action="lambda:InvokeFunctionUrl", Principal="*",
                  FunctionUrlAuthType="NONE"),
             dict(StatementId="public-url-invoke", Action="lambda:InvokeFunction", Principal="*",
                  InvokedViaFunctionUrl=True)]
    for p in perms:
        try:
            lam.add_permission(FunctionName=API_NAME, **p)
        except lam.exceptions.ResourceConflictException:
            pass  # already there
        except ParamValidationError:
            pass  # older botocore without InvokedViaFunctionUrl: the first permission is enough there
    return url


def test(url):
    print("Test call (green case: robert buys 2,000 CAD of SPY)...")
    req = urllib.request.Request(url, data=json.dumps({"client_id": "robert", "ticker": "SPY", "amount_cad": 2000}).encode(),
                                 headers={"content-type": "application/json"}, method="POST")
    for attempt in range(6):  # a brand-new URL can take a few seconds to accept calls
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = json.loads(r.read())
            print("verdict:", body["result"]["verdict"], "(expected green)")
            return
        except Exception as e:
            if attempt == 5:
                raise
            print("  not ready yet:", e)
            time.sleep(10)


if __name__ == "__main__":
    build_zip()
    deploy_function(role_arn())
    url = function_url()
    test(url)
    print("\nOK - dashboard (open in a browser):\n" + url + "\nAPI usage: " + url + "api")

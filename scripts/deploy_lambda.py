"""Deploy (or update) the tools Lambda in one command. Safe to re-run after every change to lambda_function.py.

    uv run python scripts/deploy_lambda.py

What it does (same as the manual console steps in the README):
  1. IAM role for the Lambda (logs + S3 read), created once
  2. Zips lambda/lambda_function.py
  3. Finds the AWS-managed pandas layer (AWSSDKPandas-Python312)
  4. Creates the function, or updates its code + settings if it already exists
  5. Test call: rule_check robert NVDA 100000 -> should print "red"
"""
import io
import json
import os
import sys
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config  # noqa: E402

ROLE_NAME = "second-look-lambda-role"
ROOT = os.path.join(os.path.dirname(__file__), "..")
iam = boto3.client("iam", region_name=config.AWS_REGION)
lam = boto3.client("lambda", region_name=config.AWS_REGION)


def get_role_arn():
    try:
        return iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        pass
    print("Creating IAM role", ROLE_NAME)
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                                                    "Action": "sts:AssumeRole"}]}
    arn = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust),
                          Description="Second Look tools Lambda: logs + S3 read")["Role"]["Arn"]
    for p in ("arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
              "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess"):
        iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=p)
    print("Waiting 15 s for the role to propagate...")
    time.sleep(15)
    return arn


def pandas_layer_arn():
    base = f"arn:aws:lambda:{config.AWS_REGION}:336392948345:layer:AWSSDKPandas-Python312"
    for v in range(40, 0, -1):
        try:
            return lam.get_layer_version_by_arn(Arn=f"{base}:{v}")["LayerVersionArn"]
        except ClientError:
            continue
    raise SystemExit("Could not find the AWSSDKPandas-Python312 layer. Add it by hand in the console "
                     "(Layers -> Add a layer -> AWS layers) and re-run.")


def code_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(ROOT, "lambda", "lambda_function.py"), "lambda_function.py")
    return buf.getvalue()


def main():
    role = get_role_arn()
    layer = pandas_layer_arn()
    print("pandas layer:", layer)
    settings = dict(Role=role, Handler="lambda_function.lambda_handler", Runtime="python3.12", Timeout=60,
                    MemorySize=1024, Layers=[layer],
                    Environment={"Variables": {"DATA_BUCKET": config.DATA_BUCKET, "DATA_PREFIX": config.DATA_PREFIX}})
    try:
        lam.get_function(FunctionName=config.LAMBDA_NAME)
        exists = True
    except lam.exceptions.ResourceNotFoundException:
        exists = False

    if exists:
        print("Updating", config.LAMBDA_NAME)
        lam.update_function_code(FunctionName=config.LAMBDA_NAME, ZipFile=code_zip())
        lam.get_waiter("function_updated_v2").wait(FunctionName=config.LAMBDA_NAME)
        lam.update_function_configuration(FunctionName=config.LAMBDA_NAME, **settings)
        lam.get_waiter("function_updated_v2").wait(FunctionName=config.LAMBDA_NAME)
    else:
        print("Creating", config.LAMBDA_NAME)
        for attempt in range(6):  # a new role can take a few seconds before Lambda accepts it
            try:
                lam.create_function(FunctionName=config.LAMBDA_NAME, Code={"ZipFile": code_zip()},
                                    Description="Second Look tools", **settings)
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if "role" in str(e).lower() and attempt < 5:
                    time.sleep(10)
                    continue
                raise
        lam.get_waiter("function_active_v2").wait(FunctionName=config.LAMBDA_NAME)

    print("Test call: rule_check robert NVDA 100000 ...")
    resp = lam.invoke(FunctionName=config.LAMBDA_NAME, Payload=json.dumps(
        {"action": "rule_check", "client_id": "robert", "ticker": "NVDA", "amount_cad": 100000}).encode())
    out = json.loads(resp["Payload"].read())
    if "error" in out or "errorMessage" in out:
        print("Lambda returned an error:", out)
        sys.exit(1)
    print("verdict:", out["verdict"], "(expected red)")
    print("OK - Lambda deployed. Run the agents without --local now.")


if __name__ == "__main__":
    main()

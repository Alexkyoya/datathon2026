"""Live, read-only AWS checks. No credentials, full prompts or account IDs are printed.

AWS_PROFILE=second-look uv run python scripts/check_chat_runtime.py --tools
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'second_look'))
import config
from second_look.diagnostics import failure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', help='Local named profile; Lambda should use its execution role')
    parser.add_argument('--tools', action='store_true', help='Also invoke the existing financial tools and read Lambda role/config metadata')
    args = parser.parse_args()
    import boto3
    from botocore.config import Config
    from strands import Agent
    from strands.models import BedrockModel
    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    creds = session.get_credentials()
    print(json.dumps({'region': config.AWS_REGION, 'model_id': config.MODEL_ID,
                      'credentials_found': bool(creds), 'credential_provider': getattr(creds, 'method', None)}))
    settings = Config(connect_timeout=5, read_timeout=90, retries={'max_attempts': 1})

    def check(name, action):
        try:
            result = action()
            print(json.dumps({'check': name, 'status': 'passed', **result}, default=str))
        except Exception as exc:
            print(json.dumps({'check': name, 'status': 'failed', 'diagnostic': failure(exc, name, 'runtime-check')}))

    def identity():
        session.client('sts', region_name=config.AWS_REGION, config=settings).get_caller_identity()
        return {}
    check('authentication', identity)
    def model_request():
        result = session.client('bedrock-runtime', region_name=config.AWS_REGION, config=settings).converse(
            modelId=config.MODEL_ID, messages=[{'role': 'user', 'content': [{'text': 'Reply only OK.'}]}],
            inferenceConfig={'maxTokens': 16})
        return {'aws_request_id': result['ResponseMetadata']['RequestId']}
    check('bedrock_no_tools', model_request)
    def strands_request():
        model = BedrockModel(model_id=config.MODEL_ID, region_name=config.AWS_REGION,
                             boto_session=session, boto_client_config=settings)
        result = Agent(model=model, system_prompt='Reply only OK.', callback_handler=None)('Connection check.')
        return {'response_received': bool(str(result).strip())}
    check('strands_no_tools', strands_request)
    if args.tools:
        client = session.client('lambda', region_name=config.AWS_REGION, config=settings)
        for name in (config.LAMBDA_NAME, 'second-look-api'):
            def metadata(name=name):
                result = client.get_function_configuration(FunctionName=name)
                return {'function': name, 'role_name': result['Role'].rsplit('/', 1)[-1],
                        'timeout_seconds': result['Timeout'],
                        'model_id': result.get('Environment', {}).get('Variables', {}).get('MODEL_ID')}
            check('lambda_configuration', metadata)
        for action, fields in [('news_check', {'ticker': 'BTC-USD', 'decision_date': '2026-11-13'}),
                               ('macro_snapshot', {'decision_date': '2026-11-13'}),
                               ('risk_metrics', {'tickers': ['BTC-USD'], 'decision_date': '2026-11-13'})]:
            def invoke(action=action, fields=fields):
                result = client.invoke(FunctionName=config.LAMBDA_NAME, Payload=json.dumps({'action': action, **fields}).encode())
                data = json.loads(result['Payload'].read())
                if result.get('FunctionError') or data.get('error'):
                    raise RuntimeError('Financial tool did not return a usable result')
                return {'tool': action, 'aws_request_id': result['ResponseMetadata']['RequestId']}
            check(action, invoke)


if __name__ == '__main__':
    main()

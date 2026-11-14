"""Existing S3/Lambda adapter; no infrastructure is created by this module."""
import json
import logging
import os
from .application import S3Store, SecondLookApplication

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def handler(event, context):
    import boto3  # Supplied by AWS; rules/service/application use stdlib only.
    try:
        store = S3Store(boto3.client('s3'), os.environ['DATA_BUCKET'],
                        os.environ.get('DATA_PREFIX', 'second-look'))
        function = os.environ.get('AGENT_FUNCTION_NAME')
        dispatch = None
        if function:
            def dispatch(payload):
                response = boto3.client('lambda').invoke(
                    FunctionName=function, InvocationType='Event',
                    Payload=json.dumps(payload, allow_nan=False).encode())
                if response.get('StatusCode') != 202:
                    raise RuntimeError('Existing agent Lambda did not accept the handoff')
        app = SecondLookApplication(store, dispatch)
        body = json.loads(event['body']) if isinstance(event.get('body'), str) else event
        # An authenticated platform endpoint must authorize client/result access
        # upstream. The profile is never accepted from the proposed trade.
        path = event.get('rawPath', event.get('path', '/evaluate'))
        if path.endswith('/decision'):
            if set(body)-{'decision', 'reason'} or 'decision' not in body:
                raise ValueError('Decision accepts only decision and optional reason')
            sid = path.rstrip('/').split('/')[-2]
            record = app.decide(sid, body['decision'], body.get('reason'))
        else:
            record = app.evaluate(body, getattr(context, 'aws_request_id', None))
        return {'statusCode': 200, 'headers': {'Content-Type': 'application/json'},
                'body': json.dumps(record, allow_nan=False)}
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning(json.dumps({'step': 'input_rejected', 'error': str(exc)}))
        return {'statusCode': 400, 'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'status': 'invalid_input', 'verdict': None, 'error': str(exc)})}

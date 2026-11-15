"""Small, credential-safe diagnostics shared by the local and AWS chat adapters."""
import hashlib
import json
import logging
import re

logger = logging.getLogger('second_look.chat')
logger.setLevel(logging.INFO)
if not logger.hasHandlers():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    logger.propagate = False


def redact(text):
    text = str(text)
    text = re.sub(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b', '[access key redacted]', text)
    text = re.sub(r'arn:[^\s,;\"\']+', '[AWS resource redacted]', text)
    text = re.sub(r'(?i)((?:aws_)?(?:secret_access_key|session_token|access_key_id)|authorization)([\"\s:=]+)[^\s,}\"]+',
                  r'\1\2[redacted]', text)
    return text[:500]


def failure(exc, component, request_id):
    response = getattr(exc, 'response', {})
    error = response.get('Error', {})
    # Provider messages can contain submitted values. Only local validation errors
    # are allowlisted as prose; AWS failures are identified by type/code/request ID.
    code = getattr(exc, 'code', None) or error.get('Code') or type(exc).__name__
    detail = redact(str(exc)) if isinstance(exc, (AnswerValidationError, ValueError, KeyError, TypeError)) else 'See error code and AWS request ID.'
    return {'component': component, 'code': code, 'exception_type': type(exc).__name__,
            'detail': detail, 'request_id': request_id,
            'aws_request_id': response.get('ResponseMetadata', {}).get('RequestId')}


def emit(event, request_id, **fields):
    logger.info(json.dumps({'event': event, 'request_id': request_id, **fields}, default=str))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class AnswerValidationError(ValueError):
    def __init__(self, message, code='invalid_response'):
        super().__init__(message)
        self.code = code

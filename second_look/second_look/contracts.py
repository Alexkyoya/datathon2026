"""Shared input validation and contract constants for every adapter."""
import math
import re
from datetime import date

SCHEMA_VERSION = '2.0'
CLASSES = ('cash', 'fixed_income', 'equities', 'alternatives')
PROFILE_ORDER = ('Income', 'Conservative', 'Balanced', 'Growth', 'Maximum growth')
TRADE_FIELDS = {'client_id', 'decision_date', 'ticker', 'side', 'amount_cad'}


def number(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be numeric')
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f'{label} must be finite and >= {minimum}')
    return float(value)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('Invalid identifier')
    return value


def validate_request(request):
    if not isinstance(request, dict) or set(request) != TRADE_FIELDS:
        raise ValueError('Trade requires only client_id, decision_date, ticker, side, amount_cad')
    identifier(request['client_id'])
    day = request['decision_date']
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('decision_date must be YYYY-MM-DD')
    if not isinstance(request['ticker'], str) or request['side'] not in ('buy', 'sell'):
        raise ValueError('Invalid ticker or side')
    if number(request['amount_cad'], 'amount_cad') <= 0:
        raise ValueError('amount_cad must be positive')


def data_quality_limits(policy):
    limits = policy['data_quality']
    for field in ('max_security_price_age_days', 'max_crypto_price_age_days',
                  'max_fx_age_days', 'annualization_days'):
        number(limits[field], field)
    if limits['annualization_days'] != 365:
        raise ValueError('Daily UTC calendar returns require annualization_days=365')
    return limits

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


GOAL_TYPES = ('one_off', 'recurring')
PRIORITIES = ('essential', 'flexible')
SUPPORTED_PREFERENCES = {'excluded_tickers', 'excluded_sectors', 'minimum_cash_cad', 'esg_required',
                         'other_constraints', 'esg'}
ESG_FIELDS = {'excluded_tickers', 'excluded_sectors', 'max_lookthrough_excluded_share',
              'prefer_esg_screened_funds', 'source'}


def iso_date(value, label):
    try:
        if isinstance(value, str) and date.fromisoformat(value).isoformat() == value:
            return date.fromisoformat(value)
    except ValueError:
        pass
    raise ValueError(f'{label} must be a date YYYY-MM-DD, got {value!r}')


def not_in_universe(ticker, assets):
    return ValueError(f'{ticker} is not in the asset universe (the provided datathon data). '
                      f'Choose one of: {", ".join(sorted(assets))}')


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError(f'{label} must be a list of strings')
    return value


def validate_goal(goal, i, decision_day):
    if not isinstance(goal, dict):
        raise ValueError(f'goals[{i}] must be an object')
    name = goal.get('name')
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f'goals[{i}] needs a name')
    where = f'goal "{name}"'
    if goal.get('type') not in GOAL_TYPES:
        raise ValueError(f'{where}: type must be one_off or recurring')
    if goal.get('priority') not in PRIORITIES:
        raise ValueError(f'{where}: priority must be essential or flexible')
    if goal['type'] == 'one_off':
        number(goal.get('amount_cad'), f'{where}: amount_cad')
        target = iso_date(goal.get('target_date'), f'{where}: target_date')
        if target < decision_day:
            raise ValueError(f'{where}: target_date {target} is in the past (decision date {decision_day})')
        return
    number(goal.get('amount_cad_per_year'), f'{where}: amount_cad_per_year')
    start = iso_date(goal.get('start_date'), f'{where}: start_date')
    if ('years' in goal) == ('end_date' in goal):
        raise ValueError(f'{where}: give exactly one of years or end_date')
    if 'years' in goal:
        years = goal['years']
        if isinstance(years, bool) or not isinstance(years, int) or years < 1:
            raise ValueError(f'{where}: years must be a whole number >= 1')
    else:
        end = iso_date(goal['end_date'], f'{where}: end_date')
        if end <= start:
            raise ValueError(f'{where}: end_date {end} is not after start_date {start}')
    number(goal.get('funded_by_other_income_cad_per_year', 0), f'{where}: funded_by_other_income_cad_per_year')
    if not isinstance(goal.get('indexed_to_inflation'), bool):
        raise ValueError(f'{where}: indexed_to_inflation must be true or false')


def validate_client(client, decision_date, assets):
    """Schema for the stored client: profile context, goals, income and preferences.
    Raises ValueError with a plain message on the first problem."""
    if not isinstance(client, dict):
        raise ValueError('client must be an object')
    decision_day = iso_date(decision_date, 'decision_date')
    number(client['horizon_years'], 'horizon_years')
    tolerance = number(client['loss_tolerance_fraction'], 'loss_tolerance_fraction')
    if tolerance > 1 or not isinstance(client['objectives'], list):
        raise ValueError('Invalid stored loss tolerance or objectives')
    preferences = client['preferences']
    if not isinstance(preferences, dict):
        raise ValueError('preferences must be an object')
    for key in ('excluded_tickers', 'excluded_sectors'):
        if key in preferences:
            _strings(preferences[key], key)
    if 'esg_required' in preferences and not isinstance(preferences['esg_required'], bool):
        raise ValueError('esg_required must be boolean')
    esg = preferences.get('esg')
    if esg is not None:
        if not isinstance(esg, dict):
            raise ValueError('preferences.esg must be an object')
        if set(esg) - ESG_FIELDS:
            raise ValueError('preferences.esg has unknown fields: ' + ', '.join(sorted(set(esg) - ESG_FIELDS)))
        for key in ('excluded_tickers', 'excluded_sectors'):
            _strings(esg.get(key, []), f'preferences.esg.{key}')
        if 'max_lookthrough_excluded_share' in esg:
            if number(esg['max_lookthrough_excluded_share'], 'max_lookthrough_excluded_share') > 1:
                raise ValueError('max_lookthrough_excluded_share must be between 0 and 1')
        if not isinstance(esg.get('prefer_esg_screened_funds', False), bool):
            raise ValueError('prefer_esg_screened_funds must be true or false')
    excluded = list(preferences.get('excluded_tickers', [])) + list((esg or {}).get('excluded_tickers', []))
    for ticker in excluded:
        if ticker not in assets:
            raise ValueError(f'Excluded ticker {ticker} is not in the asset universe (the provided datathon data)')
    known_sectors = {s for a in assets.values() for s in (a.get('sector_weights') or {})}
    for sector in list(preferences.get('excluded_sectors', [])) + list((esg or {}).get('excluded_sectors', [])):
        if sector not in known_sectors:
            raise ValueError(f'Unknown sector exclusion: {sector}. Known sectors: {", ".join(sorted(known_sectors))}')
    income = client.get('income')
    other = 0.0
    if income is not None:
        if not isinstance(income, dict):
            raise ValueError('income must be an object')
        other = number(income.get('other_income_cad_per_year', 0), 'income.other_income_cad_per_year')
    goals = client.get('goals', [])
    if not isinstance(goals, list):
        raise ValueError('goals must be a list')
    for i, goal in enumerate(goals):
        validate_goal(goal, i, decision_day)
    assigned = math.fsum(g.get('funded_by_other_income_cad_per_year', 0) for g in goals if g['type'] == 'recurring')
    if assigned > other + 1e-6:
        raise ValueError(f'Goals assign CAD {assigned:,.0f}/year of other income, but income.other_income_cad_per_year '
                         f'is CAD {other:,.0f}')


def data_quality_limits(policy):
    limits = policy['data_quality']
    for field in ('max_security_price_age_days', 'max_crypto_price_age_days',
                  'max_fx_age_days', 'annualization_days'):
        number(limits[field], field)
    if limits['annualization_days'] != 365:
        raise ValueError('Daily UTC calendar returns require annualization_days=365')
    return limits

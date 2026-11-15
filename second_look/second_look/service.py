"""Value stored units from a dated snapshot before calling the pure engine."""
from datetime import datetime, timezone, timedelta, date
import hashlib
import json
from .rules import evaluate_trade
from .contracts import number, data_quality_limits, validate_request
from .spectrum import load_references


def value_holdings(client, snapshot, assets, policy, day, extra_tickers=()):
    """Share dated quote validation between the account display and trade evaluation."""
    quality = data_quality_limits(policy)
    if snapshot['decision_date'] != day or client['holdings_as_of'] != day:
        raise ValueError('Snapshot and holdings must match the decision date')
    asof = datetime.fromisoformat(snapshot['snapshot_at'].replace('Z','+00:00'))
    expected = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    if asof != expected:
        raise ValueError('Daily simulation requires decision-date 00:00 UTC snapshot')
    values = {'CASH_CAD':number(client['cash_cad'],'cash_cad')}
    used = {}
    needed = set(client['holdings']) | set(extra_tickers)
    for ticker in sorted(needed):
        if ticker not in assets or ticker not in snapshot['quotes']:
            raise ValueError(f'Missing asset or price for {ticker}')
        q = snapshot['quotes'][ticker]
        if q.get('usable', True) is not True:
            raise ValueError(f'Unusable quote for {ticker}')
        price = number(q['close_cad'],ticker+' close_cad')
        if price <= 0:
            raise ValueError('Price must be positive')
        pdate = datetime.fromisoformat(q['price_available_at'].replace('Z','+00:00'))
        age = (asof-pdate).total_seconds()/86400
        cap = quality['max_crypto_price_age_days'] if assets[ticker]['is_crypto'] else quality['max_security_price_age_days']
        if not 0 <= age <= cap:
            raise ValueError('Future or stale price')
        source_day = date.fromisoformat(q['price_source_date'][:10])
        if pdate != datetime.combine(source_day+timedelta(days=1), datetime.min.time(), timezone.utc):
            raise ValueError('Price source date disagrees with next-day availability')
        if assets[ticker]['currency']=='USD':
            fdate = datetime.fromisoformat(q['fx_available_at'].replace('Z','+00:00'))
            if not 0 <= (asof-fdate).total_seconds()/86400 <= quality['max_fx_age_days']:
                raise ValueError('Future or stale FX')
            fx_day = date.fromisoformat(q['fx_source_date'][:10])
            if fdate != datetime.combine(fx_day+timedelta(days=1), datetime.min.time(), timezone.utc):
                raise ValueError('FX source date disagrees with next-day availability')
        units = number(client['holdings'].get(ticker,0),ticker+' units')
        values[ticker] = units*price
        used[ticker] = q
    return values, used


def evaluate_from_snapshot(request, client, snapshot, assets, policy, references=None):
    validate_request(request)
    references = load_references() if references is None else references
    values, used = value_holdings(client, snapshot, assets, policy, request['decision_date'], [request['ticker']])
    result = evaluate_trade(request,client,values,assets,policy,references)
    result['input_sha256'] = {name:hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()
                              for name,value in [('assets',assets),('policy',policy),('client',client),('request',request),('snapshot',snapshot)]}
    result['input_sha256']['references'] = hashlib.sha256(json.dumps(references,sort_keys=True,allow_nan=False).encode()).hexdigest()
    result['valuation'] = {'snapshot_at':snapshot['snapshot_at'],'quotes':used,
        'sources':snapshot['sources'],'source_sha256':snapshot['source_sha256'],
        'convention':'Closing prices for value; adjusted prices reserved for returns. No execution is performed.'}
    result['simulated_trade_units'] = request['amount_cad']/used[request['ticker']]['close_cad']
    return result

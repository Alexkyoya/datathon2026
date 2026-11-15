"""Account/quote display data, using the valuation service's dated prices.

No bid/ask, analyst ratings or execution prices are fabricated. Optional daily
OHLC and chart fields are provided only by a generated historical snapshot.
"""
import math
from .contracts import identifier, number, validate_client
from .service import value_holdings
from .spectrum import build_references
from .application import load_client


def build_context(store, client_id, clients=()):
    identifier(client_id)
    client = load_client(store, client_id)
    assets = store.load('config/assets.json')
    policy = store.load('config/policy.json')
    references = store.load('config/reference_portfolios.json')
    day = client['holdings_as_of']
    validate_client(client, day, assets)
    snapshot = store.load(f'snapshots/{day}.json')
    values, used = value_holdings(client, snapshot, assets, policy, day)
    quotes = {}
    for ticker, asset in assets.items():
        if ticker not in snapshot['quotes']:
            continue
        try:
            _, checked = value_holdings(client, snapshot, assets, policy, day, [ticker])
            quote = dict(checked[ticker])
            factor = number(quote['fx_to_cad'], 'fx_to_cad')
            if factor <= 0:
                raise ValueError('FX factor must be positive')
            quote['close_native'] = quote['close_cad']/factor
            quote['currency'] = asset['currency']
            quotes[ticker] = quote
        except (ValueError, KeyError, TypeError):
            # An unavailable quote is never presented as tradable.
            continue
    holdings = []
    total = math.fsum(values.values())
    if total <= 0:
        raise ValueError('Portfolio value must be positive')
    for ticker, units in client['holdings'].items():
        holdings.append({'ticker': ticker, 'name': assets[ticker]['name'],
                         'units': units, 'currency': assets[ticker]['currency'],
                         'value_cad': values[ticker], 'weight': values[ticker]/total,
                         'close_cad': used[ticker]['close_cad']})
    # Read-only display fields. `horizon` is the stored historical drawdown/recovery
    # (illustrates the past; never a forecast) shown in the asset picker.
    info = {t: {k: a.get(k) for k in ('name', 'asset_class', 'instrument_type', 'currency', 'sector', 'market',
                                      'is_single_stock', 'is_crypto', 'horizon')}
            for t, a in assets.items()}
    limits = policy.get('profiles', {}).get(client.get('profile'), {})
    profile_limits = {k: limits.get(k) for k in ('single_stock_max', 'crypto_max', 'sector_max')}
    name = client.get('first_name') or client.get('name', '').split(' ')[0] or None
    return {'client': client, 'first_name': name, 'clients': sorted(set(clients) | {client_id}),
            'assets': info, 'tickers': sorted(assets), 'quotes': quotes, 'profile_limits': profile_limits,
            'holdings': sorted(holdings, key=lambda h: -h['value_cad']),
            'account': {'total_cad': total, 'invested_cad': total-client['cash_cad'],
                        'cash_cad': client['cash_cad'], 'snapshot_at': snapshot['snapshot_at']},
            'price_history': {t: points for t, points in snapshot.get('price_history', {}).items() if t in quotes},
            'references': build_references(references, assets, policy),
            'reference_config': references,
            'data_label': 'Historical daily prices. This simulation does not place an order.'}

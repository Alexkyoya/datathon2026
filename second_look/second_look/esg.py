"""ESG preferences: exclusions and fund look-through. Pure and deterministic, no I/O.

One mechanism for every client. preferences.esg holds the screen; the older top-level
preferences.excluded_tickers / excluded_sectors are read as part of the same screen.
Only buying can create or worsen exposure, so selling is never flagged here.
"""
EPS = 1e-10
SOURCE = 'stored_client_preference'


def screen(preferences, policy):
    esg = preferences.get('esg') or {}
    tickers = sorted(set(preferences.get('excluded_tickers', [])) | set(esg.get('excluded_tickers', [])))
    sectors = sorted(set(preferences.get('excluded_sectors', [])) | set(esg.get('excluded_sectors', [])))
    limit = esg.get('max_lookthrough_excluded_share', policy['esg']['default_max_lookthrough_excluded_share'])
    return {'excluded_tickers': tickers, 'excluded_sectors': sectors, 'max_lookthrough_excluded_share': float(limit),
            'prefer_esg_screened_funds': bool(esg.get('prefer_esg_screened_funds', False)),
            'source': esg.get('source')}


def _alert(rule, severity, before, after, limit, effect, message, **extra):
    return {'rule': rule, 'kind': 'esg', 'limit': limit, 'bound': 'max', 'before': before, 'after': after,
            'gap_before': max(0.0, before - limit), 'gap_after': max(0.0, after - limit), 'effect': effect,
            'severity': severity, 'resolved': False, 'unit': 'weight', 'source': SOURCE, 'message': message, **extra}


def excluded_share(sector_weights, sectors):
    return sum(sector_weights.get(s, 0.0) for s in sectors)


def esg_check(preferences, policy, assets, trade, metrics_before, metrics_after):
    s = screen(preferences, policy)
    applied = bool(s['excluded_tickers'] or s['excluded_sectors'])
    ticker, side = trade['ticker'], trade['side']
    asset = assets[ticker]
    info = asset.get('esg')
    summary = dict(s, applied=applied, ticker=ticker,
                   ticker_esg_screened=bool(info and info.get('screened')), ticker_esg=info)
    if s['prefer_esg_screened_funds'] and side == 'buy' and not asset['is_single_stock'] \
            and asset['asset_class'] == 'equities':
        summary['screened_fund_note'] = (f"{ticker} is ESG-screened ({info['index']}, {info['source']}, {info['as_of']})."
                                         if info and info.get('screened') else
                                         f'{ticker} is not an ESG-screened fund in our data.')
    if not applied:
        summary['note'] = 'No ESG exclusions stored for this client.'
        return summary, [], []
    checks, unresolved = [], []
    sectors = s['excluded_sectors']
    limit = s['max_lookthrough_excluded_share']
    w_b = metrics_before['positions'].get(ticker, 0.0)
    w_a = metrics_after['positions'].get(ticker, 0.0)

    # 1. Buying an excluded ticker -> red. Selling it is an improvement, never flagged.
    if ticker in s['excluded_tickers'] and abs(w_a - w_b) > EPS:
        buy = w_a > w_b
        checks.append(_alert('esg.excluded_ticker.' + ticker, 'red' if buy else 'none', w_b, w_a, 0.0,
                             ('creates' if w_b <= EPS else 'worsens') if buy else 'improves',
                             f'{ticker} is on your list of excluded investments.' if buy else
                             f'Selling {ticker} reduces an investment you excluded.'))
    # 2. A single stock whose sector is excluded -> red.
    stock_sector = asset.get('sector') if asset['is_single_stock'] else None
    if stock_sector in sectors and abs(w_a - w_b) > EPS:
        buy = w_a > w_b
        checks.append(_alert(f'esg.excluded_sector_stock.{ticker}', 'red' if buy else 'none', w_b, w_a, 0.0,
                             ('creates' if w_b <= EPS else 'worsens') if buy else 'improves',
                             f'{ticker} is a {stock_sector} company, a sector you excluded.' if buy else
                             f'Selling {ticker} reduces {stock_sector} exposure you excluded.',
                             sector=stock_sector))
    # 3. Fund look-through: the fund's own share in excluded sectors above the client's limit -> yellow.
    if side == 'buy' and not asset['is_single_stock'] and asset['asset_class'] == 'equities' and sectors:
        weights = asset.get('sector_weights')
        src = asset.get('sector_weights_source') or {}
        if weights is None:
            unresolved.append({'rule': 'esg.fund_lookthrough.' + ticker, 'handling_key': 'esg_lookthrough',
                               'reason': f'{ticker} has no sector breakdown in our data, so its share in '
                                         f'{", ".join(sectors)} cannot be checked against your preferences.'})
        else:
            share = excluded_share(weights, sectors)
            summary['fund_excluded_share'] = share
            summary['fund_sector_source'] = {k: src.get(k) for k in ('source', 'url', 'as_of')}
            if share > limit + EPS:
                checks.append(_alert('esg.fund_lookthrough.' + ticker, 'yellow', share, share, limit,
                                     'creates' if w_b <= EPS else 'worsens',
                                     f"{ticker} holds {share:.1%} in {', '.join(sectors)}, above your "
                                     f"{limit:.0%} limit (issuer fact sheet as of {src.get('as_of', 'unknown date')}).",
                                     fund_excluded_share=share, fact_sheet=src.get('source'),
                                     fact_sheet_as_of=src.get('as_of'), subject='fund'))
    # 4. Portfolio level: total look-through exposure to excluded sectors rises above the limit -> yellow.
    if sectors:
        p_b = excluded_share(metrics_before['sectors'], sectors)
        p_a = excluded_share(metrics_after['sectors'], sectors)
        summary.update(portfolio_excluded_share_before=p_b, portfolio_excluded_share_after=p_a)
        if p_a > limit + EPS and p_a > p_b + EPS:
            checks.append(_alert('esg.portfolio_lookthrough', 'yellow', p_b, p_a, limit,
                                 'creates' if p_b <= limit + EPS else 'worsens',
                                 f"Your whole portfolio would hold {p_a:.1%} in {', '.join(sectors)} "
                                 f'(was {p_b:.1%}), above your {limit:.0%} limit.', subject='portfolio'))
        elif p_a < p_b - EPS and p_b > limit + EPS:
            checks.append(_alert('esg.portfolio_lookthrough', 'none', p_b, p_a, limit, 'improves',
                                 f"Your exposure to {', '.join(sectors)} falls from {p_b:.1%} to {p_a:.1%}.",
                                 subject='portfolio'))
    return summary, checks, unresolved

"""Daily UTC snapshots with backward-only matching and explicit provenance."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd
from .contracts import data_quality_limits


def clean_data(prices, fx, assets, quality):
    quality = data_quality_limits({'data_quality':quality})
    max_price_age_days = quality['max_security_price_age_days']
    max_fx_age_days = quality['max_fx_age_days']
    prices, fx = prices.copy(), fx.copy()
    if prices.empty or fx.empty:
        raise ValueError('Price and FX source observations are required')
    required = {'date', 'ticker', 'currency', 'close', 'adj_close', 'volume'}
    if not required.issubset(prices):
        raise ValueError(f'Missing price columns: {required-set(prices)}')
    if not {'date', 'usdcad'}.issubset(fx):
        raise ValueError('FX requires date and usdcad (CAD per USD)')
    for frame in (prices, fx):
        frame['date'] = pd.to_datetime(frame['date'], errors='raise').dt.normalize()
        if frame['date'].isna().any():
            raise ValueError('Missing dates')
    if prices.duplicated(['date', 'ticker']).any() or fx.duplicated('date').any():
        raise ValueError('Duplicate price/FX keys; resolve upstream, never silently average')
    for frame, columns in ((prices, ['close', 'adj_close']), (fx, ['usdcad'])):
        for column in columns:
            frame[column] = pd.to_numeric(frame[column], errors='raise')
            if not (np.isfinite(frame[column]) & (frame[column] > 0)).all():
                raise ValueError(f'{column} must be finite and positive')
    prices['volume'] = pd.to_numeric(prices['volume'], errors='raise')
    if not (np.isfinite(prices['volume']) & (prices['volume'] >= 0)).all():
        raise ValueError('Invalid volume')
    if 'cadusd' in fx and not np.allclose(fx.usdcad * fx.cadusd, 1, rtol=1e-5):
        raise ValueError('FX reciprocal check failed')
    unknown = set(prices.ticker) - set(assets)
    if unknown:
        raise ValueError(f'Unmapped tickers: {sorted(unknown)}')
    for ticker, group in prices.groupby('ticker'):
        if assets[ticker]['currency'] not in ('CAD', 'USD'):
            raise ValueError(f'Unsupported currency for {ticker}')
        if set(group.currency) != {assets[ticker]['currency']}:
            raise ValueError(f'Currency mismatch for {ticker}')

    # D is a completed source day; its values first enter snapshot D+1 at 00:00 UTC.
    # This includes the entire UTC crypto day and the North American equity close.
    prices['price_source_date'] = prices.date
    prices['price_available_at'] = (prices.date + pd.Timedelta(days=1)).dt.tz_localize('UTC')
    fx['fx_source_date'] = fx.date
    fx['fx_available_at'] = (fx.date + pd.Timedelta(days=1)).dt.tz_localize('UTC')
    calendar = pd.DataFrame({'snapshot_at': pd.date_range(
        prices.price_available_at.min(), prices.price_available_at.max(), freq='D')})
    fx_cols = ['fx_source_date', 'fx_available_at', 'usdcad']
    pieces = []
    for ticker, group in prices.groupby('ticker', sort=True):
        group = group.drop(columns=['date', 'ticker', 'currency']).rename(columns={
            key:'source_'+key for key in ['open','high','low','volume']}).sort_values('price_available_at')
        joined = pd.merge_asof(calendar, group, left_on='snapshot_at',
                               right_on='price_available_at', direction='backward')
        joined = pd.merge_asof(joined, fx[fx_cols].sort_values('fx_available_at'),
                               left_on='snapshot_at', right_on='fx_available_at', direction='backward')
        joined['ticker'] = ticker
        joined['currency'] = assets[ticker]['currency']
        joined['asset_class'] = assets[ticker]['asset_class']
        joined['price_age_days'] = (joined.snapshot_at - joined.price_available_at).dt.days
        joined['fx_age_days'] = (joined.snapshot_at - joined.fx_available_at).dt.days
        joined['price_observed'] = joined.snapshot_at.eq(joined.price_available_at)
        joined['fx_carried'] = joined.fx_age_days.gt(0) & (joined.currency == 'USD')
        joined['price_carried'] = joined.price_age_days.gt(0)
        joined['fx_to_cad'] = joined.usdcad if assets[ticker]['currency'] == 'USD' else 1.0
        cap = quality['max_crypto_price_age_days'] if assets[ticker]['is_crypto'] else max_price_age_days
        valid_price = joined.price_age_days.le(cap) & joined.price_age_days.ge(0)
        valid_fx = joined.fx_age_days.le(max_fx_age_days) & joined.fx_age_days.ge(0)
        joined['price_missing'] = joined.price_available_at.isna()
        joined['fx_missing'] = joined.fx_available_at.isna() & (joined.currency == 'USD')
        joined['price_stale'] = joined.price_age_days.gt(cap)
        joined['fx_stale'] = joined.fx_age_days.gt(max_fx_age_days) & (joined.currency == 'USD')
        joined['usable'] = valid_price & (valid_fx | (joined.currency == 'CAD'))
        joined['close_cad'] = (joined.close * joined.fx_to_cad).where(joined.usable)
        joined['adj_close_cad'] = (joined.adj_close * joined.fx_to_cad).where(joined.usable)
        joined['return_cad_daily'] = joined.adj_close_cad.pct_change(fill_method=None)
        pieces.append(joined)
    aligned = pd.concat(pieces, ignore_index=True).sort_values(['snapshot_at', 'ticker'])
    report = {
        'source_rows': len(prices), 'fx_rows': len(fx), 'asset_count': prices.ticker.nunique(),
        'missing_assets': sorted(set(assets)-set(prices.ticker)),
        'data_quality_limits': quality,
        'source_start': str(prices.date.min().date()), 'source_end': str(prices.date.max().date()),
        'snapshot_start': str(aligned.snapshot_at.min()), 'snapshot_end': str(aligned.snapshot_at.max()),
        'aligned_rows': len(aligned), 'snapshot_count': len(calendar),
        'carried_price_rows': int(aligned.price_carried.sum()),
        'carried_fx_rows_usd': int(aligned.fx_carried.sum()),
        'unusable_rows': int((~aligned.usable).sum()),
        'zero_volume_source_rows': prices.loc[prices.volume.eq(0), ['date','ticker']].astype(str).to_dict('records'),
        'max_price_age_days': int(aligned.price_age_days.max()),
        'max_fx_age_days': int(aligned.fx_age_days.max()),
        'calendar': 'Every UTC day at 00:00. Only complete source days strictly before snapshot date.',
        'annualization': 'Daily calendar returns: use 365, not 252. Carried prices can smooth volatility.',
        'limitations': [
            'Offline daily simulation: assumed next-day availability; vendor latency not reconstructed.',
            'Dates are aligned, but equity closes, daily average FX and UTC crypto closes are asynchronous.',
            'No backfill; no interpolation. Missing or stale data stays unusable.',
            'Starter closes are split-adjusted; historical quantities must use consistent split-adjusted units.',
            'Adjusted close is dividend/split-adjusted and back-adjusted; historical vintages are unavailable.',
            'Universe selected in 2026 has survivorship bias.',
            'CNQ.TO 2022-08-22 1.021 split flag retained as source caveat, not applied again.',
            'ZST.TO zero-volume row retained and flagged, not treated as missing.',
            'No exchange holiday feed: carried price cannot distinguish closure, suspension or source outage.',
            'Macro and news are outside this cleaner. Revised FRED data is not point-in-time data.'
        ]
    }
    return aligned, report


def run_cleaning(root):
    root = Path(root)
    assets = json.loads((root/'config/assets.json').read_text())
    policy = json.loads((root/'config/policy.json').read_text())
    raw, out = root/'data/raw', root/'data/clean'
    out.mkdir(parents=True, exist_ok=True)
    aligned, report = clean_data(pd.read_csv(raw/'prices_daily.csv'),
                                pd.read_csv(raw/'fx_usdcad_daily.csv'), assets, data_quality_limits(policy))
    report['source_sha256'] = {name: hashlib.sha256((raw/name).read_bytes()).hexdigest()
                               for name in ('prices_daily.csv','fx_usdcad_daily.csv','sources_metadata.csv')}
    report['sources'] = pd.read_csv(raw/'sources_metadata.csv').fillna('').to_dict('records')
    report['method_reference_urls'] = [
        'https://www.bankofcanada.ca/rates/exchange/daily-exchange-rates/',
        'https://pandas.pydata.org/docs/reference/api/pandas.merge_asof.html',
        'https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.pct_change.html']
    aligned.to_csv(out/'aligned_prices_cad.csv', index=False)
    aligned.pivot(index='snapshot_at', columns='ticker', values='return_cad_daily').to_csv(out/'returns_daily_cad.csv')
    (out/'quality_report.json').write_text(json.dumps(report, indent=2)+'\n')
    return aligned, report

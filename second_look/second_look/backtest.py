"""Historical comparison of two fixed mixes: the portfolio without and with the proposed trade.

What happened, not what will happen. Each mix is held at constant weights (rebalanced
daily) through the provided price window, in CAD, with cash earning 0%. This is a
look-back illustration: the mix did not exist back then, the universe was chosen in
2026 (survivorship), and nothing here is a forecast or a probability.
"""
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

CASH = 'CASH_CAD'
MONTH_DAYS = 365.25 / 12


@lru_cache(maxsize=4)
def load_history(raw_dir):
    """Daily adj_close in CAD on the union calendar, forward-filled (same rule as the snapshot)."""
    raw_dir = Path(raw_dir)
    prices = pd.read_csv(raw_dir / 'prices_daily.csv', parse_dates=['date'])
    fx = pd.read_csv(raw_dir / 'fx_usdcad_daily.csv', parse_dates=['date']).set_index('date').sort_index()
    fx = fx.reindex(pd.date_range(prices['date'].min(), prices['date'].max(), freq='D'))['usdcad'].ffill()
    prices = prices.merge(fx.rename('usdcad'), left_on='date', right_index=True, how='left')
    rate = np.where(prices['currency'].eq('USD'), prices['usdcad'], 1.0)
    prices['adj_close_cad'] = prices['adj_close'] * rate
    wide = prices.pivot(index='date', columns='ticker', values='adj_close_cad').sort_index().ffill()
    return wide


def _returns(wide, tickers, end):
    cols = [t for t in tickers if t != CASH and t in wide.columns]
    frame = wide.loc[:pd.Timestamp(end), cols].dropna(how='any')
    return frame.pct_change().iloc[1:]


def _mix_curve(rets, weights, start_value):
    """Constant-mix (daily rebalanced) value curve. Cash weight earns 0%."""
    w = pd.Series({t: weights.get(t, 0.0) for t in rets.columns})
    daily = rets.mul(w, axis=1).sum(axis=1)          # cash contributes 0
    curve = start_value * (1 + daily).cumprod()
    return pd.concat([pd.Series([start_value], index=[rets.index[0] - pd.Timedelta(days=1)]), curve])


def _max_drawdown(curve):
    peak = curve.cummax()
    dd = curve / peak - 1
    i = dd.idxmin()
    return float(dd.min()), i.date().isoformat()


def _rolling_year_outcomes(curve, amount):
    """What `amount` became after every 12-month window inside the curve (worst / median / best)."""
    s = curve.copy()
    s.index = pd.to_datetime(s.index)
    end_values = []
    for start in s.index[::7]:                         # weekly starts keep it cheap and representative
        end = start + pd.Timedelta(days=365)
        if end > s.index[-1]:
            break
        v0 = s.loc[start]
        v1 = s.loc[:end].iloc[-1]
        end_values.append(amount * v1 / v0)
    if not end_values:
        return None
    arr = np.array(end_values)
    return {'windows': int(len(arr)), 'worst': float(arr.min()), 'median': float(np.median(arr)),
            'best': float(arr.max()), 'share_below_start': float((arr < amount).mean())}


def compare(weights_before, weights_after, trade_amount_cad, portfolio_value_cad, decision_date,
            raw_dir, points=60):
    """Two fixed mixes over the provided history. Returns JSON-ready series and summary figures."""
    wide = load_history(str(raw_dir))
    tickers = sorted((set(weights_before) | set(weights_after)) - {CASH})
    rets = _returns(wide, tickers, decision_date)
    if len(rets) < 250:
        raise ValueError('Not enough shared price history to compare the two mixes')
    start_value = float(portfolio_value_cad)
    before = _mix_curve(rets, weights_before, start_value)
    after = _mix_curve(rets, weights_after, start_value)
    step = max(1, len(before) // points)
    sample = before.index[::step].union([before.index[-1]])
    series = [{'date': d.date().isoformat(), 'before': round(float(before.loc[d]), 2),
               'after': round(float(after.loc[d]), 2)} for d in sample]
    dd_b, dd_b_date = _max_drawdown(before)
    dd_a, dd_a_date = _max_drawdown(after)
    gap = after - before
    worst_gap_date = gap.idxmin()
    return {
        'kind': 'historical_comparison',
        'window': {'start': before.index[0].date().isoformat(), 'end': before.index[-1].date().isoformat(),
                   'trading_days': int(len(rets))},
        'start_value_cad': start_value,
        'series': series,
        'end_value_cad': {'before': round(float(before.iloc[-1]), 2), 'after': round(float(after.iloc[-1]), 2)},
        'max_drawdown': {'before': dd_b, 'before_date': dd_b_date, 'after': dd_a, 'after_date': dd_a_date},
        'largest_gap_cad': {'date': worst_gap_date.date().isoformat(), 'after_minus_before': round(float(gap.min()), 2)},
        'trade_amount_cad': float(trade_amount_cad),
        'one_year_outcomes': {'before': _rolling_year_outcomes(before, trade_amount_cad),
                              'after': _rolling_year_outcomes(after, trade_amount_cad)},
        'method': ('Each mix held at constant weights (rebalanced daily), CAD adjusted closes from the '
                   'provided prices, cash at 0%, no fees or taxes. One-year outcomes use every 12-month '
                   'window starting weekly inside the history. Past results, not a forecast.'),
        'limitations': ['The mix did not exist in the past; this applies today\'s weights backward.',
                        'The 31-asset universe was chosen in 2026 (survivorship bias).',
                        'Five years of history: one rate cycle and one drawdown (2022).'],
    }

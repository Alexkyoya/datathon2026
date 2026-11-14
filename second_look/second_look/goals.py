"""Goal coverage (liquidity in the financial-planning sense). Pure and deterministic, no I/O.

Money needed by a date only counts if the asset holding it, in its worst fall in the provided price
window, recovered in less time than is left before the date. Counted value = market value x
(1 + that worst fall): what would be left after a repeat of it. A historical stress, not a forecast.
"""
import math
import re
from datetime import date

TOL_CAD = 1e-6
CASH = 'CASH_CAD'


def add_years(day, years):
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def months_between(start, end, days_per_month):
    return (end - start).days / days_per_month


def expand_goals(goals, decision_date, policy):
    """Goals -> dated installments within the planning horizon, plus a summary of later ones."""
    gl = policy['goal_liquidity']
    today = date.fromisoformat(decision_date)
    horizon_end = add_years(today, int(gl['planning_horizon_years']))
    inflation = policy['inflation_assumption']
    checked, later = [], {}
    for goal in goals:
        if goal['type'] == 'one_off':
            dated = [(date.fromisoformat(goal['target_date']), float(goal['amount_cad']), None)]
        else:
            start = date.fromisoformat(goal['start_date'])
            base = max(0.0, float(goal['amount_cad_per_year']) - float(goal.get('funded_by_other_income_cad_per_year', 0)))
            if 'years' in goal:
                days = [add_years(start, i) for i in range(goal['years'])]
            else:
                end, days, i = date.fromisoformat(goal['end_date']), [], 0
                while add_years(start, i) < end:
                    days.append(add_years(start, i))
                    i += 1
            dated = []
            for i, d in enumerate(days):
                factor = (1 + inflation) ** ((d - today).days / 365.25) if goal['indexed_to_inflation'] else 1.0
                dated.append((d, base * factor, i + 1))
        for day, amount, n in dated:
            if day < today:
                continue  # already paid; not money the portfolio still has to provide
            item = {'goal': goal['name'], 'type': goal['type'], 'priority': goal['priority'],
                    'installment': n, 'date': day.isoformat(), 'amount_cad': amount,
                    'months_to_goal': months_between(today, day, gl['days_per_month']),
                    'source': goal.get('source')}
            if day <= horizon_end:
                checked.append(item)
            else:
                s = later.setdefault(goal['name'], {'goal': goal['name'], 'installments': 0, 'amount_cad': 0.0,
                                                    'first_date': item['date']})
                s['installments'] += 1
                s['amount_cad'] += amount
    checked.sort(key=lambda x: (x['date'], x['goal'], x['installment'] or 0))
    return checked, list(later.values()), horizon_end.isoformat()


def asset_liquidity(ticker, assets):
    """(usable_after_months or None, counted factor, reason). None = never counts toward a dated goal."""
    if ticker == CASH:
        return 0.0, 1.0, None
    asset = assets[ticker]
    if asset['asset_class'] == 'cash':
        return 0.0, 1.0, None
    h = asset.get('horizon')
    if not h or h.get('max_drawdown') is None:
        return None, 0.0, (h or {}).get('reason') or 'no worst-fall / recovery data in assets.json'
    if not h.get('recovered') or h.get('recovery_months') is None:
        return None, 0.0, 'not back to its earlier peak by the end of the price window'
    return float(h['recovery_months']), 1.0 + float(h['max_drawdown']), None


def allocate(installments, holdings_cad, assets):
    """Fund installments earliest first, each from the most liquid usable assets first.
    Each dollar of an asset is used once. Returns per-installment counted value and sources."""
    pool = []
    for ticker, value in holdings_cad.items():
        if value <= TOL_CAD:
            continue
        months, factor, _ = asset_liquidity(ticker, assets)
        if months is not None and factor > 0:
            pool.append([(-1.0 if ticker == CASH else months, ticker), ticker, months, factor, float(value)])
    pool.sort(key=lambda p: p[0])
    out = []
    for item in installments:
        need, counted, used = item['amount_cad'], 0.0, []
        for p in pool:
            if need - counted <= TOL_CAD:
                break
            _, ticker, months, factor, left = p
            if months > item['months_to_goal'] + 1e-9 or left <= TOL_CAD:
                continue
            take_value = min(left, (need - counted) / factor)
            p[4] -= take_value
            counted += take_value * factor
            used.append({'ticker': ticker, 'market_value_cad': take_value, 'counted_cad': take_value * factor})
        out.append({'counted_cad': counted, 'coverage': 1.0 if need <= TOL_CAD else min(1.0, counted / need),
                    'holdings_used': used})
    return out


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _months_text(m):
    return f'{m / 12:.1f} years' if m >= 24 else f'{m:.0f} months'


def goal_check(client, before, after, assets, policy, decision_date, trade):
    """Coverage of every goal installment before and after the trade, and the alerts it creates."""
    goals = client.get('goals') or []
    if not goals:
        return {'applied': False, 'note': 'Client has no goals; goal coverage not applied.',
                'installments': [], 'later': []}, [], []
    installments, later, horizon_end = expand_goals(goals, decision_date, policy)
    cov_b, cov_a = allocate(installments, before, assets), allocate(installments, after, assets)
    red_within = policy['goal_liquidity']['red_within_months']
    checks, unresolved, rows = [], [], []
    cum_b = cum_a = 0.0
    months, factor, reason = asset_liquidity(trade['ticker'], assets)
    traded = assets[trade['ticker']]
    no_data = not traded.get('horizon') or traded['horizon'].get('max_drawdown') is None
    if trade['side'] == 'buy' and traded['asset_class'] != 'cash' and no_data:
        unresolved.append({'rule': 'goal_coverage.' + trade['ticker'], 'handling_key': 'goal_horizon_data',
                           'reason': f"No worst-fall data for {trade['ticker']} ({reason}); its value is not "
                                     'counted toward your goals.'})
    for item, b, a in zip(installments, cov_b, cov_a):
        cum_b += b['counted_cad']
        cum_a += a['counted_cad']
        flagged = cum_a < cum_b - TOL_CAD and a['coverage'] < b['coverage'] - 1e-9
        improved = cum_a > cum_b + TOL_CAD
        severity = 'none'
        if flagged:
            severity = 'red' if item['priority'] == 'essential' and item['months_to_goal'] <= red_within else 'yellow'
        label = item['goal'] + (f" ({item['date'][:4]})" if item['installment'] else '')
        row = dict(item, coverage_before=b['coverage'], coverage_after=a['coverage'],
                   counted_before=b['holdings_used'], counted_after=a['holdings_used'],
                   flagged=flagged, severity=severity)
        rows.append(row)
        if not (flagged or improved):
            continue
        effect = ('creates' if b['coverage'] >= 1 - 1e-9 else 'worsens') if flagged else 'improves'
        why = ''
        if flagged and trade['side'] == 'buy':
            h = assets[trade['ticker']].get('horizon') or {}
            if months is None:
                why = (f"{trade['ticker']} has not recovered from its worst past fall ({h['max_drawdown']:.0%})."
                       if h.get('max_drawdown') is not None else '')
            elif months > item['months_to_goal']:
                why = (f"{trade['ticker']} once took {_months_text(months)} to recover from its worst past "
                       f"fall; this money is needed in {_months_text(item['months_to_goal'])}.")
            else:
                why = f"{trade['ticker']} would be worth {factor:.0%} of its value after a repeat of its worst past fall."
        message = (f"{label}: {a['coverage']:.0%} covered after the trade instead of {b['coverage']:.0%} "
                   f"(CAD {item['amount_cad']:,.0f} needed by {item['date']}). {why}").strip()
        checks.append({'rule': f"goal.{slug(item['goal'])}.{item['date']}", 'kind': 'goal', 'limit': 1.0,
                       'bound': 'min', 'before': b['coverage'], 'after': a['coverage'],
                       'gap_before': max(0.0, 1 - b['coverage']), 'gap_after': max(0.0, 1 - a['coverage']),
                       'effect': effect, 'severity': severity, 'resolved': False, 'unit': 'coverage',
                       'source': 'stored_client_goal; policy.goal_liquidity (Second Look team assumption)',
                       'goal': item['goal'], 'priority': item['priority'], 'date': item['date'],
                       'months_to_goal': item['months_to_goal'], 'message': message, 'why': why})
    summary = {'applied': True, 'decision_date': decision_date, 'checked_until': horizon_end,
               'planning_horizon_years': policy['goal_liquidity']['planning_horizon_years'],
               'red_within_months': red_within, 'inflation_assumption': policy['inflation_assumption'],
               'installments': rows, 'later': later,
               'flagged': [r for r in rows if r['flagged']],
               'method': ('Worst past fall and recovery time per asset, provided prices '
                          f"{next((a['horizon']['window'] for a in assets.values() if a.get('horizon')), 'n/a')}; "
                          'historical stress, not a forecast. Later installments are long-term money.')}
    return summary, checks, unresolved

"""Pure deterministic rules. All weights are fractions, not percentages.

No I/O here: every rule reads only the client, assets.json and policy.json passed in, so a new
client file or a new provided asset works without code changes.
"""
import math
from .contracts import (CLASSES, SCHEMA_VERSION, SUPPORTED_PREFERENCES, not_in_universe, number,
                        validate_client, validate_request)
from .esg import esg_check
from .goals import goal_check
from .spectrum import classify_transition, load_references

EPS = 1e-10
LABELS = {'cash': 'Cash', 'fixed_income': 'Fixed income', 'equities': 'Equities', 'alternatives': 'Alternatives'}


def metrics(values, assets):
    total = math.fsum(values.values())
    if total <= 0:
        raise ValueError('Portfolio value must be positive')
    weights = {t: v/total for t,v in values.items()}
    classes = dict.fromkeys(CLASSES, 0.0)
    sectors, unknown = {}, 0.0
    for ticker, weight in weights.items():
        if ticker == 'CASH_CAD':
            classes['cash'] += weight
            continue
        asset = assets[ticker]
        classes[asset['asset_class']] += weight
        if asset['asset_class'] == 'equities' and weight > EPS:
            exposures = asset['sector_weights']
            if exposures is None:
                unknown += weight
            else:
                if not math.isclose(sum(exposures.values()), 1, abs_tol=EPS):
                    raise ValueError(f'Sector weights must sum to 1 for {ticker}')
                for sector, share in exposures.items():
                    number(share, 'sector share')
                    if share > 1:
                        raise ValueError('Sector share > 1')
                    sectors[sector] = sectors.get(sector, 0) + weight*share
    # One cash bucket combines cash ledger and cash-like funds; other funds remain positions.
    hhi = classes['cash']**2 + math.fsum(w*w for t,w in weights.items()
        if t != 'CASH_CAD' and assets[t]['asset_class'] != 'cash')
    crypto = math.fsum(w for t,w in weights.items() if t != 'CASH_CAD' and assets[t]['is_crypto'])
    return {'total':total,'positions':weights,'classes':classes,'sectors':sectors,
            'unknown_sector_weight':unknown,'crypto':crypto,'hhi':hhi}


def message(c):
    """Plain-language line for an allocation / concentration check (goal and ESG rules write their own)."""
    parts = c['rule'].split('.') + ['']
    name = {'allocation': f"{LABELS.get(parts[1], parts[1])} share",
            'single_stock': f"{'.'.join(parts[1:])} position", 'crypto': 'Crypto share',
            'hhi': 'Concentration (HHI)', 'sector': f"{'.'.join(parts[1:])} sector share",
            'liquid_cash_reserve': 'Cash reserve'}.get(parts[0], c['rule'])
    fmt = (lambda v: f'{v:.3f}') if c['unit'] == 'hhi_index' else (lambda v: f'{v:.1%}')
    bound = 'maximum' if c['bound'] == 'max' else 'minimum'
    return f"{name}: {fmt(c['before'])} before, {fmt(c['after'])} after; {bound} {fmt(c['limit'])}."


def evaluate_trade(request, client, holdings_cad, assets, policy, references=None):
    """Simulate one trade funded from CASH_CAD. Does not mutate inputs or execute."""
    validate_request(request)
    if request['client_id'] != client['client_id']:
        raise ValueError('Client mismatch')
    profile = client['profile']
    limits = policy['profiles'][profile]
    ticker, side = request['ticker'], request['side']
    if ticker not in assets:
        raise not_in_universe(ticker, assets)
    if side not in ('buy','sell'):
        raise ValueError('Unsupported side')
    validate_client(client, request['decision_date'], assets)
    preferences = client['preferences']
    amount = number(request['amount_cad'], 'amount_cad')
    if amount <= 0:
        raise ValueError('amount_cad must be positive')
    before = {t:number(v, f'holding {t}') for t,v in holdings_cad.items()}
    if 'CASH_CAD' not in before:
        raise ValueError('A cash ledger is required; missing cash is not zero cash')
    if set(before)-set(assets)-{'CASH_CAD'}:
        raise not_in_universe(sorted(set(before)-set(assets)-{'CASH_CAD'})[0], assets)
    if any(a['asset_class'] not in CLASSES for a in assets.values()):
        raise ValueError('Unknown asset class')
    if any(a['is_crypto'] and a['asset_class'] != 'alternatives' for a in assets.values()):
        raise ValueError('Crypto must be mapped to alternatives')
    after = dict(before)
    after.setdefault('CASH_CAD', 0.0)
    after.setdefault(ticker, 0.0)
    if side == 'buy':
        if amount > after['CASH_CAD'] + EPS:
            raise ValueError('Insufficient cash; cash-like funds are not automatically sold')
        after['CASH_CAD'] -= amount
        after[ticker] += amount
    else:
        if amount > after[ticker] + EPS:
            raise ValueError('Insufficient holdings; short sales are unsupported')
        after[ticker] -= amount
        after['CASH_CAD'] += amount
    after = {t:max(0,v) for t,v in after.items()}
    b, a = metrics(before,assets), metrics(after,assets)
    if not math.isclose(b['total'], a['total'], rel_tol=1e-12):
        raise AssertionError('Trade must conserve value before fees')
    return assess_transition(request, client, before, after, assets, policy, references)


def assess_transition(request, client, before, after, assets, policy, references=None):
    """Assess a tool-built portfolio transition, including an explicit cash contribution.

    Callers construct/validate the holdings; this function never funds or executes a trade.
    The ordinary single-trade verdict semantics are preserved.
    """
    profile = client['profile']
    limits = policy['profiles'][profile]
    preferences = client['preferences']
    ticker, side, amount = request['ticker'], request['side'], request['amount_cad']
    b, a = metrics(before, assets), metrics(after, assets)
    checks, unresolved, notes = [], [], []

    def unresolved_item(rule, key, reason):
        handling = policy['unresolved_handling']
        unresolved.append({'rule': rule, 'reason': reason, 'handling': handling.get(key, handling['default']),
                           'source': 'policy.unresolved_handling'})

    if set(preferences)-SUPPORTED_PREFERENCES:
        unresolved_item('preferences', 'unsupported_preference',
                        'Unsupported stored preferences: '+', '.join(sorted(set(preferences)-SUPPORTED_PREFERENCES)))

    def check(rule, old, new, limit, bound='max', hard=False, near=False, unit='weight', source='team_assumption', old_limit=None):
        old_bound = limit if old_limit is None else old_limit
        old_gap = max(0, old-old_bound) if bound=='max' else max(0, old_bound-old)
        new_gap = max(0, new-limit) if bound=='max' else max(0, limit-new)
        old_gap = 0.0 if old_gap <= EPS else old_gap
        new_gap = 0.0 if new_gap <= EPS else new_gap
        if new_gap > old_gap+EPS:
            effect = 'creates' if old_gap == 0 else 'worsens'
        elif new_gap < old_gap-EPS:
            effect = 'improves'
        else:
            effect = 'unchanged'
        severity = 'none'
        if new_gap > 0:
            severity = 'red' if effect in ('creates','worsens') and (hard or new_gap > policy['yellow_buffer']+EPS) else 'yellow'
        elif near and abs(new-limit) <= policy['yellow_buffer']+EPS:
            severity = 'yellow'
        checks.append({'rule':rule,'kind':'limit','limit':limit,'bound':bound,'before':old,'after':new,
            'gap_before':old_gap,'gap_after':new_gap,'effect':effect,'severity':severity,
            'resolved':old_gap>0 and new_gap==0,'unit':unit,'source':source})

    for cls in CLASSES:
        low, high = limits['asset_class_ranges'][cls]
        check(f'allocation.{cls}.min',b['classes'][cls],a['classes'][cls],low,'min',near=low>0,
              source=policy['allocation_source'])
        check(f'allocation.{cls}.max',b['classes'][cls],a['classes'][cls],high,near=high<1,
              source=policy['allocation_source'])
    for t in sorted(set(before)|set(after)):
        if t!='CASH_CAD' and assets[t]['is_single_stock']:
            check('single_stock.'+t,b['positions'].get(t,0),a['positions'].get(t,0),limits['single_stock_max'],hard=True)
    check('crypto',b['crypto'],a['crypto'],limits['crypto_max'],hard=True)
    check('hhi',b['hhi'],a['hhi'],policy['hhi_max'],hard=True,unit='hhi_index')
    if policy['hhi_watch']-EPS <= a['hhi'] <= policy['hhi_max']+EPS:
        checks[-1]['severity'] = 'yellow'

    if limits['sector_max'] is not None:
        if a['unknown_sector_weight'] > b['unknown_sector_weight']+EPS:
            unresolved_item('sector_lookthrough.'+ticker, 'sector_lookthrough',
                            f'{ticker} has no sector breakdown in our data, so the sector limit cannot be checked for it.')
        elif max(a['unknown_sector_weight'], b['unknown_sector_weight']) > EPS:
            notes.append('Some holdings have no sector breakdown; sector figures are a lower bound.')
        for sector in sorted(set(a['sectors'])|set(b['sectors'])):
            check('sector.'+sector,b['sectors'].get(sector,0),a['sectors'].get(sector,0),limits['sector_max'])
    if 'minimum_cash_cad' in preferences:
        reserve = number(preferences['minimum_cash_cad'],'minimum_cash_cad')
        check('liquid_cash_reserve',before.get('CASH_CAD',0)/b['total'],after['CASH_CAD']/a['total'],
              reserve/a['total'],'min',hard=True,source='stored_client_constraint',old_limit=reserve/b['total'])

    # Rule B (ESG preferences) and rule A (goal coverage): only creating or worsening is flagged.
    trade = {'ticker': ticker, 'side': side, 'amount_cad': amount}
    esg_summary, esg_checks, esg_unresolved = esg_check(preferences, policy, assets, trade, b, a)
    goal_summary, goal_checks, goal_unresolved = goal_check(client, before, after, assets, policy,
                                                            request['decision_date'], trade)
    checks.extend(esg_checks + goal_checks)
    for item in esg_unresolved + goal_unresolved:
        unresolved_item(item['rule'], item['handling_key'], item['reason'])
    if not goal_summary['applied']:
        notes.append(goal_summary['note'])
    if preferences.get('esg_required', False) and not esg_summary['applied']:
        unresolved_item('preferences.esg_required', 'esg_required_unscreened',
                        'An ESG preference is not a verified screen. Supply explicit exclusions or a sourced screening dataset.')
    if preferences.get('other_constraints'):
        unresolved_item('preferences.other_constraints', 'other_constraints',
                        'Other stored constraints require explicit machine-readable rules.')

    for c in checks:
        c.setdefault('message', message(c))
    worsened = any(c['effect'] in ('creates','worsens') for c in checks)
    improved = any(c['effect']=='improves' for c in checks)
    unresolved_yellow = any(u['handling']=='yellow' for u in unresolved)
    if any(c['severity']=='red' for c in checks):
        verdict = 'red'
    elif any(u['handling']=='manual_review' for u in unresolved):
        verdict = None
    elif improved and not worsened and not unresolved_yellow:
        verdict = 'green'
    elif unresolved_yellow or any(c['severity']=='yellow' for c in checks):
        verdict = 'yellow'
    else:
        verdict = 'green'
    effect = 'mixed' if improved and worsened else ('improves' if improved else
        'creates_or_worsens' if worsened else 'unchanged')
    alerts = [c for c in checks if c['severity']!='none']
    breaches = [c for c in checks if c['gap_before']>0 or c['gap_after']>0]
    reasons = [c['message'] for c in alerts]
    if verdict=='green' and improved:
        reasons.insert(0,'Existing deviation reduced with no new or worsened breach; remaining breaches are reported.')
    if not reasons:
        reasons.append('All evaluated limits respected, with no near-limit warning.')
    reasons.extend(u['reason'] for u in unresolved)
    # Classification is explanatory and cannot participate in verdict selection.
    reference_config = load_references() if references is None else references
    classification = classify_transition(b['classes'], a['classes'], profile,
                                          reference_config, assets, policy)
    result = {'schema_version':SCHEMA_VERSION,'policy_version':policy['version'],
        'reference_portfolio_version':reference_config['version'],
        'classification':classification,
        'client_id':client['client_id'],'decision_date':request['decision_date'],'profile':profile,
        'verdict':verdict,'evaluation_status':'partial' if unresolved else 'complete',
        'action':'manual_review' if verdict is None else {'green':'pass_simulation','yellow':'confirm_or_cancel','red':'pause_and_ask_reason'}[verdict],
        'activate_agents':verdict=='red','effect':effect,'reasons':reasons,
        'breaches':breaches,'alerts':alerts,'checks':checks,
        'goals':goal_summary,'esg':esg_summary,
        'hhi_before':b['hhi'],'hhi_after':a['hhi'],
        'weights_before':b['classes'],'weights_after':a['classes'],
        'position_weights_before':b['positions'],'position_weights_after':a['positions'],
        'holdings_cad_before':before,'holdings_cad_after':after,'portfolio_value_cad':a['total'],
        'context_for_agents':{k:client.get(k) for k in ('horizon_years','objectives','loss_tolerance_fraction',
                                                       'preferences','goals','income')},
        'context_only':['Horizon, objectives and loss tolerance do not change the assigned profile or verdict without an explicit constraint.','Historical drawdown is not a guaranteed future loss or a profile classifier.'],
        'coverage':{'unresolved':unresolved,'notes':notes,'unknown_sector_weight_before':b['unknown_sector_weight'],
                    'unknown_sector_weight_after':a['unknown_sector_weight']},
        'assumptions':policy['assumptions'],
        'disclaimer':'Fictional clients; simulated trades only; no investment advice.'}
    result.update(full_compliance(result, client, assets, policy))
    return result


def full_compliance(result, client, assets, policy):
    """Report every residual limit, independent of whether the trade improved it."""
    residual = [dict(c) for c in result['checks'] if c['gap_after'] > EPS]
    known = {c['rule'] for c in residual}
    # Goal/ESG alerts deliberately omit unchanged breaches; compliance must include them.
    for row in result['goals'].get('installments', []):
        rule = 'goal.' + row['goal'] + '.' + row['date']
        if row['coverage_after'] < 1-EPS and not any(
                c.get('goal') == row['goal'] and c.get('date') == row['date'] for c in residual):
            residual.append({'rule': rule, 'after': row['coverage_after'], 'limit': 1.0,
                             'bound': 'min', 'unit': 'coverage', 'source': 'stored_client_goal'})
    after = metrics(result['holdings_cad_after'], assets)
    empty = dict(after, positions={}, sectors={})
    unresolved = list(result['coverage']['unresolved'])
    for ticker, weight in after['positions'].items():
        if ticker == 'CASH_CAD' or weight <= EPS:
            continue
        _, checks, missing = esg_check(client['preferences'], policy, assets,
                                      {'ticker': ticker, 'side': 'buy'}, empty, after)
        unresolved.extend(missing)
        for check in checks:
            if check['gap_after'] > EPS and check['rule'] not in known:
                residual.append(check); known.add(check['rule'])
    if after['unknown_sector_weight'] > EPS and policy['profiles'][client['profile']]['sector_max'] is not None:
        unresolved.append({'rule': 'sector_lookthrough', 'reason': 'Some held funds have no sector breakdown.'})
    return {'portfolio_within_limits': False if residual else (None if unresolved else True),
            'residual_breaches': residual, 'compliance_unresolved': unresolved}

"""Pure deterministic rules. All weights are fractions, not percentages."""
import math
from .contracts import CLASSES, SCHEMA_VERSION, number, validate_request
from .spectrum import classify_transition, load_references

EPS = 1e-10


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
    # One cash bucket combines cash ledger and ZST; other funds remain positions.
    hhi = classes['cash']**2 + math.fsum(w*w for t,w in weights.items()
        if t != 'CASH_CAD' and assets[t]['asset_class'] != 'cash')
    crypto = math.fsum(w for t,w in weights.items() if t != 'CASH_CAD' and assets[t]['is_crypto'])
    return {'total':total,'positions':weights,'classes':classes,'sectors':sectors,
            'unknown_sector_weight':unknown,'crypto':crypto,'hhi':hhi}


def evaluate_trade(request, client, holdings_cad, assets, policy, references=None):
    """Simulate one trade funded from CASH_CAD. Does not mutate inputs or execute."""
    validate_request(request)
    if request['client_id'] != client['client_id']:
        raise ValueError('Client mismatch')
    profile = client['profile']
    limits = policy['profiles'][profile]
    number(client['horizon_years'], 'horizon_years')
    tolerance = number(client['loss_tolerance_fraction'], 'loss_tolerance_fraction')
    if tolerance > 1 or not isinstance(client['objectives'], list):
        raise ValueError('Invalid stored loss tolerance or objectives')
    preferences = client['preferences']
    if not isinstance(preferences, dict):
        raise ValueError('preferences must be an object')
    for key in ('excluded_tickers','excluded_sectors'):
        if key in preferences and (not isinstance(preferences[key], list) or
                any(not isinstance(v,str) for v in preferences[key])):
            raise ValueError(f'{key} must be a list of strings')
    ticker, side = request['ticker'], request['side']
    if ticker not in assets or side not in ('buy','sell'):
        raise ValueError('Unknown asset or unsupported side')
    amount = number(request['amount_cad'], 'amount_cad')
    if amount <= 0:
        raise ValueError('amount_cad must be positive')
    before = {t:number(v, f'holding {t}') for t,v in holdings_cad.items()}
    if 'CASH_CAD' not in before:
        raise ValueError('A cash ledger is required; missing cash is not zero cash')
    if set(before)-set(assets)-{'CASH_CAD'}:
        raise ValueError('Unmapped holding')
    if any(a['asset_class'] not in CLASSES for a in assets.values()):
        raise ValueError('Unknown asset class')
    if any(a['is_crypto'] and a['asset_class'] != 'alternatives' for a in assets.values()):
        raise ValueError('Crypto must be mapped to alternatives')
    after = dict(before)
    after.setdefault('CASH_CAD', 0.0)
    after.setdefault(ticker, 0.0)
    if side == 'buy':
        if amount > after['CASH_CAD'] + EPS:
            raise ValueError('Insufficient cash; ZST.TO is not automatically liquidated')
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
    checks, unresolved = [], []
    supported_preferences = {'excluded_tickers', 'excluded_sectors', 'minimum_cash_cad',
                             'esg_required', 'other_constraints'}
    if set(preferences)-supported_preferences:
        unresolved.append('Unsupported stored preferences: '+', '.join(sorted(set(preferences)-supported_preferences)))
    if 'esg_required' in preferences and not isinstance(preferences['esg_required'], bool):
        raise ValueError('esg_required must be boolean')

    def check(rule, old, new, limit, bound='max', hard=False, near=False, unit='weight', source='team_assumption'):
        old_gap = max(0, old-limit) if bound=='max' else max(0, limit-old)
        new_gap = max(0, new-limit) if bound=='max' else max(0, limit-new)
        old_gap = 0.0 if old_gap <= EPS else old_gap
        new_gap = 0.0 if new_gap <= EPS else new_gap
        if new_gap > old_gap+EPS:
            effect = 'creates' if old_gap == 0 else 'worsens'
        elif new_gap < old_gap-EPS:
            effect = 'reduces'
        else:
            effect = 'unchanged'
        severity = 'none'
        if new_gap > 0:
            severity = 'red' if effect in ('creates','worsens') and (hard or new_gap > policy['yellow_buffer']+EPS) else 'yellow'
        elif near and abs(new-limit) <= policy['yellow_buffer']+EPS:
            severity = 'yellow'
        checks.append({'rule':rule,'limit':limit,'bound':bound,'before':old,'after':new,
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

    needs_sectors = limits['sector_max'] is not None or bool(preferences.get('excluded_sectors'))
    if needs_sectors and max(a['unknown_sector_weight'],b['unknown_sector_weight']) > EPS:
        unresolved.append('ETF sector look-through missing; direct sector exposure is a lower bound only.')
    if limits['sector_max'] is not None:
        for sector in sorted(set(a['sectors'])|set(b['sectors'])):
            check('sector.'+sector,b['sectors'].get(sector,0),a['sectors'].get(sector,0),limits['sector_max'])
    for t in preferences.get('excluded_tickers',[]):
        if t not in assets:
            raise ValueError(f'Unknown excluded ticker: {t}')
        check('excluded_ticker.'+t,b['positions'].get(t,0),a['positions'].get(t,0),0,hard=True,source='stored_client_constraint')
    known_sectors = {s for v in assets.values() for s in (v.get('sector_weights') or {})}
    for sector in preferences.get('excluded_sectors',[]):
        if sector not in known_sectors:
            raise ValueError(f'Unknown sector exclusion: {sector}')
        check('excluded_sector.'+sector,b['sectors'].get(sector,0),a['sectors'].get(sector,0),0,hard=True,source='stored_client_constraint')
    if preferences.get('esg_required',False):
        unresolved.append('An ESG preference is not a verified screen. Supply explicit exclusions or a sourced screening dataset.')
    if preferences.get('other_constraints'):
        unresolved.append('Other stored constraints require explicit machine-readable rules.')
    if 'minimum_cash_cad' in preferences:
        reserve = number(preferences['minimum_cash_cad'],'minimum_cash_cad')
        check('liquid_cash_reserve',before.get('CASH_CAD',0)/b['total'],after['CASH_CAD']/a['total'],
              reserve/b['total'],'min',hard=True,source='stored_client_constraint')

    worsened = any(c['effect'] in ('creates','worsens') for c in checks)
    improved = any(c['effect']=='reduces' for c in checks)
    if any(c['severity']=='red' for c in checks):
        verdict = 'red'
    elif unresolved:
        verdict = None
    elif improved and not worsened:
        verdict = 'green'
    elif any(c['severity']=='yellow' for c in checks):
        verdict = 'yellow'
    else:
        verdict = 'green'
    effect = 'mixed' if improved and worsened else ('reduces' if improved else
        'creates_or_worsens' if worsened else 'unchanged')
    alerts = [c for c in checks if c['severity']!='none']
    breaches = [c for c in checks if c['gap_before']>0 or c['gap_after']>0]
    reasons = [f"{c['rule']}: {c['before']:.6f} -> {c['after']:.6f}; {c['bound']} {c['limit']:.6f}; {c['effect']}" for c in alerts]
    if verdict=='green' and improved:
        reasons.insert(0,'Existing deviation reduced with no new or worsened breach; remaining breaches are reported.')
    if not reasons:
        reasons.append('All evaluated limits respected, with no near-limit warning.')
    reasons.extend(unresolved)
    # Classification is explanatory and cannot participate in verdict selection.
    reference_config = load_references() if references is None else references
    classification = classify_transition(b['classes'], a['classes'], profile,
                                          reference_config, assets, policy)
    return {'schema_version':SCHEMA_VERSION,'policy_version':policy['version'],
        'reference_portfolio_version':reference_config['version'],
        'classification':classification,
        'client_id':client['client_id'],'decision_date':request['decision_date'],'profile':profile,
        'verdict':verdict,'evaluation_status':'partial' if unresolved else 'complete',
        'action':'manual_review' if verdict is None else {'green':'pass_simulation','yellow':'confirm_or_cancel','red':'pause_and_ask_reason'}[verdict],
        'activate_agents':verdict=='red','effect':effect,'reasons':reasons,
        'breaches':breaches,'alerts':alerts,'checks':checks,
        'hhi_before':b['hhi'],'hhi_after':a['hhi'],
        'weights_before':b['classes'],'weights_after':a['classes'],
        'position_weights_before':b['positions'],'position_weights_after':a['positions'],
        'holdings_cad_before':before,'holdings_cad_after':after,'portfolio_value_cad':a['total'],
        'context_for_agents':{k:client[k] for k in ('horizon_years','objectives','loss_tolerance_fraction','preferences')},
        'context_only':['Horizon, objectives and loss tolerance do not change the assigned profile or verdict without an explicit constraint.','Historical drawdown is not a guaranteed future loss or a profile classifier.'],
        'coverage':{'unresolved':unresolved,'unknown_sector_weight_before':b['unknown_sector_weight'],
                    'unknown_sector_weight_after':a['unknown_sector_weight']},
        'assumptions':policy['assumptions'],
        'disclaimer':'Fictional clients; simulated trades only; no investment advice.'}

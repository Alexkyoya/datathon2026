"""Deterministic, unfunded contribution comparisons over pinned assessment inputs."""
import copy
import math
import uuid
from .application import digest
from .contracts import number
from .rules import assess_transition
from .service import value_holdings

PLAN_FIELDS = {'amount', 'currency', 'expected_timing', 'certainty', 'cash_use', 'purchases'}


def simulate_market_scenario(record, inputs, name, shocks):
    """Apply client-specified decimal shocks, with no invented price forecast."""
    assets = inputs['assets']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200 or not isinstance(shocks, dict) or not shocks:
        raise ValueError('A scenario name and explicit hypothetical shocks are required')
    allowed = set(assets) | {'cash', 'fixed_income', 'equities', 'alternatives'}
    if set(shocks)-allowed or any(isinstance(v, bool) or not isinstance(v, (int, float)) or
                                 not math.isfinite(v) or v < -1 for v in shocks.values()):
        raise ValueError('Shocks must map provided tickers/classes to finite decimal returns >= -1')
    after = {t: value*(1+shocks.get(t, shocks.get('cash' if t == 'CASH_CAD' else assets[t]['asset_class'], 0)))
             for t, value in record['result']['holdings_cad_after'].items()}
    result = assess_transition(record['request'], inputs['client'], record['result']['holdings_cad_before'],
                               after, assets, inputs['policy'], inputs['references'])
    return {'scenario_id': 'scenario_'+uuid.uuid4().hex, 'kind': 'market_shock', 'name': name,
            'data_status': 'hypothetical', 'as_of': record['request']['decision_date'],
            'current_assessment_id': record['simulation_id'], 'result': result,
            'input_version': digest({'base': record['assessment_input_version'], 'shocks': shocks}),
            'assumptions': {'shocks': shocks, 'method': 'Instant CAD value shocks; no forecast, fees or taxes. Other prices and policy frozen.'}}


def contribution_plan(previous, changes):
    if not isinstance(changes, dict) or set(changes)-PLAN_FIELDS:
        raise ValueError('Unknown contribution field')
    plan = copy.deepcopy(previous)
    plan.update(changes)
    if 'amount' in plan and number(plan['amount'], 'contribution amount') <= 0:
        raise ValueError('Contribution must be positive')
    if 'currency' in plan and plan['currency'] not in ('CAD', 'USD'):
        raise ValueError('Only CAD or USD contributions can be valued by this snapshot')
    for key in ('expected_timing', 'certainty'):
        if key in plan and (not isinstance(plan[key], str) or not 0 < len(plan[key].strip()) <= 300):
            raise ValueError(f'A user-stated {key} is required')
    if 'cash_use' in plan and plan['cash_use'] not in ('cash', 'specified_purchases'):
        raise ValueError('cash_use must be cash or specified_purchases')
    if 'purchases' in plan:
        if not isinstance(plan['purchases'], list) or len(plan['purchases']) > 15:
            raise ValueError('Specify at most 15 purchases')
        for purchase in plan['purchases']:
            if not isinstance(purchase, dict) or set(purchase) != {'ticker', 'amount_cad'}:
                raise ValueError('Each client-specified purchase needs ticker and amount_cad')
            if not isinstance(purchase['ticker'], str) or number(purchase['amount_cad'], 'purchase amount_cad') <= 0:
                raise ValueError('Purchase must be positive')
    missing = [key for key in PLAN_FIELDS-{'purchases'} if key not in plan]
    if plan.get('cash_use') == 'specified_purchases' and not plan.get('purchases'):
        missing.append('purchases')
    if plan.get('cash_use') == 'cash':
        plan['purchases'] = []
    return plan, sorted(missing)


def simulate_contribution(record, inputs, plan):
    plan, missing = contribution_plan({}, plan)
    if missing:
        raise ValueError('Contribution details missing: '+', '.join(missing))
    req = record['request']
    assets, policy, snapshot, client, refs = (inputs[k] for k in ('assets', 'policy', 'snapshot', 'client', 'references'))
    # Validate *every* quote used for a planned purchase; retain the original date.
    _, quotes = value_holdings(client, snapshot, assets, policy, req['decision_date'],
                              [req['ticker']] + [p['ticker'] for p in plan['purchases']])
    fx, fx_date = 1.0, req['decision_date']
    if plan['currency'] == 'USD':
        # Use the same snapshot's validated USD/CAD rate, never a model conversion.
        usd = next((t for t, a in assets.items() if a['currency'] == 'USD' and t in snapshot['quotes']), None)
        if usd is None:
            raise ValueError('USD/CAD conversion unavailable')
        _, fx_quotes = value_holdings(client, snapshot, assets, policy, req['decision_date'], [usd])
        fx = number(fx_quotes[usd]['fx_to_cad'], 'FX')
        fx_date = fx_quotes[usd]['fx_source_date']
    contribution_cad = plan['amount'] * fx
    after = copy.deepcopy(record['result']['holdings_cad_after'])
    after['CASH_CAD'] += contribution_cad
    for p in plan['purchases']:
        amount = number(p['amount_cad'], 'purchase amount_cad')
        if amount > after['CASH_CAD'] + 1e-8:
            raise ValueError('Planned purchases exceed hypothetical available cash')
        after['CASH_CAD'] -= amount
        after[p['ticker']] = after.get(p['ticker'], 0) + amount
    result = assess_transition(req, client, record['result']['holdings_cad_before'], after, assets, policy, refs)
    scenario_id = 'scenario_' + uuid.uuid4().hex
    assumptions = {
        'pricing_as_of': req['decision_date'], 'prices_and_fx': 'Held fixed at the original historical snapshot; not a forecast.',
        'fees_and_taxes': 'Excluded from the simulation.',
        'timing': 'Original trade first, then contribution, then only the client-specified purchases.',
        'goal_dates': 'Goal runway and policy are held at the original decision date; not a future revaluation.',
        'before_arrival_or_if_delayed': 'The current trade assessment remains applicable until cash actually arrives.',
        'certainty': plan['certainty'], 'expected_timing': plan['expected_timing'],
        'fx_to_cad': fx, 'fx_source_date': fx_date}
    return {'scenario_id': scenario_id, 'kind': 'future_contribution', 'data_status': 'hypothetical',
            'current_assessment_id': record['simulation_id'], 'as_of': req['decision_date'],
            'input_version': digest({'base': record['assessment_input_version'], 'plan': plan}),
            'plan': plan, 'contribution_cad': contribution_cad, 'result': result,
            'assumptions': assumptions, 'quotes': quotes,
            'before_contribution': {'assessment_id': record['simulation_id'], 'result': record['result']}}

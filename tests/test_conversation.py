"""Real Python rules/storage, scripted model boundaries. These are not live LLM scores."""
import copy
import importlib.util
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from second_look.application import FileStore, S3Store, SecondLookApplication, StateConflict
from second_look.conversation import ConversationService, evidence, render_answer
from second_look.scenarios import simulate_contribution

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT/'second_look_conversation_kit'


@pytest.fixture
def setup(tmp_path):
    store = FileStore(tmp_path)
    comp = ROOT/'second_look'
    for name in ('assets', 'policy', 'reference_portfolios'):
        store.save(f'config/{name}.json', json.loads((comp/f'config/{name}.json').read_text()))
    # Reuse the deterministic snapshot builder, rather than loading fixture numbers as market data.
    from second_look.__main__ import make_snapshot
    from second_look.cleaning import clean_data
    import pandas as pd
    aligned, report = clean_data(pd.read_csv(comp/'data/raw/prices_daily.csv'),
        pd.read_csv(comp/'data/raw/fx_usdcad_daily.csv'), store.load('config/assets.json'),
        store.load('config/policy.json')['data_quality'])
    report.update(sources=[], source_sha256={})
    snapshot = make_snapshot(aligned, report, '2026-11-13')
    store.save('snapshots/2026-11-13.json', snapshot)
    client = json.loads((comp/'examples/client.json').read_text())
    store.save('clients/demo_balanced.json', client)
    app = SecondLookApplication(store)
    record = app.evaluate({'client_id': 'demo_balanced', 'decision_date': '2026-11-13',
                           'ticker': 'TSLA', 'side': 'buy', 'amount_cad': 12000})
    model = SimpleNamespace(max_tool_calls=3, initial=Mock(return_value={
        'specialist_reports': {'profile': 'Scripted profile report', 'news': 'Scripted news report', 'data': 'Scripted data report'},
        'explanation': {'why_it_matters': 'Scripted initial explanation'}, 'evidence': {}}),
        respond=Mock(return_value={'assistant_message': 'Your reason adds context. The numerical assessment stays separate.', 'evidence_refs': ['current']}),
        reference=Mock(return_value={'cards': []}), news=Mock(return_value={'items': []}))
    service = ConversationService(store, model)
    return SimpleNamespace(store=store, app=app, record=record, model=model, service=service, snapshot=snapshot)


def send(s, text='What does this flag mean?', mid='message_1', revision=0):
    return s.service.send(s.record['simulation_id'], {'client_id': s.record['request']['client_id'],
        'input_version': s.record['assessment_input_version'], 'message_id': mid,
        'expected_revision': revision, 'message': text})


def test_restoration_isolation_initial_reports_run_once_and_exact_inputs(setup):
    s = setup
    first = send(s)
    inputs = s.store.load(f"assessment_inputs/{s.record['simulation_id']}.json")
    client = s.store.load('clients/demo_balanced.json');client['profile'] = 'Growth'
    s.store.save('clients/demo_balanced.json', client)
    restored = ConversationService(FileStore(s.store.root), s.model)
    s.service = restored
    second = send(s, 'Explain the term concentration.', 'message_2', 1)
    assert len(second['conversation']['messages']) == 4
    assert second['record']['result'] == first['record']['result']
    assert s.model.initial.call_count == 1
    assert s.model.respond.call_args.args[1]['client'] == inputs['client']
    assert second['conversation']['session_id'] == first['conversation']['session_id']
    with pytest.raises(ValueError, match='another client'):
        restored.get(s.record['simulation_id'], 'robert', s.record['assessment_input_version'])
    other = s.app.evaluate(s.record['request'])
    assert restored.get(other['simulation_id'], 'demo_balanced', other['assessment_input_version'])['conversation']['messages'] == []


def test_duplicate_message_replay_and_stale_input_rejected(setup):
    s = setup
    first = send(s)
    assert send(s) == first
    assert s.model.respond.call_count == 1
    with pytest.raises(StateConflict, match='different content'):
        send(s, 'different content')
    with pytest.raises(StateConflict, match='Conversation changed'):
        send(s, 'next', 'message_2', 0)
    payload = {'client_id': 'demo_balanced', 'input_version': 'old', 'message_id': 'new', 'expected_revision': 1, 'message': 'test'}
    with pytest.raises(StateConflict, match='inputs changed'):
        s.service.send(s.record['simulation_id'], payload)


def test_parallel_submission_claim_is_persisted_not_process_memory(setup):
    s = setup
    started, finish = threading.Event(), threading.Event()
    def respond(*args):
        started.set();assert finish.wait(3)
        return {'assistant_message': 'A scripted reply.', 'evidence_refs': []}
    s.model.respond.side_effect = respond
    results = []
    thread = threading.Thread(target=lambda: results.append(send(s)))
    thread.start();assert started.wait(3)
    try:
        with pytest.raises(StateConflict, match='still being prepared'):
            ConversationService(FileStore(s.store.root), s.model).send(s.record['simulation_id'], {
                'client_id': 'demo_balanced', 'input_version': s.record['assessment_input_version'],
                'message_id': 'other', 'expected_revision': 1, 'message': 'another message'})
    finally:
        finish.set();thread.join()
    assert len(results) == 1
    assert s.model.initial.call_count == 1


def test_failed_model_preserves_message_and_reuses_initial_reports_on_retry(setup):
    s = setup
    s.model.respond.side_effect = RuntimeError('model unavailable')
    failed = send(s)
    assert failed['conversation']['status'] == 'unavailable'
    assert [m['role'] for m in failed['conversation']['messages']] == ['user']
    s.model.respond.side_effect = None
    retried = send(s)
    assert retried['conversation']['status'] == 'ready'
    assert len(retried['conversation']['messages']) == 2
    assert s.model.initial.call_count == 1


@pytest.mark.parametrize('bad', [
    {'assistant_message': 'The position is 99%.', 'evidence_refs': ['current']},
    {'assistant_message': 'An answer.', 'evidence_refs': ['made_up']},
    {'assistant_message': 'An answer.', 'evidence_refs': [], 'verdict': 'green'},
    {'assistant_message': 'The position is [[current:/profile|percent]].', 'evidence_refs': ['current']},
])
def test_unsupported_figures_refs_and_model_owned_state_are_never_displayed(setup, bad):
    s = setup;s.model.respond.return_value = bad
    response = send(s)
    assert response['conversation']['status'] == 'unavailable'
    assert len(response['conversation']['messages']) == 1
    assert response['record']['result']['verdict'] == 'red'


def test_numeric_placeholders_preserve_exact_tool_identity(setup):
    s = setup
    answer = {'assistant_message': 'The position is [[current:/position_weights_after/TSLA|percent]] at [[current:/decision_date|date]].',
              'evidence_refs': ['current']}
    text, refs = render_answer(answer, {'current': evidence('current', s.record['result'], s.record)})
    assert '12.0%' in text and '2026-11-13' in text and refs == ['current']


def fixture_trade(s):
    # Fictional development fixture used only at this test boundary.
    fixture = json.loads((KIT/'numeric_fixtures.json').read_text())['fixtures'][0]
    client = dict(fixture['client'], fictional=True, cash_cad=fixture['original_holdings_cad']['CASH_CAD'],
                  holdings_as_of='2026-11-13', holdings={t: v/s.snapshot['quotes'][t]['close_cad']
                    for t, v in fixture['original_holdings_cad'].items() if t != 'CASH_CAD'})
    s.store.save('clients/training_example_only.json', client)
    return s.app.evaluate(fixture['candidate_request'])


def plan(amount=50000, purchases=None):
    return {'amount': amount, 'currency': 'CAD', 'expected_timing': 'next week', 'certainty': 'expected, not funded',
            'cash_use': 'specified_purchases' if purchases else 'cash', 'purchases': purchases or []}


def test_contributions_some_all_and_no_resolution_are_distinct_from_current(setup):
    s = setup;record = fixture_trade(s)
    inputs = s.store.load(f"assessment_inputs/{record['simulation_id']}.json")
    assert record['result']['verdict'] == 'red'
    cash = simulate_contribution(record, inputs, plan())
    # Current policy evaluates the whole transition; unlike the kit's older fixture,
    # increased cash can itself worsen a limit and keep this scenario red.
    assert cash['result']['portfolio_within_limits'] is False
    rules = {c['rule'] for c in cash['result']['residual_breaches']}
    assert {'allocation.cash.max', 'allocation.fixed_income.min'} <= rules
    allocated = simulate_contribution(record, inputs, plan(purchases=[{'ticker': t, 'amount_cad': 15000} for t in ['XBB.TO', 'AGG', 'TLT']]))
    assert allocated['result']['portfolio_within_limits'] is True
    assert allocated['result']['position_weights_after']['TSLA'] == pytest.approx(.1)
    tiny = simulate_contribution(record, inputs, plan(1000))
    assert tiny['result']['portfolio_within_limits'] is False
    assert any(c['rule'] == 'single_stock.TSLA' for c in tiny['result']['residual_breaches'])
    assert s.store.load(f"results/{record['simulation_id']}.json") == record
    assert cash['before_contribution']['result'] == record['result']
    from second_look.rules import evaluate_trade
    reduced = evaluate_trade(dict(record['request'], side='sell', amount_cad=1000), inputs['client'],
                             record['result']['holdings_cad_after'], inputs['assets'], inputs['policy'], inputs['references'])
    assert reduced['verdict'] == 'green'
    assert reduced['portfolio_within_limits'] is False


def test_partial_plan_restores_known_details_and_tool_budget(setup):
    s = setup
    def respond(record, inputs, state, tools):
        partial = tools.invoke('simulate_future_cash_plan', changes={'amount': 50000, 'currency': 'CAD'})
        assert partial['data']['missing'] == ['cash_use', 'certainty', 'expected_timing']
        tools.invoke('get_current_assessment')
        tools.invoke('get_portfolio_exposure')
        assert tools.invoke('retrieve_evidence')['data_status'] == 'unavailable'
        return {'assistant_message': 'When do you expect the contribution?', 'evidence_refs': []}
    s.model.respond.side_effect = respond
    response = send(s, 'I plan to add CAD 50,000.')
    assert response['conversation']['planned_contribution'] == {'amount': 50000, 'currency': 'CAD'}
    def follow(record, inputs, state, tools):
        assert state['planned_contribution']['amount'] == 50000
        result = tools.invoke('simulate_future_cash_plan', changes={'expected_timing': 'next week', 'certainty': 'bonus may not arrive', 'cash_use': 'cash'})
        assert result['data_status'] == 'hypothetical'
        return {'assistant_message': 'This remains conditional. Before arrival or if delayed, the current assessment applies.', 'evidence_refs': [result['tool_call_id']]}
    s.model.respond.side_effect = follow
    response = send(s, 'Expected next week, but my bonus may not arrive; leave it in cash.', 'message_2', 1)
    assert response['conversation']['scenarios'][0]['plan']['certainty'] == 'bonus may not arrive'
    assert s.model.initial.call_count == 1


def test_candidate_gets_new_assessment_id_without_changing_original(setup):
    s = setup
    def respond(record, inputs, state, tools):
        result = tools.invoke('recalculate_candidate', ticker='TSLA', side='buy', amount_cad=1000)
        assert result['data']['scenario_id'] != record['simulation_id']
        assert result['data']['input_version'] != record['assessment_input_version']
        return {'assistant_message': 'The comparison uses a separate assessment.', 'evidence_refs': [result['tool_call_id']]}
    s.model.respond.side_effect = respond
    response = send(s, 'Compare a CAD 1,000 buy instead.')
    assert response['record']['result'] == s.record['result']


def test_tool_failure_does_not_become_a_pass(setup):
    s = setup;s.model.news.side_effect = RuntimeError('unavailable')
    def respond(record, inputs, state, tools):
        result = tools.invoke('retrieve_evidence')
        assert result['data_status'] == 'unavailable'
        return {'assistant_message': 'The evidence check is unavailable. You can retry later.', 'evidence_refs': [result['tool_call_id']]}
    s.model.respond.side_effect = respond
    assert send(s)['record']['result'] == s.record['result']


def test_late_reply_does_not_reopen_a_cancelled_trade(setup):
    s = setup
    def respond(*args):
        s.app.decide(s.record['simulation_id'], 'cancel')
        return {'assistant_message': 'Late scripted reply.', 'evidence_refs': []}
    s.model.respond.side_effect = respond
    response = send(s)
    assert response['record']['stage'] == 'cancelled'
    assert len(response['conversation']['messages']) == 1
    assert response['conversation']['status'] == 'closed'


def test_confirmed_cash_uses_explicit_control_and_new_input_version(setup):
    s = setup
    def respond(record, inputs, state, tools):
        result = tools.invoke('simulate_future_cash_plan', changes=plan())
        return {'assistant_message': 'The scenario is conditional.', 'evidence_refs': [result['tool_call_id']]}
    s.model.respond.side_effect = respond
    first = send(s)
    scenario_id = first['conversation']['scenarios'][0]['scenario_id']
    payload = {'client_id': 'demo_balanced', 'input_version': s.record['assessment_input_version'],
               'scenario_id': scenario_id, 'event_id': 'cash_event_1', 'confirmed': True}
    fresh = s.service.confirm_contribution(s.record['simulation_id'], payload)['record']
    assert fresh['simulation_id'] != s.record['simulation_id']
    assert fresh['assessment_input_version'] != s.record['assessment_input_version']
    assert fresh['result']['holdings_cad_before']['CASH_CAD'] == pytest.approx(65000)
    assert fresh['result']['holdings_cad_before'].get('TSLA', 0) == 0  # no planned trade executed
    assert s.service.confirm_contribution(s.record['simulation_id'], payload)['record'] == fresh
    original = s.store.load(f"results/{s.record['simulation_id']}.json")
    assert original['result'] == s.record['result'] and original['superseded_by'] == fresh['simulation_id']
    with pytest.raises(StateConflict):s.app.decide(s.record['simulation_id'], 'proceed', risk_acknowledged=True)
    assert s.app.evaluate(s.record['request'])['result']['holdings_cad_before']['CASH_CAD'] == pytest.approx(65000)


def test_s3_conditional_writes_reject_concurrent_updates_and_restore():
    objects = {}
    class S3:
        def get_object(self, Bucket, Key):
            if Key not in objects:raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
            body, tag = objects[Key];return {'Body': io.BytesIO(body), 'ETag': tag}
        def put_object(self, Bucket, Key, Body, **kwargs):
            tag = objects.get(Key, (None, None))[1]
            if ('IfNoneMatch' in kwargs and tag is not None) or ('IfMatch' in kwargs and kwargs['IfMatch'] != tag):
                raise ClientError({'Error': {'Code': 'PreconditionFailed'}}, 'PutObject')
            new = str(int(tag or 0)+1);objects[Key] = (Body, new);return {'ETag': new}
    store = S3Store(S3(), 'demo', 'second-look')
    assert store.load_versioned('session.json') == (None, None)
    tag = store.compare_and_swap('session.json', {'revision': 1}, None)
    with pytest.raises(StateConflict):store.compare_and_swap('session.json', {'revision': 2}, None)
    assert S3Store(S3(), 'demo', 'second-look').load_versioned('session.json') == ({'revision': 1}, tag)


def test_runtime_loads_only_canonical_prompt_and_reference_content():
    spec = importlib.util.spec_from_file_location('chat_runtime_test', ROOT/'agents/conversation.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    coordinator = module.ConversationalCoordinator(SimpleNamespace())
    assert coordinator.prompt == (KIT/'system_prompt.txt').read_text()
    assert coordinator.reference(['C12'])['cards'][0]['conversation_design']['plausible_alternative_explanations']
    with pytest.raises(ValueError):coordinator.reference(['C16'])
    source = (ROOT/'agents/conversation.py').read_text()
    assert all(name not in source for name in ('numeric_fixtures.json', 'evaluation_cases.json', 'conversation_examples.json'))


def test_installed_strands_runs_real_tool_loop_and_restores_messages(setup):
    """Real SDK/executor, scripted model stream; no Bedrock request or behavioral score."""
    from strands.models.model import Model
    class ScriptedModel(Model):
        def __init__(self):self.calls=0;self.contexts=[]
        def update_config(self, **kwargs):pass
        def get_config(self):return {}
        async def structured_output(self, *args, **kwargs):
            raise NotImplementedError
            yield
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.calls += 1;self.contexts.append(copy.deepcopy(messages))
            yield {'messageStart': {'role': 'assistant'}}
            if self.calls == 1:
                yield {'contentBlockStart': {'contentBlockIndex': 0, 'start': {'toolUse': {
                    'toolUseId': 'plan_tool', 'name': 'simulate_future_cash_plan'}}}}
                delta = {'toolUse': {'input': json.dumps({'changes': {'amount': 50000, 'currency': 'CAD'}})}}
                stop = 'tool_use'
            else:
                delta = {'text': json.dumps({'assistant_message': 'When do you expect the contribution?', 'evidence_refs': []})}
                stop = 'end_turn'
            yield {'contentBlockDelta': {'contentBlockIndex': 0, 'delta': delta}}
            yield {'contentBlockStop': {'contentBlockIndex': 0}}
            yield {'messageStop': {'stopReason': stop}}
            yield {'metadata': {'usage': {'inputTokens': 0, 'outputTokens': 0, 'totalTokens': 0}, 'metrics': {'latencyMs': 0}}}
    spec = importlib.util.spec_from_file_location('chat_runtime_sdk_test', ROOT/'agents/conversation.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    model = ScriptedModel()
    coordinator = module.ConversationalCoordinator(SimpleNamespace(model=lambda: model))
    coordinator.select_reference_ids = Mock(return_value=[])
    coordinator.initial = setup.model.initial
    setup.service = ConversationService(setup.store, coordinator)
    first = send(setup, 'I expect to contribute CAD 50,000.')
    assert first['conversation']['status'] == 'ready'
    assert first['conversation']['planned_contribution']['amount'] == 50000
    assert model.calls == 2
    send(setup, 'Next Friday.', 'message_2', 1)
    assert coordinator.initial.call_count == 1
    assert len(model.contexts[-1]) >= 3
    assert 'I expect to contribute CAD 50,000.' in model.contexts[-1][0]['content'][0]['text']


def test_scenario_checks_residual_esg_goals_and_cash_reserve(setup):
    s = setup
    client = s.store.load('clients/demo_balanced.json')
    client['preferences'] = {'excluded_tickers': ['TSLA'], 'minimum_cash_cad': 100000}
    client['goals'] = [{'name': 'Essential need', 'type': 'one_off', 'amount_cad': 1000000,
                       'target_date': '2026-12-13', 'priority': 'essential'}]
    s.store.save('clients/demo_balanced.json', client)
    record = s.app.evaluate(s.record['request'])
    inputs = s.store.load(f"assessment_inputs/{record['simulation_id']}.json")
    result = simulate_contribution(record, inputs, plan(1000))['result']
    rules = {c['rule'] for c in result['residual_breaches']}
    assert 'esg.excluded_ticker.TSLA' in rules
    assert 'liquid_cash_reserve' in rules
    assert any(r.startswith('goal.') for r in rules)
    assert result['portfolio_within_limits'] is False


def test_usd_plan_uses_snapshot_fx_and_rejects_invented_or_unfunded_purchases(setup):
    s = setup;inputs = s.store.load(f"assessment_inputs/{s.record['simulation_id']}.json")
    usd = simulate_contribution(s.record, inputs, dict(plan(), currency='USD'))
    assert usd['contribution_cad'] == pytest.approx(50000*usd['assumptions']['fx_to_cad'])
    assert usd['assumptions']['fx_source_date'] <= s.record['request']['decision_date']
    with pytest.raises(ValueError, match='exceed'):
        simulate_contribution(s.record, inputs, plan(purchases=[{'ticker': 'SPY', 'amount_cad': 1000000}]))
    inputs['snapshot']['quotes']['SPY']['usable'] = False
    with pytest.raises(ValueError, match='Unusable'):
        simulate_contribution(s.record, inputs, plan(purchases=[{'ticker': 'SPY', 'amount_cad': 1000}]))


def test_market_event_uses_explicit_shock_without_changing_current_assessment(setup):
    from second_look.scenarios import simulate_market_scenario
    s = setup;inputs = s.store.load(f"assessment_inputs/{s.record['simulation_id']}.json")
    scenario = simulate_market_scenario(s.record, inputs, 'Hypothetical stock fall', {'TSLA': -.3})
    assert scenario['result']['holdings_cad_after']['TSLA'] == pytest.approx(8400)
    assert scenario['result']['portfolio_value_cad'] == pytest.approx(96400)
    assert s.store.load(f"results/{s.record['simulation_id']}.json") == s.record
    with pytest.raises(ValueError):simulate_market_scenario(s.record, inputs, 'bad', {'TSLA': -2})


def test_news_refresh_preserves_exact_sources_and_excludes_future_items():
    spec = importlib.util.spec_from_file_location('chat_runtime_news_test', ROOT/'agents/conversation.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    eligible = {'ticker': 'TSLA', 'source_name': 'Official filing', 'url': 'https://example.test/filing', 'published_date': '2026-11-12'}
    call = Mock(return_value={'items': [eligible, dict(eligible, published_date='2026-11-14'), dict(eligible, placeholder=True)]})
    coordinator = module.ConversationalCoordinator(SimpleNamespace(T=SimpleNamespace(call_tool=call)))
    assert coordinator.news('TSLA', '2026-11-13')['items'] == [eligible]
    call.assert_called_once_with('news_check', ticker='TSLA', decision_date='2026-11-13', record_trace=False)


def test_initial_specialists_are_bound_to_the_exact_account_weights_and_date(setup):
    s = setup;inputs = s.store.load(f"assessment_inputs/{s.record['simulation_id']}.json")
    spec = importlib.util.spec_from_file_location('chat_runtime_initial_test', ROOT/'agents/conversation.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls = Mock(return_value={'items': [], 'assets': {}})
    def review(ctx, rc, reason, shocks, profiles, news, data, alternative, on_unavailable=None):
        assert profiles[0]() ['data']['client'] == inputs['client']
        news[0]()
        data[0](['TSLA'], shocks=shocks)
        data[1]()
        assert alternative('SPY',1000)['data']['client_id'] == 'demo_balanced'
        return {'specialist_reports': {'profile': 'scripted', 'news': 'scripted', 'data': 'scripted'}}
    coordinator = module.ConversationalCoordinator(SimpleNamespace(T=SimpleNamespace(call_tool=calls), review_red=review))
    initial = coordinator.initial(s.record, inputs, 'My stated reason.')
    assert len(initial['evidence']) == 5
    risk = next(call.kwargs for call in calls.call_args_list if call.args[0] == 'risk_metrics')
    assert risk['weights']['TSLA'] == pytest.approx(.12)
    assert risk['weights']['CASH'] == pytest.approx(.03)
    assert 'client_id' not in risk and risk['decision_date'] == '2026-11-13'
    assert all(call.kwargs['record_trace'] is False for call in calls.call_args_list)


def test_observed_risk_wrapper_path_is_resolved_but_literal_figures_stay_blocked(setup):
    from second_look.diagnostics import AnswerValidationError
    facts = {'risk': evidence('risk', {'assets': {'BTC-USD': {'max_drawdown': -.7}}}, setup.record)}
    text, refs = render_answer({'assistant_message': 'Worst historical fall: [[risk:/data/assets/BTC-USD/max_drawdown|percent]].',
                              'evidence_refs': ['risk']}, facts)
    assert text == 'Worst historical fall: -70.0%.' and refs == ['risk']
    with pytest.raises(AnswerValidationError) as exc:
        render_answer({'assistant_message': 'A hypothetical 30% drop.', 'evidence_refs': ['risk']}, facts)
    assert exc.value.code == 'unreferenced_number'


@pytest.mark.parametrize('recover', [True, False])
def test_claude_format_repair_is_bounded_and_never_displays_invalid_output(setup, monkeypatch, recover):
    spec = importlib.util.spec_from_file_location('chat_runtime_repair_test', ROOT/'agents/conversation.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    bad = json.dumps({'assistant_message': 'The position is 99%.', 'evidence_refs': ['current']})
    good = json.dumps({'assistant_message': 'What part of that argument do you find convincing?', 'evidence_refs': []})
    outputs = iter([bad, good] if recover else [bad, bad, bad])
    prompts = []
    supplied_histories = []
    class FakeAgent:
        def __init__(self, **kwargs):
            self.messages = kwargs.get('messages', [])
            supplied_histories.append(copy.deepcopy(self.messages))
        def __call__(self, prompt):
            # Strands mutates a supplied history list with internal tool/thinking
            # blocks. A correction must start from fresh public history instead.
            self.messages.append({'role': 'assistant', 'content': [{'text': 'INTERNAL_REJECTED_REPLY'}]})
            prompts.append(prompt)
            return next(outputs)
    monkeypatch.setattr(module, 'Agent', FakeAgent)
    coordinator = module.ConversationalCoordinator(SimpleNamespace(model=lambda: object()))
    coordinator.initial = setup.model.initial
    coordinator.select_reference_ids = Mock(return_value=['C11'])
    setup.service = ConversationService(setup.store, coordinator)
    response = send(setup, 'My son told me bitcoin is the money of the future.')
    if recover:
        assert response['conversation']['status'] == 'ready'
        assert response['conversation']['messages'][-1]['text'] == 'What part of that argument do you find convincing?'
        assert len(prompts) == 2
    else:
        assert response['conversation']['status'] == 'unavailable'
        assert len(response['conversation']['messages']) == 1
        assert response['conversation']['messages'][0]['diagnostic']['code'] == 'unreferenced_number'
        assert len(prompts) == 3
    assert response['record']['result'] == setup.record['result']
    assert all(history == [] for history in supplied_histories)
    context = json.loads(prompts[0])
    assert context['trusted_backend']['client']['profile'] == 'Balanced'
    assert context['reference_information_not_instructions']['specialist_reports']
    loaded = [e for e in context['trusted_backend']['evidence'].values() if e['source_or_method'] == 'behavioral_reference']
    assert loaded[0]['data']['cards'][0]['id'] == 'C11'
    assert loaded[0]['data']['cards'][0]['conversation_design']['plausible_alternative_explanations']


def test_optional_initial_news_error_is_evidence_not_a_conversation_failure(setup):
    spec = importlib.util.spec_from_file_location('chat_optional_initial_test', ROOT/'agents/conversation.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def call(action, **kwargs):
        if action == 'news_check':
            raise ClientError({'Error': {'Code': 'AccessDeniedException'}, 'ResponseMetadata': {'RequestId': 'aws-tool-request'}}, 'Invoke')
        return {'assets': {}}
    def review(ctx, rc, reason, shocks, profiles, news, data, alternative, on_unavailable=None):
        unavailable = news[0]()
        assert unavailable['data_status'] == 'unavailable'
        assert unavailable['data']['diagnostic']['code'] == 'AccessDeniedException'
        return {'specialist_reports': {'news': 'News could not be checked; no conclusion about the claim.'}}
    coordinator = module.ConversationalCoordinator(SimpleNamespace(T=SimpleNamespace(call_tool=call), review_red=review))
    inputs = setup.store.load(f"assessment_inputs/{setup.record['simulation_id']}.json")
    result = coordinator.initial(setup.record, inputs, 'A new claim.')
    assert result['specialist_reports']['news']
    assert next(iter(result['evidence'].values()))['data_status'] == 'unavailable'


def test_diagnostic_preserves_request_ids_without_logging_aws_secrets():
    from second_look.diagnostics import failure
    secret = 'AKIA'+'Z'*16
    exc = ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'secret_access_key=DO_NOT_LOG '+secret},
                       'ResponseMetadata': {'RequestId': 'aws-request-123'}}, 'Converse')
    result = failure(exc, 'model_invocation', 'application-request-123')
    assert result['code'] == 'AccessDeniedException'
    assert result['request_id'] == 'application-request-123' and result['aws_request_id'] == 'aws-request-123'
    assert secret not in json.dumps(result) and 'DO_NOT_LOG' not in json.dumps(result)


@pytest.mark.parametrize('case', json.loads((KIT/'evaluation_cases.json').read_text())['cases'], ids=lambda c: c['id'])
def test_holdout_messages_pass_through_unchanged_without_affecting_account(setup, case):
    """Contract coverage for all hold-outs, deliberately no claim about model tone or reasoning."""
    response = send(setup, case['user_message'])
    assert response['conversation']['messages'][0]['text'] == case['user_message']
    assert response['record']['result'] == setup.record['result']
    assert setup.model.respond.call_args.args[2]['messages'][0]['text'] == case['user_message']


def test_zero_limit_is_said_plainly_never_as_a_percentage(setup):
    from second_look.diagnostics import AnswerValidationError
    facts = {'current': evidence('current', {'alerts': [{'rule': 'crypto', 'limit': 0.0, 'after': 0.02}],
                                            'limits': {'crypto_max': 0, 'single_stock_max': 0.05}}, setup.record)}
    for path in ('/alerts/0/limit', '/limits/crypto_max'):
        with pytest.raises(AnswerValidationError) as exc:
            render_answer({'assistant_message': f'Your profile sets a crypto maximum of [[current:{path}|percent]].',
                           'evidence_refs': ['current']}, facts)
        assert exc.value.code == 'zero_limit_as_figure'
    # Plain wording passes; a non-zero limit and the after-weight still render as figures.
    text, _ = render_answer({'assistant_message': "Your profile doesn't include crypto. This trade would make it "
                             "[[current:/alerts/0/after|percent]] of the portfolio; the single-stock cap is "
                             "[[current:/limits/single_stock_max|percent]].", 'evidence_refs': ['current']}, facts)
    assert '2.0%' in text and '5.0%' in text

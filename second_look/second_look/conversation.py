"""Persisted chat workflow. Models supply prose; backend code owns all state and figures."""
import copy
import json
import logging
import re
import time
import uuid
from .application import SecondLookApplication, StateConflict, digest, load_client, check_account_version
from .contracts import identifier
from .scenarios import contribution_plan, simulate_contribution, simulate_market_scenario
from .diagnostics import AnswerValidationError, emit, failure

log = logging.getLogger(__name__)
TERMINAL = ('cancelled', 'simulation_continued')


class LazyCoordinator:
    """Keep the standalone numerical package importable without AWS/Strands dependencies."""
    def __init__(self, project):
        self.project, self._coordinator = project, None

    def __getattr__(self, name):
        if self._coordinator is None:
            import importlib.util
            import sys
            from pathlib import Path
            path = Path(self.project)/'agents/conversation.py'
            if not path.is_file():
                raise RuntimeError('Install/run the full repository to enable Bedrock conversations')
            sys.path.insert(0, str(path.parent))
            spec = importlib.util.spec_from_file_location('sl_chat_coordinator', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._coordinator = module.ConversationalCoordinator()
        return getattr(self._coordinator, name)


def evidence(ref, data, record, status='verified_current', method='Python rule engine; pinned historical snapshot'):
    return {'tool_call_id': ref, 'source_or_method': method, 'as_of': record['request']['decision_date'],
            'input_version': record['assessment_input_version'], 'data_status': status, 'data': data}


def render_answer(answer, facts):
    """Resolve numeric placeholders from evidence. Reject invented IDs and literal figures."""
    if isinstance(answer, str):
        answer = answer.strip()
        if answer.startswith('```json') and answer.endswith('```'):
            answer = answer[7:-3].strip()
        try:
            answer = json.loads(answer)
        except json.JSONDecodeError:
            raise AnswerValidationError('Return one JSON object with assistant_message and evidence_refs.', 'invalid_json') from None
    if not isinstance(answer, dict) or set(answer) != {'assistant_message', 'evidence_refs'}:
        raise AnswerValidationError('Only assistant_message and evidence_refs are accepted.', 'invalid_response_fields')
    text, refs = answer['assistant_message'], answer['evidence_refs']
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 6000:
        raise AnswerValidationError('The message is empty or too long.', 'invalid_message_length')
    if text.count('?') > 1:
        raise AnswerValidationError('Ask at most one focused question. Answer the current message without repeating the warning.', 'too_many_questions')
    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in facts for r in refs):
        raise AnswerValidationError('An evidence ID is not available in this session.', 'unknown_evidence')
    token = re.compile(r'\[\[([A-Za-z0-9_-]+):(/[^\[\]|]*)\|(percent|cad|number|date)\]\]')
    if re.search(r'\d|[%$]', token.sub('', text)):
        raise AnswerValidationError('A literal figure is not linked to evidence. Use a valid placeholder or omit the figure.', 'unreferenced_number')

    def replace(match):
        ref, path, style = match.groups()
        if ref not in refs or ref not in facts or facts[ref]['data_status'] == 'unavailable':
            raise AnswerValidationError('This numerical reference has no available evidence.', 'unavailable_evidence')
        value = facts[ref]['data']
        parts = path[1:].split('/')
        # References are relative to evidence.data, not its outer metadata wrapper.
        # Accept one redundant /data prefix when the actual body has no such key.
        if parts[0] == 'data' and isinstance(value, dict) and 'data' not in value:
            parts = parts[1:]
        try:
            for part in parts:
                part = part.replace('~1', '/').replace('~0', '~')
                if isinstance(value, list) and not re.fullmatch(r'0|[1-9]\d*', part):
                    raise ValueError('Invalid list index')
                value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            raise AnswerValidationError('The JSON pointer does not resolve. Paths start inside the evidence data body.', 'invalid_evidence_path') from None
        if style == 'date':
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value[:10]):
                raise AnswerValidationError('This reference is not a date.', 'invalid_evidence_type')
            return value[:10]
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise AnswerValidationError('This reference is not a number.', 'invalid_evidence_type')
        return {'percent': lambda: f'{value:.1%}', 'cad': lambda: f'CAD {value:,.2f}',
                'number': lambda: f'{value:,.4f}'.rstrip('0').rstrip('.')}[style]()
    rendered = token.sub(replace, text)
    if '[[' in rendered or ']]' in rendered:
        raise AnswerValidationError('Use complete evidence placeholders.', 'invalid_placeholder')
    return rendered, list(dict.fromkeys(refs))


class ConversationService:
    def __init__(self, store, coordinator):
        self.store, self.coordinator = store, coordinator

    def _scope(self, sid, client_id, input_version):
        record = self.store.load(f'results/{identifier(sid)}.json')
        if record['request']['client_id'] != identifier(client_id):
            raise ValueError('This trade belongs to another client')
        if input_version != record.get('assessment_input_version'):
            raise StateConflict('The trade inputs changed. Check the trade again.')
        if record['result']['verdict'] != 'red':
            raise ValueError('Conversations start through a red trade assessment')
        key = f'conversations/{client_id}/{sid}/{identifier(input_version)}.json'
        return record, key

    def _load(self, record, key):
        state, etag = self.store.load_versioned(key)
        if state is None:
            state = {'session_id': uuid.uuid4().hex, 'client_id': record['request']['client_id'],
                     'trade_id': record['simulation_id'], 'input_version': record['assessment_input_version'],
                     'revision': 0, 'messages': [], 'status': 'ready', 'initial_analysis': None,
                     'planned_contribution': {}, 'scenarios': [], 'tool_trace': [],
                     'evidence': {'current': evidence('current', record['result'], record)}}
        return state, etag

    def public(self, record, state):
        refs = {r for m in state['messages'] for r in m.get('evidence_refs', [])}
        return {'record': record, 'conversation': {
            k: copy.deepcopy(state[k]) for k in ('session_id', 'client_id', 'trade_id', 'input_version',
                'revision', 'messages', 'status', 'planned_contribution', 'scenarios')},
            'evidence': [copy.deepcopy(v) for k, v in state['evidence'].items() if k in refs],
            'current_assessment_id': record['simulation_id'], 'current_verdict': record['result']['verdict'],
            'trade_execution_state': record['stage'],
            'scenario_verdicts': {s['scenario_id']: s['result']['verdict'] for s in state['scenarios']}}

    def get(self, sid, client_id, input_version):
        record, key = self._scope(sid, client_id, input_version)
        state, _ = self._load(record, key)
        return self.public(record, state)

    def send(self, sid, payload, request_id=None):
        if not isinstance(payload, dict) or set(payload) != {
                'client_id', 'input_version', 'message_id', 'expected_revision', 'message'}:
            raise ValueError('Chat requires client_id, input_version, message_id, expected_revision and message')
        mid, message = identifier(payload['message_id']), payload['message']
        if not isinstance(message, str) or not 1 <= len(message.strip()) <= 5000:
            raise ValueError('Enter a message of 1 to 5000 characters')
        record, key = self._scope(sid, payload['client_id'], payload['input_version'])
        state, etag = self._load(record, key)
        old = next((m for m in state['messages'] if m['id'] == mid and m['role'] == 'user'), None)
        if old and old['text'] != message.strip():
            raise StateConflict('This message ID was already used for different content')
        if old and old['status'] == 'completed':
            return self.public(record, state)  # lost HTTP response, no second model call
        if state['status'] == 'busy' and state.get('lease_until', 0) > time.time():
            raise StateConflict('A reply is still being prepared. Reload or retry shortly.')
        if record['stage'] in TERMINAL:
            raise StateConflict('This simulation is finished. Its history remains available.')
        if record.get('superseded_by'):
            raise StateConflict('Account state changed. Use the newer assessment.')
        check_account_version(self.store, record)
        if old is None:
            if state['messages'] and state['messages'][-1].get('status') == 'failed':
                raise StateConflict('Retry the saved message before sending another message')
            if isinstance(payload['expected_revision'], bool) or payload['expected_revision'] != state['revision']:
                raise StateConflict('Conversation changed. Reload before sending this message.')
            if len(state['messages']) >= 80:
                raise ValueError('This demo session reached its turn limit. History is preserved; start a new trade check.')
            state['messages'].append({'id': mid, 'role': 'user', 'text': message.strip(), 'status': 'pending',
                                      'created_at': time.time()})
            state['revision'] += 1
        else:
            old['status'] = 'pending'
            old.pop('error', None)
            old.pop('diagnostic', None)
        state['status'], state['lease_until'] = 'busy', time.time()+660
        etag = self.store.compare_and_swap(key, state, etag)
        request_id = request_id or uuid.uuid4().hex
        stage = 'context_loading'
        turn_record = dict(record, _chat_request_id=request_id)
        emit('chat_request', request_id, trade_id=sid, client_id=record['request']['client_id'],
             message_id=mid, input_version=payload['input_version'], restored_messages=len(state['messages'])-1)

        def checkpoint():
            nonlocal etag
            etag = self.store.compare_and_swap(key, state, etag)

        try:
            inputs = self.store.load(f'assessment_inputs/{sid}.json')
            if digest(record['result']['input_sha256']) != payload['input_version']:
                raise StateConflict('Assessment version does not match its inputs')
            if not state['initial_analysis']:
                stage = 'initial_analysis'
                state['initial_analysis'] = self.coordinator.initial(turn_record, inputs, message.strip())
                # Persist expensive reports before the first conversational reply, even if that reply fails.
                for ref, item in state['initial_analysis'].get('evidence', {}).items():
                    state['evidence'][ref] = item
                checkpoint()
            stage = 'model_invocation'
            tools = ConversationTools(self.store, turn_record, inputs, state, self.coordinator, checkpoint, mid)
            answer = self.coordinator.respond(turn_record, inputs, state, tools)
            stage = 'response_validation'
            text, refs = render_answer(answer, state['evidence'])
            current = self.store.load(f'results/{sid}.json')
            check_account_version(self.store, current)
            if current['stage'] in TERMINAL or current.get('superseded_by'):
                state['status'] = 'closed'
                next(m for m in state['messages'] if m['id'] == mid)['status'] = 'completed'
                checkpoint()
                return self.public(current, state)
            state['messages'].append({'id': 'reply_'+mid, 'role': 'assistant', 'text': text,
                                      'evidence_refs': refs, 'created_at': time.time()})
            next(m for m in state['messages'] if m['id'] == mid)['status'] = 'completed'
            state['status'] = 'ready'
            emit('chat_completed', request_id, trade_id=sid, message_id=mid,
                 current_verdict=record['result']['verdict'], evidence_refs=refs)
        except StateConflict:
            raise
        except Exception as exc:
            diagnostic = failure(exc, 'response_validation' if isinstance(exc, AnswerValidationError) else stage, request_id)
            emit('chat_failed', request_id, diagnostic=diagnostic, trade_id=sid, message_id=mid)
            state['status'] = 'unavailable'
            item = next(m for m in state['messages'] if m['id'] == mid)
            item['status'] = 'failed'
            item['diagnostic'] = diagnostic
            description = ('The AI reply could not be verified.' if isinstance(exc, AnswerValidationError) else
                           'The AI model could not reply.' if stage in ('initial_analysis', 'model_invocation') else
                           'The saved context could not be loaded.')
            item['error'] = description+' Your message is saved. Retry this message. Reference: '+request_id
        checkpoint()
        return self.public(self.store.load(f'results/{sid}.json'), state)

    def confirm_contribution(self, sid, payload):
        """Explicit simulation control, deliberately absent from the model's tools.

        Confirms cash only, at the fixed historical snapshot. Future purchases stay unexecuted.
        Creates a new assessment and preserves the original result and history.
        """
        if not isinstance(payload, dict) or set(payload) != {
                'client_id', 'input_version', 'scenario_id', 'event_id', 'confirmed'} or payload['confirmed'] is not True:
            raise ValueError('Explicit simulated-account confirmation is required')
        record, key = self._scope(sid, payload['client_id'], payload['input_version'])
        state, _ = self._load(record, key)
        account_key = f"accounts/{payload['client_id']}.json"
        account, version = self.store.load_versioned(account_key)
        event_id = identifier(payload['event_id'])
        if account and account.get('event_id') == event_id:
            if account['source_trade_id'] != sid or account['source_scenario_id'] != payload['scenario_id']:
                raise StateConflict('Confirmation ID was used for another plan')
            return {'record': self.store.load(f"results/{account['assessment_id']}.json")}
        if record['stage'] in TERMINAL or record.get('superseded_by') or state['status'] == 'busy':
            raise StateConflict('Wait for the reply to finish and use the active assessment')
        scenario = next((s for s in state['scenarios'] if s['scenario_id'] == payload['scenario_id']
                         and s['kind'] == 'future_contribution'), None)
        if not scenario:
            raise ValueError('Unknown contribution scenario')
        inputs = self.store.load(f'assessment_inputs/{sid}.json')
        current_client = account['client'] if account else load_client(self.store, payload['client_id'])
        if digest(current_client) != digest(inputs['client']):
            raise StateConflict('Account changed since this scenario. Check the trade again.')
        client = copy.deepcopy(current_client)
        client['cash_cad'] += scenario['contribution_cad']
        new_id = uuid.uuid4().hex
        updated = dict(inputs, client=client)
        # Evaluate first; this remains an unexecuted simulation even if persistence fails.
        fresh = SecondLookApplication(self.store).evaluate(record['request'], new_id, trusted_inputs=updated)
        self.store.compare_and_swap(account_key, {'client': client, 'event_id': event_id,
            'source_trade_id': sid, 'source_scenario_id': scenario['scenario_id'], 'assessment_id': new_id,
            'confirmation': 'Explicit fictional cash-state update at the pinned historical snapshot. No purchases executed.'}, version)
        latest, tag = self.store.load_versioned(f'results/{sid}.json')
        latest['superseded_by'] = new_id
        self.store.compare_and_swap(f'results/{sid}.json', latest, tag)
        return {'record': fresh}


class ConversationTools:
    """Bound to one client/trade; no execution, profile editing or cash transfers exposed."""
    def __init__(self, store, record, inputs, state, coordinator, checkpoint, message_id):
        self.store, self.record, self.inputs, self.state = store, record, inputs, state
        self.coordinator, self.checkpoint, self.message_id = coordinator, checkpoint, message_id
        self.request_id = record.get('_chat_request_id', '')
        self.calls, self.cards = 0, set()

    def invoke(self, name, **kwargs):
        self.calls += 1
        if self.calls > self.coordinator.max_tool_calls:
            return {'data_status': 'unavailable', 'error': 'Tool budget reached. Explain the remaining gap.'}
        ref = 'tool_' + uuid.uuid4().hex
        status = 'verified_current'
        try:
            if name == 'get_current_assessment':
                return self.state['evidence']['current']
            if name == 'get_portfolio_exposure':
                data = {k: self.record['result'][k] for k in ('weights_before', 'weights_after',
                    'position_weights_after', 'holdings_cad_after', 'residual_breaches', 'portfolio_within_limits')}
            elif name == 'behavioral_reference':
                ids = kwargs['card_ids']
                if not isinstance(ids, list) or len(ids) > 2 or len(set(ids)|self.cards) > 2:
                    raise ValueError('Use at most two relevant knowledge cards per turn')
                data = self.coordinator.reference(ids)
                self.cards.update(ids)
            elif name == 'retrieve_evidence':
                ticker = kwargs.get('ticker') or self.record['request']['ticker']
                if ticker not in self.inputs['assets']:
                    raise ValueError('Ticker is outside the provided universe')
                data = self.coordinator.news(ticker, self.record['request']['decision_date'])
            elif name == 'recalculate_candidate':
                req = dict(self.record['request'], **kwargs)
                candidate = SecondLookApplication(self.store).evaluate(req, trusted_inputs=self.inputs)
                data = {'scenario_id': candidate['simulation_id'], 'kind': 'candidate_trade',
                        'input_version': candidate['assessment_input_version'], 'data_status': 'hypothetical',
                        'as_of': req['decision_date'], 'request': req, 'result': candidate['result'],
                        'assumptions': {'pricing': 'Same pinned historical prices and account; no execution.'}}
                self.state['scenarios'].append(data)
                status = 'hypothetical'
            elif name == 'simulate_future_cash_plan':
                plan, missing = contribution_plan(self.state['planned_contribution'], kwargs['changes'])
                self.state['planned_contribution'] = plan
                status = 'user_stated'
                data = {'plan': plan, 'missing': missing, 'next_missing_field': missing[0] if missing else None,
                        'instruction': 'Ask about only next_missing_field this turn. Leave other missing values unknown.',
                        'source_message_id': self.message_id}
                if not missing:
                    data = simulate_contribution(self.record, self.inputs, plan)
                    data['source_message_id'] = self.message_id
                    self.state['scenarios'].append(data)
                    status = 'hypothetical'
            elif name == 'simulate_market_scenario':
                data = simulate_market_scenario(self.record, self.inputs, kwargs['name'], kwargs['shocks'])
                self.state['scenarios'].append(data)
                status = 'hypothetical'
            else:
                raise ValueError('Unknown conversation tool')
            if isinstance(data, dict) and data.get('error'):
                status = 'unavailable'
        except Exception as exc:
            status = 'unavailable'
            diagnostic = failure(exc, name, self.request_id)
            emit('optional_tool_unavailable', self.request_id, diagnostic=diagnostic, message_id=self.message_id)
            data = {'error': 'This tool is unavailable. Continue using verified context and explain the missing check.',
                    'diagnostic': diagnostic}
        result = evidence(ref, data, self.record, status, name)
        self.state['evidence'][ref] = result
        self.state['tool_trace'].append({'name': name, 'input': kwargs, 'evidence_ref': ref, 'message_id': self.message_id})
        self.checkpoint()
        return result

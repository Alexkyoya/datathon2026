"""Shared local/AWS workflow. Trusted profiles and numerical verdicts stay fixed."""
import copy
import hashlib
import json
import logging
import uuid
from pathlib import Path
from .contracts import SCHEMA_VERSION, identifier, validate_request
from .service import evaluate_from_snapshot

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class FileStore:
    def __init__(self, root):
        self.root = Path(root)

    def _path(self, key):
        path = (self.root/key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError('Invalid storage path')
        return path

    def load(self, key):
        return json.loads(self._path(key).read_text())

    def save(self, key, value):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
        temporary.replace(path)


class ProjectStore(FileStore):
    """Generated state lives in runtime; source configuration is read in place."""
    def __init__(self, root):
        self.project = Path(root)
        super().__init__(self.project/'data/runtime')

    def load(self, key):
        if key in ('config/assets.json', 'config/policy.json', 'config/reference_portfolios.json'):
            return json.loads((self.project/key).read_text())
        if key == 'clients/demo_balanced.json':
            return json.loads((self.project/'examples/client.json').read_text())
        if key.startswith('clients/') and (self.project/'examples'/key).is_file():
            # team personas (robert, mateo, amira) written by scripts/make_persona_clients.py
            return json.loads((self.project/'examples'/key).read_text())
        return super().load(key)


class S3Store:
    def __init__(self, s3, bucket, prefix):
        self.s3, self.bucket, self.prefix = s3, bucket, prefix.strip('/')

    def _key(self, key):
        return '/'.join(filter(None, (self.prefix, key)))

    def load(self, key):
        return json.loads(self.s3.get_object(Bucket=self.bucket, Key=self._key(key))['Body'].read())

    def save(self, key, value):
        self.s3.put_object(Bucket=self.bucket, Key=self._key(key),
                           Body=json.dumps(value, sort_keys=True, allow_nan=False).encode(),
                           ContentType='application/json')


class SecondLookApplication:
    def __init__(self, store, dispatch_agents=None):
        self.store = store
        self.dispatch_agents = dispatch_agents

    def evaluate(self, request, simulation_id=None):
        validate_request(request)
        client = self.store.load(f"clients/{request['client_id']}.json")
        snapshot = self.store.load(f"snapshots/{request['decision_date']}.json")
        assets = self.store.load('config/assets.json')
        policy = self.store.load('config/policy.json')
        references = self.store.load('config/reference_portfolios.json')
        result = evaluate_from_snapshot(request, client, snapshot, assets, policy, references)
        sid = identifier(simulation_id or uuid.uuid4().hex)
        stage = {None: 'manual_review', 'green': 'simulation_continued',
                 'yellow': 'awaiting_confirmation', 'red': 'awaiting_reason'}[result['verdict']]
        record = {'schema_version': SCHEMA_VERSION, 'simulation_id': sid,
                  'request': copy.deepcopy(request), 'result': result,
                  'stage': stage, 'agent_status': 'not_requested'}
        self.store.save(f'results/{sid}.json', record)
        logger.info(json.dumps({'step': 'rule_check', 'simulation_id': sid,
                               'verdict': result['verdict'], 'stage': stage,
                               'policy_version': result['policy_version'],
                               'reference_portfolio_version': result['reference_portfolio_version'],
                               'result_key': f'results/{sid}.json',
                               'result_sha256': hashlib.sha256(json.dumps(record,sort_keys=True,allow_nan=False).encode()).hexdigest()}))
        return record

    def decide(self, simulation_id, decision, reason=None):
        sid = identifier(simulation_id)
        record = self.store.load(f'results/{sid}.json')
        stage = record['stage']
        if stage not in ('awaiting_confirmation', 'awaiting_reason'):
            raise ValueError('This simulation is not awaiting a decision')
        if decision == 'cancel':
            record['stage'] = 'cancelled'
        elif stage == 'awaiting_confirmation' and decision == 'confirm':
            record['stage'] = 'simulation_continued'
        elif stage == 'awaiting_reason' and decision == 'explain':
            if not isinstance(reason, str) or not reason.strip() or len(reason) > 5000:
                raise ValueError('A reason of 1 to 5000 characters is required')
            record['investor_reason'] = reason.strip()
            handoff = {'schema_version': SCHEMA_VERSION, 'simulation_id': sid,
                       'investor_reason': reason.strip(), 'request': record['request'],
                       'numerical_result': record['result'],
                       'instruction': 'Explain the fixed numerical result. Use sourced tool outputs only. Do not change the verdict or stored target profile.'}
            self.store.save(f'agent_requests/{sid}.json', handoff)
            record['stage'] = 'awaiting_agents'
            record['agent_status'] = 'not_configured'
            if self.dispatch_agents is not None:
                try:
                    self.dispatch_agents(copy.deepcopy(handoff))
                    record['agent_status'] = 'dispatched'
                except Exception:
                    logger.exception('Agent dispatch failed for %s', sid)
                    record['agent_status'] = 'dispatch_failed'
        else:
            raise ValueError('Decision is not supported for this stage')
        self.store.save(f'results/{sid}.json', record)
        logger.info(json.dumps({'step': 'investor_decision', 'simulation_id': sid,
                               'stage': record['stage'], 'agent_status': record['agent_status']}))
        return record

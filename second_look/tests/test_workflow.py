import copy
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from second_look.application import FileStore, SecondLookApplication
from second_look.lambda_handler import handler
from second_look.web import make_handler
from second_look.__main__ import make_snapshot
from second_look.cleaning import clean_data
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets=json.loads((ROOT/'config/assets.json').read_text())
        cls.policy=json.loads((ROOT/'config/policy.json').read_text())
        cls.refs=json.loads((ROOT/'config/reference_portfolios.json').read_text())
        cls.client=json.loads((ROOT/'examples/client.json').read_text())
        aligned,report=clean_data(pd.read_csv(ROOT/'data/raw/prices_daily.csv'),pd.read_csv(ROOT/'data/raw/fx_usdcad_daily.csv'),cls.assets,cls.policy['data_quality'])
        report.update(sources=[],source_sha256={})
        cls.snapshot=make_snapshot(aligned,report,cls.client['holdings_as_of'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=FileStore(self.temp.name)
        for key,value in [('config/assets.json',self.assets),('config/policy.json',self.policy),('config/reference_portfolios.json',self.refs),('clients/demo_balanced.json',self.client),('snapshots/2026-11-13.json',self.snapshot)]:
            self.store.save(key,value)
        self.app=SecondLookApplication(self.store)

    def trade(self,ticker='SPY',amount=4000):
        return {'client_id':'demo_balanced','decision_date':'2026-11-13','ticker':ticker,'side':'buy','amount_cad':amount}

    def test_green_yellow_confirmation_and_cancel(self):
        green=self.app.evaluate(self.trade())
        self.assertEqual(green['stage'],'simulation_continued')
        yellow=self.app.evaluate(self.trade('GLD',14000))
        self.assertEqual(yellow['stage'],'awaiting_confirmation')
        confirmed=self.app.decide(yellow['simulation_id'],'confirm')
        self.assertEqual(confirmed['stage'],'simulation_continued')
        self.assertEqual(confirmed['result'],yellow['result'])
        with self.assertRaises(ValueError):self.app.decide(yellow['simulation_id'],'confirm')
        yellow=self.app.evaluate(self.trade('GLD',14000))
        self.assertEqual(self.app.decide(yellow['simulation_id'],'cancel')['stage'],'cancelled')

    def test_red_reason_agent_handoff_cannot_override_verdict(self):
        received=[]
        def dispatch(payload):
            received.append(copy.deepcopy(payload))
            payload['numerical_result']['verdict']='green'
            return {'verdict':'green'}
        app=SecondLookApplication(self.store,dispatch)
        red=app.evaluate(self.trade('TSLA',12000))
        with self.assertRaises(ValueError):app.decide(red['simulation_id'],'confirm')
        with self.assertRaises(ValueError):app.decide(red['simulation_id'],'explain',' ')
        explained=app.decide(red['simulation_id'],'explain','I expect long-term growth.')
        self.assertEqual(explained['result'],red['result'])
        self.assertEqual(explained['result']['verdict'],'red')
        self.assertEqual(explained['agent_status'],'dispatched')
        self.assertEqual(received[0]['numerical_result']['verdict'],'red')
        self.assertEqual(self.store.load(f"agent_requests/{red['simulation_id']}.json")['numerical_result'],red['result'])

    def test_missing_agent_service_and_partial_result_are_explicit(self):
        red=self.app.evaluate(self.trade('TSLA',12000))
        result=self.app.decide(red['simulation_id'],'explain','My reason')
        self.assertEqual(result['agent_status'],'not_configured')
        self.assertEqual(result['stage'],'awaiting_agents')
        client=copy.deepcopy(self.client);client['preferences']['unimplemented_restriction']=True
        self.store.save('clients/demo_balanced.json',client)
        record=self.app.evaluate(self.trade())
        self.assertIsNone(record['result']['verdict'])
        self.assertEqual(record['stage'],'manual_review')
        with self.assertRaises(ValueError):self.app.decide(record['simulation_id'],'confirm')

    def test_trusted_profile_required_fields_and_stale_data(self):
        for bad in [dict(self.trade(),profile='Maximum growth'),dict(self.trade(),amount_cad=15001),dict(self.trade(),client_id='../bad')]:
            with self.assertRaises(ValueError):self.app.evaluate(bad)
        client=copy.deepcopy(self.client);del client['preferences']
        self.store.save('clients/demo_balanced.json',client)
        with self.assertRaises(KeyError):self.app.evaluate(self.trade())
        self.store.save('clients/demo_balanced.json',self.client)
        snapshot=copy.deepcopy(self.snapshot);snapshot['quotes']['SPY']['price_available_at']='2026-10-01T00:00:00+00:00'
        self.store.save('snapshots/2026-11-13.json',snapshot)
        with self.assertRaises(ValueError):self.app.evaluate(self.trade())

    def test_red_requires_explicit_risk_acknowledgement_and_keeps_verdict(self):
        red=self.app.evaluate(self.trade('TSLA',12000))
        for value in [False, None, 'yes', 1]:
            with self.assertRaises(ValueError):
                self.app.decide(red['simulation_id'],'proceed',risk_acknowledged=value)
        continued=self.app.decide(red['simulation_id'],'proceed',risk_acknowledged=True)
        self.assertEqual(continued['stage'],'simulation_continued')
        self.assertTrue(continued['risk_acknowledged'])
        self.assertEqual(continued['result'],red['result'])
        self.assertEqual(continued['result']['verdict'],'red')
        with self.assertRaises(ValueError):self.app.decide(red['simulation_id'],'proceed',risk_acknowledged=True)

    def test_red_can_be_cancelled_or_acknowledged_after_agent_handoff(self):
        for decision in ['cancel','proceed']:
            red=self.app.evaluate(self.trade('TSLA',12000))
            explained=self.app.decide(red['simulation_id'],'explain','My investment thesis')
            final=self.app.decide(red['simulation_id'],decision,risk_acknowledged=decision=='proceed')
            self.assertEqual(final['stage'],'cancelled' if decision=='cancel' else 'simulation_continued')
            self.assertEqual(final['result'],explained['result'])

    def test_current_result_and_risk_acknowledgement_match_shared_schema(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('Install the root locked environment for JSON Schema checks')
        schema=json.loads((ROOT/'contracts/simulation.schema.json').read_text())
        validator=Draft202012Validator(schema)
        for ticker,amount in [('SPY',4000),('GLD',14000),('TSLA',12000)]:
            record=self.app.evaluate(self.trade(ticker,amount))
            validator.validate(record)
            if record['result']['verdict']=='red':
                validator.validate(self.app.decide(record['simulation_id'],'proceed',risk_acknowledged=True))

    def test_aws_adapter_with_mock_s3_and_existing_agent_lambda(self):
        objects={f'second-look/{p.relative_to(Path(self.temp.name)).as_posix()}':p.read_bytes() for p in Path(self.temp.name).rglob('*.json')}
        invoked=[]
        class S3:
            def get_object(self,**kwargs):return {'Body':io.BytesIO(objects[kwargs['Key']])}
            def put_object(self,**kwargs):objects[kwargs['Key']]=kwargs['Body']
        class Lambda:
            def invoke(self,**kwargs):invoked.append(kwargs);return {'StatusCode':202}
        boto=types.SimpleNamespace(client=lambda service:S3() if service=='s3' else Lambda())
        with patch.dict(sys.modules,{'boto3':boto}),patch.dict(os.environ,{'DATA_BUCKET':'existing-test-bucket','DATA_PREFIX':'second-look','AGENT_FUNCTION_NAME':'existing-agents'}):
            response=handler({'body':json.dumps(self.trade('TSLA',12000))},types.SimpleNamespace(aws_request_id='invocation-1'))
            self.assertEqual(response['statusCode'],200)
            record=json.loads(response['body']);self.assertEqual(record['result']['verdict'],'red')
            response=handler({'rawPath':'/simulations/invocation-1/decision','body':json.dumps({'decision':'explain','reason':'My reason'})},types.SimpleNamespace())
            self.assertEqual(json.loads(response['body'])['agent_status'],'dispatched')
            self.assertEqual(json.loads(invoked[0]['Payload'])['numerical_result']['verdict'],'red')
            self.assertEqual(invoked[0]['FunctionName'],'existing-agents')
            self.assertEqual(handler({'body':json.dumps(dict(self.trade(),profile='Growth'))},types.SimpleNamespace())['statusCode'],400)

"""The hosted dashboard must share the local trade and acknowledgement contract.

Model calls and storage are mocked; the numerical evaluation uses real fixtures.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'second_look'))
from second_look.cleaning import clean_data
from second_look.__main__ import make_snapshot


class BrokerageApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        component=ROOT/'second_look'
        cls.inputs={f'config/{name}.json':json.loads((component/f'config/{name}.json').read_text())
                    for name in ['assets','policy','reference_portfolios']}
        cls.inputs['clients/demo_balanced.json']=json.loads((component/'examples/client.json').read_text())
        aligned,report=clean_data(pd.read_csv(component/'data/raw/prices_daily.csv'),pd.read_csv(component/'data/raw/fx_usdcad_daily.csv'),
                                  cls.inputs['config/assets.json'],cls.inputs['config/policy.json']['data_quality'])
        report.update(sources=[],source_sha256={})
        cls.inputs['snapshots/2026-11-13.json']=make_snapshot(aligned,report,'2026-11-13')
        cls.model=types.SimpleNamespace(DECISION_DATE='2026-11-13',run=Mock())
        spec=importlib.util.spec_from_file_location('brokerage_api_under_test',ROOT/'api_handler.py')
        cls.api=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'second_look':cls.model,'tools':types.SimpleNamespace(TRACE=[])}):
            spec.loader.exec_module(cls.api)

    def setUp(self):
        self.values=copy.deepcopy(self.inputs)
        values=self.values
        class Store:
            def load(self,key):return copy.deepcopy(values[key])
            def save(self,key,value):values[key]=copy.deepcopy(value)
            def load_versioned(self,key):
                if key not in values:return None,None
                from second_look.application import digest
                return self.load(key),digest(values[key])
            def compare_and_swap(self,key,value,version):
                from second_look.application import StateConflict
                if self.load_versioned(key)[1]!=version:raise StateConflict('Session changed')
                self.save(key,value);return self.load_versioned(key)[1]
        self.api.STORE=Store()
        self.model.run.reset_mock(side_effect=True)
        self.model.run.return_value={'verdict':'red','headline':'Mocked agent review','rule_check':{}}

    def call(self,path,payload):
        response=self.api.handler({'rawPath':path,'body':json.dumps(payload)},types.SimpleNamespace(aws_request_id='api-test'))
        return response['statusCode'],json.loads(response['body'])

    def red(self):
        code,record=self.call('/api/evaluate',{'client_id':'demo_balanced','decision_date':'2026-11-13','ticker':'TSLA','side':'buy','amount_cad':12000})
        self.assertEqual(code,200)
        self.assertEqual(record['result']['verdict'],'red')
        return record

    def test_context_returns_dated_quotes_and_account_facts(self):
        context=self.api._context({'client_id':'demo_balanced'})
        self.assertEqual(len(context['quotes']),31)
        self.assertAlmostEqual(context['account']['total_cad'],100000)
        self.assertEqual(context['quotes']['SPY']['currency'],'USD')

    def test_hosted_red_review_and_acknowledgement_keep_numerical_result(self):
        red=self.red();path='/api/simulations/api-test/decision'
        code,reviewed=self.call(path,{'decision':'explain','reason':'A fictional investment thesis'})
        self.assertEqual(code,200)
        self.assertEqual(reviewed['agent_status'],'completed')
        self.assertEqual(reviewed['result'],red['result'])
        self.assertEqual(self.call(path,{'decision':'proceed'})[0],400)
        code,continued=self.call(path,{'decision':'proceed','risk_acknowledged':True})
        self.assertEqual(code,200)
        self.assertEqual(continued['stage'],'simulation_continued')
        self.assertEqual(continued['result'],red['result'])

    def test_unsupported_agent_side_is_explicit_instead_of_explaining_a_buy(self):
        red=self.red();red['request']['side']='sell'
        reviewed=self.api._run_agents(red)
        self.assertEqual(reviewed['agent_status'],'unavailable')
        self.model.run.assert_not_called()

    def test_late_agent_response_cannot_undo_newer_cancellation(self):
        red=self.red();path='/api/simulations/api-test/decision'
        def review(*args,**kwargs):
            pending=self.api.STORE.load('results/api-test.json')
            pending['stage']='cancelled';self.api.STORE.save('results/api-test.json',pending)
            return {'verdict':'red','headline':'Delayed mocked review'}
        self.model.run.side_effect=review
        code,record=self.call(path,{'decision':'explain','reason':'My reason'})
        self.assertEqual(code,200)
        self.assertEqual(record['stage'],'cancelled')
        self.assertEqual(record['result'],red['result'])

    def test_conversation_http_route_restores_deduplicates_and_isolates_clients(self):
        from second_look.conversation import ConversationService
        coordinator=types.SimpleNamespace(initial=Mock(return_value={'specialist_reports':{},'explanation':{},'evidence':{}}),
            respond=Mock(return_value={'assistant_message':'A scripted integration reply.','evidence_refs':['current']}),max_tool_calls=3)
        chat=ConversationService(self.api.STORE,coordinator)
        red=self.red();path='/api/simulations/api-test/conversation'
        payload={'client_id':'demo_balanced','input_version':red['assessment_input_version'],'message_id':'turn_1',
                 'expected_revision':0,'message':'What does this red flag mean?'}
        with patch.object(self.api,'_conversations',return_value=chat):
            code,response=self.call(path,payload)
            self.assertEqual(code,200)
            self.assertEqual(response['current_verdict'],'red')
            self.assertEqual(len(response['conversation']['messages']),2)
            self.assertEqual(self.call(path,payload),(code,response))
            self.assertEqual(coordinator.initial.call_count,1)
            self.assertEqual(coordinator.respond.call_count,1)
            self.assertEqual(self.call(path,dict(payload,client_id='robert'))[0],400)
            self.assertEqual(self.call(path,dict(payload,message_id='turn_2'))[0],409)
            get=self.api.handler({'rawPath':path,'requestContext':{'http':{'method':'GET'}},
                'queryStringParameters':{'client_id':'demo_balanced','input_version':red['assessment_input_version']}})
            self.assertEqual(get['statusCode'],200)
            self.assertEqual(json.loads(get['body'])['conversation'],response['conversation'])

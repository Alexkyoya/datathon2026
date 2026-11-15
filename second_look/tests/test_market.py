"""Display facts must agree with the numerical engine's trusted valuation."""
import copy
import json
import unittest
from pathlib import Path
import pandas as pd
from second_look.cleaning import clean_data
from second_look.__main__ import make_snapshot
from second_look.market import build_context
from second_look.service import evaluate_from_snapshot

ROOT=Path(__file__).resolve().parents[1]


class MarketContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets=json.loads((ROOT/'config/assets.json').read_text())
        cls.policy=json.loads((ROOT/'config/policy.json').read_text())
        cls.references=json.loads((ROOT/'config/reference_portfolios.json').read_text())
        cls.client=json.loads((ROOT/'examples/client.json').read_text())
        aligned,report=clean_data(pd.read_csv(ROOT/'data/raw/prices_daily.csv'),pd.read_csv(ROOT/'data/raw/fx_usdcad_daily.csv'),cls.assets,cls.policy['data_quality'])
        report.update(sources=[],source_sha256={})
        cls.snapshot=make_snapshot(aligned,report,cls.client['holdings_as_of'])

    def context(self,snapshot=None):
        values={'clients/demo_balanced.json':self.client,'config/assets.json':self.assets,
                'config/policy.json':self.policy,'config/reference_portfolios.json':self.references,
                'snapshots/2026-11-13.json':snapshot or self.snapshot}
        class Store:
            def load(self,key):return values[key]
        return build_context(Store(),'demo_balanced',['demo_balanced'])

    def test_account_and_cad_conversion_match_evaluation(self):
        context=self.context()
        request={'client_id':'demo_balanced','decision_date':'2026-11-13','ticker':'SPY','side':'buy','amount_cad':4000}
        result=evaluate_from_snapshot(request,self.client,self.snapshot,self.assets,self.policy,self.references)
        self.assertAlmostEqual(context['account']['total_cad'],result['portfolio_value_cad'])
        self.assertAlmostEqual(sum(h['value_cad'] for h in context['holdings'])+context['account']['cash_cad'],context['account']['total_cad'])
        q=context['quotes']['SPY']
        self.assertAlmostEqual(q['close_native']*q['fx_to_cad'],q['close_cad'])
        self.assertAlmostEqual(4000/q['close_cad'],result['simulated_trade_units'])

    def test_history_and_quote_fields_are_real_completed_source_observations(self):
        context=self.context()
        self.assertEqual(len(context['quotes']),31)
        for ticker,points in context['price_history'].items():
            self.assertLessEqual(len(points),90)
            self.assertTrue(all(p['date']<self.client['holdings_as_of'] for p in points))
            self.assertTrue(all(p['close_cad']>0 for p in points))
            self.assertEqual([p['date'] for p in points],sorted(p['date'] for p in points))
            quote=context['quotes'][ticker]
            self.assertGreaterEqual(quote['volume'],0)
            self.assertLessEqual(quote['low_native'],quote['high_native'])

    def test_missing_unheld_quote_is_not_invented(self):
        snapshot=copy.deepcopy(self.snapshot);del snapshot['quotes']['NVDA']
        self.assertNotIn('NVDA',self.context(snapshot)['quotes'])

    def test_stale_held_quote_rejects_account_display(self):
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['quotes']['SPY']['price_available_at']='2026-10-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'stale'):self.context(snapshot)

    def test_real_persona_first_names(self):
        for name in ['robert','mateo','amira']:
            client=json.loads((ROOT/f'examples/clients/{name}.json').read_text())
            self.assertEqual(client['name'].split()[0].lower(),name)

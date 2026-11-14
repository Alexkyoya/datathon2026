import copy
import json
import math
import unittest
from pathlib import Path
from second_look.contracts import CLASSES, PROFILE_ORDER
from second_look.rules import evaluate_trade
from second_look.spectrum import build_references, project, classify_transition

ROOT = Path(__file__).resolve().parents[1]


class SpectrumTests(unittest.TestCase):
    def setUp(self):
        self.assets = json.loads((ROOT/'config/assets.json').read_text())
        self.policy = json.loads((ROOT/'config/policy.json').read_text())
        self.config = json.loads((ROOT/'config/reference_portfolios.json').read_text())
        self.refs = build_references(self.config, self.assets, self.policy)

    def test_weights_totals_ranges_and_official_targets(self):
        self.assertEqual([r['category'] for r in self.refs], list(PROFILE_ORDER))
        expected = [(0.04,.685,.225,.05),(.04,.535,.375,.05),
                    (.04,.38,.48,.10),(.04,.28,.58,.10),(.03,.14,.68,.15)]
        for ref, target in zip(self.refs, expected):
            self.assertAlmostEqual(sum(ref['ticker_weights'].values()), 1)
            self.assertAlmostEqual(sum(ref['official_recommended_weights'].values()), 1)
            for cls, value in zip(CLASSES, target):
                self.assertAlmostEqual(ref['class_weights'][cls], value)
                low, high = self.policy['profiles'][ref['category']]['asset_class_ranges'][cls]
                self.assertLessEqual(low, value)
                self.assertLessEqual(value, high)
        self.assertTrue(all(self.assets[t]['asset_class']=='alternatives' for t in ('BTC-USD','ETH-USD')))

    def test_exact_references_and_endpoint_memberships(self):
        for index, ref in enumerate(self.refs):
            result = project(ref['class_weights'], self.refs)
            self.assertAlmostEqual(result['score'], index)
            self.assertAlmostEqual(result['distance'], 0)
            self.assertAlmostEqual(result['membership_weights'][ref['category']], 1)
            self.assertAlmostEqual(sum(result['membership_weights'].values()), 1)

    def test_interpolation_and_transition_relative_to_stored_target(self):
        left, right = self.refs[2]['class_weights'], self.refs[3]['class_weights']
        between = {c:.65*left[c]+.35*right[c] for c in CLASSES}
        result = classify_transition(left, between, 'Balanced', self.config, self.assets, self.policy)
        self.assertEqual(result['after']['categories'], ['Balanced','Growth'])
        self.assertAlmostEqual(result['after']['membership_weights']['Balanced'], .65)
        self.assertAlmostEqual(result['after']['score'], 2.35)
        self.assertAlmostEqual(result['after']['distance'], 0)
        self.assertAlmostEqual(result['movement']['change_in_distance_to_target'], .35)
        self.assertEqual(result['target_profile'], 'Balanced')

    def test_vertex_tie_chooses_earliest_segment(self):
        result = project(self.refs[2]['class_weights'], self.refs)
        self.assertEqual(result['categories'], ['Conservative','Balanced'])
        self.assertEqual(result['membership_weights'], {'Conservative':0,'Balanced':1})

    def test_equal_distance_tie_is_deterministic(self):
        # Repeated/reversed segment deliberately creates equal-distance candidates.
        vectors = [(1,0,0,0),(0,1,0,0),(1,0,0,0),(0,0,1,0),(0,0,0,1)]
        refs = [{'category':name,'class_weights':dict(zip(CLASSES,v))}
                for name,v in zip(PROFILE_ORDER,vectors)]
        result = project(dict(zip(CLASSES,(.5,.5,0,0))), refs)
        self.assertEqual(result['score'], .5)
        self.assertEqual(result['categories'], ['Income','Conservative'])

    def test_far_from_spectrum_reports_residual_distance(self):
        vector = dict(zip(CLASSES,(0,0,0,1)))
        result = project(vector, self.refs)
        self.assertGreater(result['distance'], .8)
        self.assertLessEqual(result['score'], 4)
        self.assertGreaterEqual(result['score'], 0)
        self.assertAlmostEqual(sum(result['residual_class_weights'].values()), 0)
        self.assertAlmostEqual(result['distance'], math.sqrt(sum(v*v for v in result['residual_class_weights'].values())))

    def test_invalid_config_and_vectors_fail_closed(self):
        for mutate in [lambda d:d['portfolios'][0]['ticker_weights'].update({'ZST.TO':.5}),
                       lambda d:d['portfolios'].reverse(),
                       lambda d:d['portfolios'][0]['ticker_weights'].update({'UNKNOWN':0})]:
            config=copy.deepcopy(self.config);mutate(config)
            with self.assertRaises(ValueError):build_references(config,self.assets,self.policy)
        for vector in [{}, dict(zip(CLASSES,(0,0,0,0))),dict(zip(CLASSES,(float('nan'),0,0,1))),dict(zip(CLASSES,(-.1,.5,.5,.1)))]:
            with self.assertRaises(ValueError):project(vector,self.refs)

    def test_exact_balanced_match_cannot_hide_crypto_breach(self):
        # The trade lands exactly on Balanced's class vector, with prohibited crypto.
        holdings={'CASH_CAD':5000,'XBB.TO':38000,'SPY':24000,'XIU.TO':24000,'GLD':9000}
        client={'client_id':'test','profile':'Balanced','horizon_years':8,
                'loss_tolerance_fraction':.2,'objectives':['growth'],'preferences':{}}
        request={'client_id':'test','decision_date':'2026-11-13','ticker':'BTC-USD','side':'buy','amount_cad':1000}
        result=evaluate_trade(request,client,holdings,self.assets,self.policy,self.config)
        self.assertAlmostEqual(result['classification']['after']['score'],2)
        self.assertAlmostEqual(result['classification']['after']['distance'],0)
        self.assertEqual(result['verdict'],'red')
        self.assertTrue(any(c['rule']=='crypto' and c['severity']=='red' for c in result['breaches']))

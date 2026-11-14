import json
import unittest
from pathlib import Path

from second_look.contracts import PROFILE_ORDER
from second_look.questionnaire import (PERSONA_ANSWERS, QUESTIONS, SCORE_BANDS, build_client,
                                       place_investor, score_answers)
from second_look.rules import evaluate_trade

ROOT = Path(__file__).resolve().parents[1]


class QuestionnaireTests(unittest.TestCase):
    def test_questions_are_well_formed(self):
        for q in QUESTIONS:
            if q['kind'] == 'choice':
                self.assertEqual(sorted(q['options'].values()), [1, 2, 3, 4, 5], q['key'])
                self.assertEqual(set(q['labels']), set(q['options']), q['key'])
        # Bands cover 5..25 without gaps and increase in risk.
        self.assertEqual([lo for lo, _, _ in SCORE_BANDS], [5, 9, 13, 17, 21])
        self.assertEqual([hi for _, hi, _ in SCORE_BANDS], [8, 12, 16, 20, 25])
        self.assertEqual([p for _, _, p in SCORE_BANDS], list(PROFILE_ORDER))

    def test_personas_land_on_their_stored_profiles(self):
        expected = {'robert': 'Conservative', 'mateo': 'Balanced', 'amira': 'Growth'}
        for persona, profile in expected.items():
            placement = place_investor(PERSONA_ANSWERS[persona])
            self.assertEqual(placement['profile'], profile, placement)
            stored = json.loads((ROOT / 'examples' / 'clients' / f'{persona}.json').read_text())
            self.assertEqual(placement['profile'], stored['profile'])
            self.assertEqual(placement['loss_tolerance_fraction'], stored['loss_tolerance_fraction'])

    def test_horizon_buckets(self):
        self.assertEqual(score_answers(dict(PERSONA_ANSWERS['mateo'], horizon_years=2.9))['horizon_years'], 1)
        self.assertEqual(score_answers(dict(PERSONA_ANSWERS['mateo'], horizon_years=3))['horizon_years'], 2)
        self.assertEqual(score_answers(dict(PERSONA_ANSWERS['mateo'], horizon_years=5))['horizon_years'], 3)
        self.assertEqual(score_answers(dict(PERSONA_ANSWERS['mateo'], horizon_years=10))['horizon_years'], 4)
        self.assertEqual(score_answers(dict(PERSONA_ANSWERS['mateo'], horizon_years=15))['horizon_years'], 5)
        self.assertEqual(score_answers(dict(PERSONA_ANSWERS['mateo'], horizon_years=40))['horizon_years'], 5)

    def test_short_horizon_caps_an_aggressive_appetite(self):
        answers = {'horizon_years': 2, 'capacity': 'none', 'willingness': 'any',
                   'objective': 'max_growth', 'experience': 'professional'}
        p = place_investor(answers)
        self.assertEqual(p['band_profile'], 'Maximum growth')
        self.assertEqual(p['profile'], 'Income')
        self.assertEqual(p['caps_applied'], {'horizon': 'Income'})
        self.assertIn('horizon cap', p['explanation'])

    def test_low_capacity_caps_willingness(self):
        answers = {'horizon_years': 30, 'capacity': 'severe', 'willingness': 'any',
                   'objective': 'max_growth', 'experience': 'advanced'}
        p = place_investor(answers)
        self.assertEqual(p['profile'], 'Income')
        self.assertEqual(p['caps_applied'], {'capacity': 'Income'})
        answers['capacity'] = 'significant'
        self.assertEqual(place_investor(answers)['profile'], 'Conservative')

    def test_caps_never_raise_a_profile(self):
        # Caps only ever lower a placement. A long horizon and high capacity leave the
        # band result untouched; they never lift a client above what the score gives.
        answers = {'horizon_years': 30, 'capacity': 'none', 'willingness': 'avoid',
                   'objective': 'preserve', 'experience': 'none'}
        p = place_investor(answers)
        self.assertEqual(p['caps'], {'horizon': 'Maximum growth'})
        self.assertEqual(p['caps_applied'], {})
        self.assertEqual(p['profile'], p['band_profile'])

    def test_monotonic_in_every_answer(self):
        # Raising any single answer never lowers the profile.
        base = dict(PERSONA_ANSWERS['mateo'])
        for q in QUESTIONS:
            if q['kind'] == 'years':
                ladder = [1, 4, 7, 12, 20]
            else:
                ladder = sorted(q['options'], key=q['options'].get)
            ranks = [PROFILE_ORDER.index(place_investor(dict(base, **{q['key']: v}))['profile'])
                     for v in ladder]
            self.assertEqual(ranks, sorted(ranks), q['key'])

    def test_validation(self):
        with self.assertRaises(ValueError):
            place_investor({'horizon_years': 5})
        with self.assertRaises(ValueError):
            place_investor(dict(PERSONA_ANSWERS['amira'], objective='moon'))
        with self.assertRaises(ValueError):
            place_investor(dict(PERSONA_ANSWERS['amira'], horizon_years=-1))
        with self.assertRaises(ValueError):
            place_investor('not a dict')

    def test_build_client_is_accepted_by_the_rule_engine(self):
        client = build_client('newclient', 'New Client', 35, PERSONA_ANSWERS['mateo'],
                              cash_cad=15000, holdings={'XBB.TO': 500, 'SPY': 20},
                              holdings_as_of='2026-11-13',
                              preferences={'excluded_tickers': ['XOM']})
        self.assertEqual(client['profile'], 'Balanced')
        self.assertEqual(client['horizon_years'], 10.0)
        self.assertEqual(client['preferences'], {'excluded_tickers': ['XOM'], 'excluded_sectors': []})
        self.assertTrue(client['fictional'])
        self.assertIn('explanation', client['placement'])
        for key in ('client_id', 'profile', 'horizon_years', 'objectives', 'loss_tolerance_fraction',
                    'preferences', 'cash_cad', 'holdings_as_of', 'holdings'):
            self.assertIn(key, client)
        # Ariane's engine evaluates a trade for this client without complaint.
        assets = json.loads((ROOT / 'config/assets.json').read_text())
        policy = json.loads((ROOT / 'config/policy.json').read_text())
        holdings_cad = {'CASH_CAD': 15000, 'XBB.TO': 40000, 'SPY': 30000, 'XIU.TO': 15000}
        req = {'client_id': 'newclient', 'decision_date': '2026-11-13', 'ticker': 'SPY',
               'side': 'buy', 'amount_cad': 1000}
        result = evaluate_trade(req, client, holdings_cad, assets, policy)
        self.assertEqual(result['profile'], 'Balanced')
        self.assertIn(result['verdict'], ('green', 'yellow', 'red'))
        # The excluded ticker is enforced from the stored preferences.
        req_xom = dict(req, ticker='XOM')
        self.assertEqual(evaluate_trade(req_xom, client, holdings_cad, assets, policy)['verdict'], 'red')

    def test_build_client_validation(self):
        good = dict(client_id='c1', name='C', age=40, answers=PERSONA_ANSWERS['robert'],
                    cash_cad=100, holdings={}, holdings_as_of='2026-11-13')
        with self.assertRaises(ValueError):
            build_client(**dict(good, client_id='bad id!'))
        with self.assertRaises(ValueError):
            build_client(**dict(good, name='  '))
        with self.assertRaises(ValueError):
            build_client(**dict(good, holdings={'SPY': -1}))
        with self.assertRaises(ValueError):
            build_client(**dict(good, cash_cad=-5))


if __name__ == '__main__':
    unittest.main()

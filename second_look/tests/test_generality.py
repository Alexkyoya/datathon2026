"""Generality: random valid clients and trades over the whole universe never break the engine."""
import json
import math
import random
import unittest
from datetime import date, timedelta
from pathlib import Path

from second_look.rules import evaluate_trade

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-11-13'
N_CLIENTS = 220
VERDICTS = {'green', 'yellow', 'red', 'manual_review'}


def random_client(rng, i, assets, policy):
    tickers = sorted(assets)
    size = rng.choice([1e4, 5e6]) if i < 2 else 10 ** rng.uniform(4, math.log10(5e6))  # CAD 10,000 .. 5,000,000
    held = rng.sample(tickers, rng.randint(1, 12))
    raw = {t: rng.random() for t in held}
    cash_share = rng.uniform(0.01, 0.5)
    total_raw = sum(raw.values())
    holdings = {t: size * (1 - cash_share) * v / total_raw for t, v in raw.items()}
    holdings['CASH_CAD'] = size * cash_share
    today = date.fromisoformat(DAY)
    goals = []
    for g in range(rng.randint(0, 4)):
        if rng.random() < 0.6:
            goals.append({'name': f'one-off {g}', 'type': 'one_off', 'amount_cad': size * rng.uniform(0, 0.4),
                          'target_date': (today + timedelta(days=rng.randint(0, 3650))).isoformat(),
                          'priority': rng.choice(['essential', 'flexible']), 'source': 'random test'})
        else:
            goal = {'name': f'recurring {g}', 'type': 'recurring', 'amount_cad_per_year': size * rng.uniform(0, 0.1),
                    'start_date': (today + timedelta(days=rng.randint(-1500, 2500))).isoformat(),
                    'indexed_to_inflation': rng.random() < 0.5, 'priority': rng.choice(['essential', 'flexible']),
                    'funded_by_other_income_cad_per_year': 0.0, 'source': 'random test'}
            if rng.random() < 0.5:
                goal['years'] = rng.randint(1, 35)
            else:
                goal['end_date'] = (date.fromisoformat(goal['start_date']) + timedelta(days=rng.randint(1, 9000))).isoformat()
            goals.append(goal)
    other = rng.choice([0.0, rng.uniform(0, 1e5)])
    budget = other
    for g in goals:
        if g['type'] == 'recurring' and budget > 0:
            g['funded_by_other_income_cad_per_year'] = rng.uniform(0, budget)
            budget -= g['funded_by_other_income_cad_per_year']
    sectors = sorted({s for a in assets.values() for s in (a.get('sector_weights') or {})})
    prefs = {}
    if rng.random() < 0.5:
        prefs['esg'] = {'excluded_tickers': rng.sample(tickers, rng.randint(0, 4)),
                        'excluded_sectors': rng.sample(sectors, rng.randint(0, 2)),
                        'max_lookthrough_excluded_share': rng.choice([0.0, 0.02, 0.05, 0.1, 0.3]),
                        'prefer_esg_screened_funds': rng.random() < 0.5, 'source': 'random test'}
    client = {'client_id': f'c{i}', 'profile': rng.choice(sorted(policy['profiles'])), 'horizon_years': rng.randint(1, 30),
              'loss_tolerance_fraction': rng.uniform(0, 0.5), 'objectives': ['random'], 'preferences': prefs,
              'income': {'other_income_cad_per_year': other, 'source': 'random test'}, 'goals': goals}
    return client, holdings


def random_trade(rng, client, holdings, assets):
    held = [t for t in holdings if t != 'CASH_CAD' and holdings[t] > 1]
    if held and rng.random() < 0.4:
        t = rng.choice(held)
        return {'client_id': client['client_id'], 'decision_date': DAY, 'ticker': t, 'side': 'sell',
                'amount_cad': holdings[t] * rng.uniform(0.01, 1)}
    return {'client_id': client['client_id'], 'decision_date': DAY, 'ticker': rng.choice(sorted(assets)),
            'side': 'buy', 'amount_cad': holdings['CASH_CAD'] * rng.uniform(0.01, 1)}


class GeneralityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = json.loads((ROOT/'config/assets.json').read_text())
        cls.policy = json.loads((ROOT/'config/policy.json').read_text())
        cls.refs = json.loads((ROOT/'config/reference_portfolios.json').read_text())

    def test_random_clients_and_trades(self):
        rng = random.Random(2026)
        counts = dict.fromkeys(VERDICTS, 0)
        fired = {'goal': 0, 'esg': 0}
        for i in range(N_CLIENTS):
            client, holdings = random_client(rng, i, self.assets, self.policy)
            for _ in range(3):
                req = random_trade(rng, client, holdings, self.assets)
                r = evaluate_trade(req, client, holdings, self.assets, self.policy, self.refs)
                verdict = r['verdict'] or 'manual_review'
                self.assertIn(verdict, VERDICTS)
                counts[verdict] += 1
                flagged = [a for a in r['alerts'] if a.get('kind') in ('goal', 'esg')]
                for a in flagged:
                    fired[a['kind']] += 1
                    self.assertIn(a['effect'], ('creates', 'worsens'))
                    for key in ('rule', 'severity', 'before', 'after', 'limit', 'effect', 'source', 'message'):
                        self.assertIn(key, a)
                # Improving trades: a sale only adds cash and cuts exposure; buying a cash-like fund is neutral.
                improving = req['side'] == 'sell' or self.assets[req['ticker']]['asset_class'] == 'cash'
                if improving:
                    self.assertEqual(flagged, [], (client, req))
                again = evaluate_trade(req, client, holdings, self.assets, self.policy, self.refs)
                self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(r, sort_keys=True))
                json.dumps(r, allow_nan=False)
        # The random set really exercises the new rules and every colour.
        self.assertGreater(fired['goal'], 10)
        self.assertGreater(fired['esg'], 10)
        self.assertTrue(all(counts[v] > 0 for v in ('green', 'yellow', 'red')), counts)


if __name__ == '__main__':
    unittest.main()

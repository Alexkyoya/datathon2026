"""Bias test, part 2: the goal-coverage rule (goals.py) and the ESG rule (esg.py).

These are the two rules most likely to smuggle a bias back in. The goal rule could push
every client toward cash and bonds; the ESG rule could penalise clients who never asked
for a screen. The tests below show neither happens:

- The goal rule reacts to dates, not to risk appetite: the same trade is red when an
  essential need is 18 months away and clean when it is 4 years away.
- The goal rule is not a bond rule. In the provided price window, XBB.TO took 38 months to
  recover from its worst fall and SPY 19, so for a need two years out the engine flags the
  bond purchase and passes the equity purchase. The ranking is data-derived and
  window-dependent, which the tech doc must say.
- Goals ignore the risk profile; ESG ignores clients without a screen; selling is never
  penalised by either rule.

Portfolios hold 8+ positions so the HHI rule does not fire. CAD values; all fictional.
"""
import json
import unittest
from pathlib import Path

from second_look.goals import asset_liquidity
from second_look.rules import evaluate_trade

ROOT = Path(__file__).resolve().parents[1]
DATE = '2026-11-13'

# Growth client, compliant: cash 24%, fixed income 23%, equities 47%, gold 7% (150k).
GROWTH = {'CASH_CAD': 36000, 'XBB.TO': 17000, 'AGG': 17000, 'XIU.TO': 17000, 'SPY': 18000,
          'ESGV': 17000, 'QQQ': 18000, 'GLD': 10000}
# Conservative client whose risky holdings all took more than 24 months to recover:
# cash 20%, fixed income 53%, six TSX stocks 27% (150k).
SLOW = {'CASH_CAD': 30000, 'XBB.TO': 30000, 'AGG': 30000, 'TLT': 20000, 'TD.TO': 7000,
        'CNR.TO': 7000, 'FTS.TO': 7000, 'RY.TO': 7000, 'ENB.TO': 7000, 'ATD.TO': 5000}
# Balanced-looking portfolio for the ESG tests (100k).
MIDDLE = {'CASH_CAD': 20000, 'XBB.TO': 15000, 'AGG': 15000, 'SPY': 15000, 'XIU.TO': 15000,
          'ESGV': 10000, 'GLD': 10000}

FOSSIL_FREE = {'esg': {'excluded_tickers': ['CNQ.TO', 'ENB.TO', 'XOM'], 'excluded_sectors': ['Energy'],
                       'max_lookthrough_excluded_share': 0.05, 'prefer_esg_screened_funds': True,
                       'source': 'fictional test preference'}}


def goal(amount, target_date, priority='essential', name='Purchase'):
    return {'name': name, 'type': 'one_off', 'amount_cad': amount, 'target_date': target_date,
            'priority': priority, 'source': 'fictional test goal'}


def client(profile, goals=(), preferences=None):
    return {'client_id': 'bias', 'profile': profile, 'horizon_years': 10, 'loss_tolerance_fraction': 0.2,
            'objectives': ['test'], 'preferences': preferences or {}, 'goals': list(goals)}


def request(ticker, amount, side='buy'):
    return {'client_id': 'bias', 'decision_date': DATE, 'ticker': ticker, 'side': side, 'amount_cad': amount}


class GoalRuleBiasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = json.loads((ROOT / 'config/assets.json').read_text())
        cls.policy = json.loads((ROOT / 'config/policy.json').read_text())

    def run_trade(self, cl, holdings, ticker, amount, side='buy'):
        return evaluate_trade(request(ticker, amount, side), cl, holdings, self.assets, self.policy)

    @staticmethod
    def goal_alerts(result):
        return [c for c in result['alerts'] if c['rule'].startswith('goal.')]

    def test_goal_rule_reacts_to_the_date_not_to_the_asset_class(self):
        # Same Growth client, same 15k QQQ purchase. Only the date of a 30k essential need moves.
        near = self.run_trade(client('Growth', [goal(30000, '2028-05-13')]), GROWTH, 'QQQ', 15000)   # 18 months
        far = self.run_trade(client('Growth', [goal(30000, '2030-11-13')]), GROWTH, 'QQQ', 15000)    # 48 months
        self.assertEqual(near['verdict'], 'red')
        self.assertEqual([c['severity'] for c in self.goal_alerts(near)], ['red'])
        self.assertIn('once took', self.goal_alerts(near)[0]['why'])
        self.assertEqual(self.goal_alerts(far), [])
        self.assertNotEqual(far['verdict'], 'red')

    def test_goal_rule_passes_a_long_horizon_client_with_no_near_needs(self):
        # Amira-like: retirement money is beyond the 5-year planning horizon; an emergency fund of
        # 6k is due today and stays covered by the remaining cash. Buying equities is not a goal issue.
        goals = [goal(6000, DATE, name='Emergency fund'),
                 {'name': 'Retirement income', 'type': 'recurring', 'amount_cad_per_year': 40000,
                  'start_date': '2041-11-13', 'years': 25, 'funded_by_other_income_cad_per_year': 0,
                  'indexed_to_inflation': True, 'priority': 'essential', 'source': 'fictional test goal'}]
        r = self.run_trade(client('Growth', goals), GROWTH, 'QQQ', 20000)
        self.assertEqual(self.goal_alerts(r), [])
        self.assertNotEqual(r['verdict'], 'red')
        # Retirement installments beyond the planning horizon are reported as long-term
        # money, not checked against today's holdings.
        self.assertEqual([x['goal'] for x in r['goals']['later']], ['Retirement income'])
        self.assertEqual([x['goal'] for x in r['goals']['installments']], ['Emergency fund'])

    def test_goal_rule_is_not_a_bond_rule(self):
        # Conservative client, 25k essential need in 23 months, all risky holdings slow to recover.
        # Buying a bond ETF (38-month recovery) is red; buying a broad equity ETF (19-month
        # recovery, counted at 78% of its value) keeps the need covered and is not flagged.
        cl = client('Conservative', [goal(25000, '2028-10-13')])   # 23 months
        bonds = self.run_trade(cl, SLOW, 'XBB.TO', 20000)
        equities = self.run_trade(cl, SLOW, 'SPY', 20000)
        self.assertEqual(bonds['verdict'], 'red')
        self.assertEqual([c['rule'] for c in bonds['alerts'] if c['severity'] == 'red'],
                         ['goal.purchase.2028-10-13'])
        self.assertAlmostEqual(self.goal_alerts(bonds)[0]['after'], 0.4)      # 10k cash left / 25k
        self.assertEqual(self.goal_alerts(equities), [])
        self.assertNotEqual(equities['verdict'], 'red')

    def test_liquidity_ranking_is_data_derived_and_window_dependent(self):
        # What counts as "liquid" for a dated goal comes from each asset's worst fall and recovery
        # in the provided 2021-2026 prices, not from its asset class. In this window the bond ETFs
        # recovered more slowly than the broad equity ETFs, and two single stocks (JNJ, XOM)
        # recovered fastest of all. Cash is always usable. This must be stated as a limitation.
        months = {t: asset_liquidity(t, self.assets)[0] for t in
                  ('CASH_CAD', 'ZST.TO', 'SPY', 'XIU.TO', 'XBB.TO', 'AGG', 'TLT', 'JNJ', 'XOM', 'GLD')}
        self.assertEqual(months['CASH_CAD'], 0.0)
        self.assertEqual(months['ZST.TO'], 0.0)
        self.assertGreater(months['XBB.TO'], months['SPY'])
        self.assertGreater(months['AGG'], months['XIU.TO'])
        self.assertIsNone(months['TLT'])      # never regained its peak in the window: never counts
        self.assertIsNone(months['GLD'])
        self.assertLess(months['JNJ'], months['SPY'])
        self.assertLess(months['XOM'], months['SPY'])

    def test_goal_rule_ignores_the_risk_profile(self):
        # The same portfolio, goal and bond purchase under the most and least cautious profiles
        # produce the same goal alert. Goals are about dates and amounts, not appetite.
        g = [goal(25000, '2028-10-13')]
        a = self.goal_alerts(self.run_trade(client('Income', g), SLOW, 'XBB.TO', 20000))
        b = self.goal_alerts(self.run_trade(client('Maximum growth', g), SLOW, 'XBB.TO', 20000))
        keys = ('rule', 'before', 'after', 'severity', 'effect')
        self.assertEqual([{k: c[k] for k in keys} for c in a], [{k: c[k] for k in keys} for c in b])
        self.assertEqual(a[0]['severity'], 'red')

    def test_selling_toward_cash_improves_coverage_and_is_not_penalised(self):
        # Under-covered near goal (40k in 18 months, only 30k cash). Selling a slow asset to cash
        # improves coverage: the goal check reports 'improves' and the verdict is not red.
        # (5k, not more: a larger sale would push cash above the 20% Conservative maximum.)
        cl = client('Conservative', [goal(40000, '2028-05-13')])
        r = self.run_trade(cl, SLOW, 'XBB.TO', 5000, side='sell')
        goal_checks = [c for c in r['checks'] if c['rule'].startswith('goal.')]
        self.assertEqual([c['effect'] for c in goal_checks], ['improves'])
        self.assertNotEqual(r['verdict'], 'red')

    def test_non_essential_goal_is_yellow_not_red(self):
        cl = client('Conservative', [goal(25000, '2028-10-13', priority='flexible')])
        r = self.run_trade(cl, SLOW, 'XBB.TO', 20000)
        self.assertEqual([c['severity'] for c in self.goal_alerts(r)], ['yellow'])
        self.assertNotEqual(r['verdict'], 'red')


class EsgRuleBiasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = json.loads((ROOT / 'config/assets.json').read_text())
        cls.policy = json.loads((ROOT / 'config/policy.json').read_text())

    def run_trade(self, cl, holdings, ticker, amount, side='buy'):
        return evaluate_trade(request(ticker, amount, side), cl, holdings, self.assets, self.policy)

    @staticmethod
    def esg_alerts(result):
        return [c for c in result['alerts'] if c['rule'].startswith('esg.')]

    @staticmethod
    def non_esg_checks(result):
        return sorted((c['rule'], round(c['gap_after'], 12), c['severity'])
                      for c in result['checks'] if not c['rule'].startswith('esg.'))

    def test_esg_applies_only_to_clients_who_asked_for_it(self):
        plain = self.run_trade(client('Balanced'), MIDDLE, 'XOM', 3000)
        screened = self.run_trade(client('Balanced', preferences=FOSSIL_FREE), MIDDLE, 'XOM', 3000)
        self.assertEqual(self.esg_alerts(plain), [])
        self.assertNotEqual(plain['verdict'], 'red')
        self.assertEqual(screened['verdict'], 'red')
        rules = {c['rule'] for c in self.esg_alerts(screened) if c['severity'] == 'red'}
        self.assertEqual(rules, {'esg.excluded_ticker.XOM', 'esg.excluded_sector_stock.XOM'})

    def test_esg_does_not_change_the_numerical_checks(self):
        # A screened client buying a non-excluded stock gets exactly the same allocation,
        # single-stock, crypto and HHI checks as an unscreened client.
        plain = self.run_trade(client('Balanced'), MIDDLE, 'JNJ', 3000)
        screened = self.run_trade(client('Balanced', preferences=FOSSIL_FREE), MIDDLE, 'JNJ', 3000)
        self.assertEqual(self.non_esg_checks(plain), self.non_esg_checks(screened))
        self.assertEqual(plain['verdict'], screened['verdict'])

    def test_selling_an_excluded_holding_is_never_flagged(self):
        holdings = dict(MIDDLE, XOM=3000)
        r = self.run_trade(client('Balanced', preferences=FOSSIL_FREE), holdings, 'XOM', 3000, side='sell')
        self.assertEqual(self.esg_alerts(r), [])
        improving = [c for c in r['checks'] if c['rule'].startswith('esg.') and c['effect'] == 'improves']
        self.assertTrue(improving)
        self.assertNotEqual(r['verdict'], 'red')

    def test_fund_lookthrough_is_yellow_and_respects_the_client_tolerance(self):
        energy_in_xiu = self.assets['XIU.TO']['sector_weights']['Energy']
        strict = self.run_trade(client('Balanced', preferences=FOSSIL_FREE), MIDDLE, 'XIU.TO', 5000)
        fund = [c for c in self.esg_alerts(strict) if c['rule'] == 'esg.fund_lookthrough.XIU.TO']
        self.assertEqual([c['severity'] for c in fund], ['yellow'])
        self.assertAlmostEqual(fund[0]['after'], energy_in_xiu)
        self.assertNotEqual(strict['verdict'], 'red')
        tolerant = json.loads(json.dumps(FOSSIL_FREE))
        tolerant['esg']['max_lookthrough_excluded_share'] = energy_in_xiu + 0.05
        relaxed = self.run_trade(client('Balanced', preferences=tolerant), MIDDLE, 'XIU.TO', 5000)
        self.assertEqual([c for c in self.esg_alerts(relaxed) if c['rule'].startswith('esg.fund')], [])

    def test_screened_fund_passes_where_broad_fund_is_flagged(self):
        # ESGV is the ESG-screened U.S. fund in the universe. If its fact sheet shows an excluded-
        # sector share within the tolerance it passes where XIU.TO is flagged; either way the
        # engine reports the fund's share and source rather than a verdict based on a label.
        r = self.run_trade(client('Balanced', preferences=FOSSIL_FREE), MIDDLE, 'ESGV', 5000)
        share = sum(self.assets['ESGV']['sector_weights'].get(s, 0.0) for s in ('Energy',))
        fund = [c for c in self.esg_alerts(r) if c['rule'] == 'esg.fund_lookthrough.ESGV']
        self.assertEqual(bool(fund), share > 0.05 + 1e-10)
        self.assertTrue(self.assets['ESGV']['esg']['screened'])


if __name__ == '__main__':
    unittest.main()

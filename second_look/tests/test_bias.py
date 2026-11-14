"""Bias test for the rule engine and the placement questionnaire.

The jury will ask whether Second Look "always recommends bonds". These tests show that it
does not: the NBF ranges have a minimum as well as a maximum, so an over-conservative
portfolio is flagged like an over-aggressive one; the verdict depends only on weights,
never on an asset's past returns; the same trade gets different verdicts under different
profiles; and the profile comes from the questionnaire, not from the holdings.

Portfolios below hold 8+ positions so that the engine's HHI rule (whole ETF positions,
no look-through, red above 0.25 when worsened) does not fire and hide the allocation
behaviour under test. Values are CAD; everything is fictional.
"""
import inspect
import json
import unittest
from pathlib import Path

from second_look.contracts import PROFILE_ORDER
from second_look.questionnaire import PERSONA_ANSWERS, place_investor
from second_look.rules import evaluate_trade

ROOT = Path(__file__).resolve().parents[1]
DATE = '2026-11-13'

# Growth client parked in bonds: cash 10%, fixed income 60%, equities 20%, gold 10%.
BOND_PARKED = {'CASH_CAD': 10000, 'XBB.TO': 20000, 'AGG': 20000, 'TLT': 20000,
               'XIU.TO': 5000, 'SPY': 5000, 'ESGV': 5000, 'QQQ': 5000, 'GLD': 10000}
# Balanced-looking portfolio: cash 20%, fixed income 30%, equities 40%, gold 10%.
MIDDLE = {'CASH_CAD': 20000, 'XBB.TO': 15000, 'AGG': 15000, 'SPY': 15000, 'XIU.TO': 15000,
          'ESGV': 10000, 'GLD': 10000}


def client(profile):
    return {'client_id': 'bias', 'profile': profile, 'horizon_years': 10,
            'loss_tolerance_fraction': 0.2, 'objectives': ['test'], 'preferences': {}}


def request(ticker, amount, side='buy'):
    return {'client_id': 'bias', 'decision_date': DATE, 'ticker': ticker, 'side': side, 'amount_cad': amount}


class BiasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = json.loads((ROOT / 'config/assets.json').read_text())
        cls.policy = json.loads((ROOT / 'config/policy.json').read_text())

    def run_trade(self, profile, holdings, ticker, amount, side='buy'):
        return evaluate_trade(request(ticker, amount, side), client(profile), holdings, self.assets, self.policy)

    @staticmethod
    def check(result, rule):
        return next(c for c in result['checks'] if c['rule'] == rule)

    @staticmethod
    def allocation_reds(result):
        return sorted(c['rule'] for c in result['checks']
                      if c['rule'].startswith('allocation.') and c['severity'] == 'red')

    # ------------------------------------------------------------ too conservative is flagged

    def test_growth_client_parked_in_bonds_is_red_when_buying_more_bonds(self):
        r = self.run_trade('Growth', BOND_PARKED, 'XBB.TO', 10000)
        self.assertEqual(r['verdict'], 'red')
        fi = self.check(r, 'allocation.fixed_income.max')
        self.assertEqual(fi['effect'], 'worsens')
        self.assertAlmostEqual(fi['after'], 0.70)
        self.assertEqual(self.allocation_reds(r), ['allocation.fixed_income.max'])
        self.assertNotEqual(self.check(r, 'hhi')['severity'], 'red')   # the red is allocation, not HHI

    def test_growth_client_selling_equities_below_the_minimum_is_red(self):
        holdings = {'CASH_CAD': 4000, 'XBB.TO': 14000, 'AGG': 14000, 'XIU.TO': 10000,
                    'SPY': 25000, 'ESGV': 10000, 'QQQ': 13000, 'GLD': 10000}   # equities 58%
        r = self.run_trade('Growth', holdings, 'SPY', 24000, side='sell')
        self.assertEqual(r['verdict'], 'red')
        eq = self.check(r, 'allocation.equities.min')
        self.assertEqual(eq['effect'], 'creates')
        self.assertAlmostEqual(eq['after'], 0.34)
        self.assertIn('allocation.equities.min', self.allocation_reds(r))

    def test_buying_equities_repairs_an_over_conservative_growth_portfolio(self):
        # Same bond-parked Growth client: buying equities reduces the shortfall -> green,
        # even though the fixed-income breach is still there (reported, unchanged).
        r = self.run_trade('Growth', BOND_PARKED, 'SPY', 10000)
        self.assertEqual(r['verdict'], 'green')
        self.assertEqual(self.check(r, 'allocation.equities.min')['effect'], 'improves')
        self.assertEqual(self.check(r, 'allocation.fixed_income.max')['effect'], 'unchanged')

    def test_bond_buying_is_flagged_for_aggressive_profiles_only(self):
        # The same bond-heavy portfolio and the same bond purchase under every profile.
        verdict, reds = {}, {}
        for p in PROFILE_ORDER:
            r = self.run_trade(p, BOND_PARKED, 'XBB.TO', 10000)
            verdict[p], reds[p] = r['verdict'], self.allocation_reds(r)
        self.assertEqual(reds['Income'], [])
        self.assertEqual(reds['Conservative'], [])
        self.assertEqual(reds['Balanced'], [])          # 70% vs 65% max: 5 points, yellow
        self.assertEqual(reds['Growth'], ['allocation.fixed_income.max'])
        self.assertEqual(reds['Maximum growth'], ['allocation.fixed_income.max'])
        self.assertEqual(verdict['Balanced'], 'yellow')
        self.assertEqual((verdict['Growth'], verdict['Maximum growth']), ('red', 'red'))

    # ------------------------------------------------------------ symmetry

    def test_equity_overshoot_and_shortfall_get_the_same_gap_and_severity(self):
        # Balanced, equities 47.5% (middle of 30-65), cash 20%. Moving 20 points of equities
        # up or down lands 2.5 points outside the range on each side.
        holdings = {'CASH_CAD': 20000, 'XBB.TO': 11000, 'AGG': 11000, 'TLT': 10500,
                    'XIU.TO': 9000, 'SPY': 20000, 'ESGV': 9500, 'QQQ': 9000}
        up = self.check(self.run_trade('Balanced', holdings, 'SPY', 20000), 'allocation.equities.max')
        down = self.check(self.run_trade('Balanced', holdings, 'SPY', 20000, side='sell'), 'allocation.equities.min')
        self.assertAlmostEqual(up['gap_after'], down['gap_after'], places=9)
        self.assertAlmostEqual(up['gap_after'], 0.025, places=9)
        self.assertEqual(up['effect'], down['effect'])
        self.assertEqual(up['severity'], down['severity'])
        # Note: the overall verdicts are not symmetric here, and that is correct: the sale
        # also pushes cash to 40%, above the 20% cash maximum. Proceeds have to go somewhere.

    def test_one_cash_funded_buy_from_a_compliant_portfolio_cannot_breach_an_allocation_by_more_than_the_cash_cap(self):
        # Property of the design: purchases are paid from cash, and cash is capped at 20% for
        # Income/Conservative/Balanced. A compliant Conservative client with equities at 30%
        # who spends all their cash on equities reaches 50%: 5 points over, yellow, never red.
        holdings = {'CASH_CAD': 20000, 'XBB.TO': 17000, 'AGG': 17000, 'TLT': 16000,
                    'XIU.TO': 10000, 'SPY': 10000, 'ESGV': 10000}
        r = self.run_trade('Conservative', holdings, 'SPY', 20000)
        self.assertEqual(self.check(r, 'allocation.equities.max')['severity'], 'yellow')
        self.assertEqual(r['verdict'], 'yellow')
        # Allocation reds therefore come from sales, pre-existing breaches, or the hard
        # single-stock / crypto caps, which is why Robert/NVDA is red (single stock), not equities.
        r = self.run_trade('Conservative', holdings, 'NVDA', 20000)
        self.assertEqual(r['verdict'], 'red')
        self.assertEqual(self.check(r, 'single_stock.NVDA')['severity'], 'red')

    # ------------------------------------------------------------ profile, not holdings, decides

    def test_same_trade_different_profiles_different_verdicts(self):
        verdicts = {p: self.run_trade(p, MIDDLE, 'NVDA', 12000)['verdict'] for p in PROFILE_ORDER}
        # 12% in one stock: over the 5% cap (Income, Conservative) and the 10% cap
        # (Balanced, Growth); under the 15% cap of Maximum growth.
        for p in ('Income', 'Conservative', 'Balanced', 'Growth'):
            self.assertEqual(verdicts[p], 'red', p)
        self.assertNotEqual(verdicts['Maximum growth'], 'red')

    def test_verdict_depends_on_weights_not_on_past_performance(self):
        # NVDA and JNJ have very different five-year histories. Same class, same weight:
        # identical checks, identical verdict.
        holdings = {'CASH_CAD': 20000, 'XBB.TO': 20000, 'AGG': 20000, 'SPY': 20000, 'XIU.TO': 20000}
        a = self.run_trade('Balanced', holdings, 'NVDA', 8000)
        b = self.run_trade('Balanced', holdings, 'JNJ', 8000)
        self.assertEqual(a['verdict'], b['verdict'])
        strip = lambda r: sorted((c['rule'].replace('NVDA', 'X').replace('JNJ', 'X'),
                                  round(c['gap_after'], 12), c['severity']) for c in r['checks'])
        self.assertEqual(strip(a), strip(b))
        # Two equity ETFs with different histories: same class weights after the trade.
        c = self.run_trade('Balanced', holdings, 'QQQ', 8000)
        d = self.run_trade('Balanced', holdings, 'ESGV', 8000)
        self.assertEqual(c['verdict'], d['verdict'])
        self.assertEqual(c['weights_after'], d['weights_after'])

    def test_aggressive_holdings_do_not_upgrade_a_conservative_profile(self):
        # A Conservative client already holding 70% equities is still judged as Conservative.
        # The spectrum says the portfolio looks like Growth; that is context and cannot
        # change the verdict or the stored profile.
        holdings = {'CASH_CAD': 10000, 'XBB.TO': 10000, 'AGG': 10000, 'SPY': 15000, 'QQQ': 15000,
                    'XIU.TO': 15000, 'ESGV': 15000, 'NVDA': 5000, 'AAPL': 5000}
        r = self.run_trade('Conservative', holdings, 'SPY', 5000)
        self.assertEqual(r['profile'], 'Conservative')
        self.assertEqual(r['verdict'], 'red')
        self.assertEqual(self.check(r, 'allocation.equities.max')['effect'], 'worsens')
        self.assertIn(r['classification']['before']['categories'][0], ('Growth', 'Maximum growth'))
        self.assertEqual(r['classification']['target_profile'], 'Conservative')

    # ------------------------------------------------------------ questionnaire side

    def test_placement_ignores_holdings_entirely(self):
        # place_investor takes answers only: no holdings, prices or returns can reach it.
        self.assertEqual(list(inspect.signature(place_investor).parameters), ['answers'])

    def test_questionnaire_covers_the_whole_spectrum(self):
        lowest = {'horizon_years': 1, 'capacity': 'severe', 'willingness': 'avoid',
                  'objective': 'preserve', 'experience': 'none'}
        highest = {'horizon_years': 30, 'capacity': 'none', 'willingness': 'any',
                   'objective': 'max_growth', 'experience': 'professional'}
        self.assertEqual(place_investor(lowest)['profile'], 'Income')
        self.assertEqual(place_investor(highest)['profile'], 'Maximum growth')
        reached = {place_investor(PERSONA_ANSWERS[p])['profile'] for p in PERSONA_ANSWERS}
        self.assertEqual(reached, {'Conservative', 'Balanced', 'Growth'})


if __name__ == '__main__':
    unittest.main()

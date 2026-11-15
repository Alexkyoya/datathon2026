"""Unit tests for rule A (goal coverage) and rule B (ESG preferences)."""
import copy
import json
import unittest
from pathlib import Path

from second_look.goals import allocate, expand_goals
from second_look.rules import evaluate_trade

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-11-13'


def fixture_asset(asset_class='equities', single=False, sector=None, sector_weights=None, horizon=None, esg=None):
    return {'name': 'fixture', 'currency': 'CAD', 'asset_class': asset_class, 'instrument_type': 'fixture',
            'market': 'test', 'sector': sector, 'is_single_stock': single, 'is_crypto': False,
            'sector_weights': sector_weights if sector_weights is not None or asset_class != 'equities' else None,
            'mapping_source': 'test fixture', 'esg': esg, 'horizon': horizon}


def h(mdd, months, recovered=True):
    return {'max_drawdown': mdd, 'recovery_months': months if recovered else None, 'recovered': recovered,
            'window': 'test', 'source': 'test fixture'}


class Base(unittest.TestCase):
    def setUp(self):
        self.assets = json.loads((ROOT/'config/assets.json').read_text())
        self.policy = json.loads((ROOT/'config/policy.json').read_text())
        self.assets.update({
            'CASHLIKE': fixture_asset('cash', sector_weights={}, horizon=h(0.0, 0.0)),
            'FAST': fixture_asset('fixed_income', sector_weights={}, horizon=h(-0.10, 6.0)),
            'SLOW': fixture_asset(sector_weights={'Information Technology': 1.0}, horizon=h(-0.20, 30.0)),
            'NEVER': fixture_asset(sector_weights={'Information Technology': 1.0}, horizon=h(-0.50, None, False)),
            'NODATA': fixture_asset(sector_weights={'Information Technology': 1.0},
                                    horizon={'max_drawdown': None, 'recovery_months': None, 'recovered': None,
                                             'reason': 'no price history'}),
            'OILCO': fixture_asset(single=True, sector='Energy', sector_weights={'Energy': 1.0}, horizon=h(-0.3, 12)),
            'OILFUND': fixture_asset(sector_weights={'Energy': 0.20, 'Financials': 0.80}, horizon=h(-0.2, 12)),
            'LOWOIL': fixture_asset(sector_weights={'Energy': 0.03, 'Financials': 0.97}, horizon=h(-0.2, 12)),
            'NOSECT': fixture_asset(sector_weights=None, horizon=h(-0.2, 12)),
        })
        self.client = {'client_id': 't', 'profile': 'Maximum growth', 'horizon_years': 10,
                       'loss_tolerance_fraction': .4, 'objectives': ['growth'], 'preferences': {}, 'goals': []}
        self.holdings = {'CASH_CAD': 50000, 'SLOW': 50000}

    def goal(self, amount, date, priority='essential', name='Goal'):
        return {'name': name, 'type': 'one_off', 'amount_cad': amount, 'target_date': date,
                'priority': priority, 'source': 'test'}

    def trade(self, ticker, amount, side='buy', holdings=None, client=None):
        req = {'client_id': 't', 'decision_date': DAY, 'ticker': ticker, 'side': side, 'amount_cad': amount}
        return evaluate_trade(req, client or self.client, holdings or self.holdings, self.assets, self.policy)

    def fired(self, r, prefix):
        return [c for c in r['alerts'] if c['rule'].startswith(prefix)]


class GoalTests(Base):
    def test_recovery_longer_than_time_to_goal_not_counted(self):
        items, _, _ = expand_goals([self.goal(40000, '2027-11-13')], DAY, self.policy)
        cov = allocate(items, {'CASH_CAD': 1000, 'SLOW': 100000}, self.assets)[0]
        self.assertAlmostEqual(cov['coverage'], 1000/40000)          # SLOW needs 30 months, goal is 12 away
        items, _, _ = expand_goals([self.goal(40000, '2029-11-13')], DAY, self.policy)
        cov = allocate(items, {'CASH_CAD': 1000, 'SLOW': 100000}, self.assets)[0]
        self.assertEqual(cov['coverage'], 1.0)                        # 36 months away: counted at 80%
        self.assertAlmostEqual(cov['holdings_used'][1]['counted_cad'], 39000)
        self.assertAlmostEqual(cov['holdings_used'][1]['market_value_cad'], 39000/0.8)

    def test_unrecovered_and_missing_data_never_counted(self):
        items, _, _ = expand_goals([self.goal(10000, '2030-11-01')], DAY, self.policy)
        cov = allocate(items, {'CASH_CAD': 0, 'NEVER': 1e6, 'NODATA': 1e6}, self.assets)[0]
        self.assertEqual(cov['coverage'], 0.0)

    def test_cash_like_always_usable_at_full_value(self):
        items, _, _ = expand_goals([self.goal(5000, DAY)], DAY, self.policy)
        cov = allocate(items, {'CASH_CAD': 2000, 'CASHLIKE': 3000, 'FAST': 1e6}, self.assets)[0]
        self.assertEqual(cov['coverage'], 1.0)
        self.assertEqual([u['ticker'] for u in cov['holdings_used']], ['CASH_CAD', 'CASHLIKE'])

    def test_each_dollar_used_once_earliest_goal_first(self):
        goals = [self.goal(30000, '2027-06-01', name='B'), self.goal(30000, '2027-01-01', name='A')]
        items, _, _ = expand_goals(goals, DAY, self.policy)
        a, b = allocate(items, {'CASH_CAD': 40000}, self.assets)
        self.assertEqual([i['goal'] for i in items], ['A', 'B'])
        self.assertEqual((a['coverage'], b['coverage']), (1.0, 10000/30000))

    def test_red_and_yellow_thresholds(self):
        cases = [('2027-11-13', 'essential', 'red'),        # 12 months, essential
                 ('2028-11-12', 'essential', 'red'),        # just under 24 months
                 ('2029-01-13', 'essential', 'yellow'),     # 26 months
                 ('2027-11-13', 'flexible', 'yellow')]      # flexible goals are never red
        for date, priority, expected in cases:
            self.client['goals'] = [self.goal(60000, date, priority)]
            h = {'CASH_CAD': 60000, 'SLOW': 1000}
            r = self.trade('SLOW', 20000, holdings=h)
            alerts = self.fired(r, 'goal.')
            self.assertEqual([a['severity'] for a in alerts], [expected], (date, priority))
            self.assertEqual(alerts[0]['effect'], 'creates')
            self.assertAlmostEqual(alerts[0]['before'], 1.0)
            self.assertLess(alerts[0]['after'], 1.0)
            self.assertIn('worst past fall', alerts[0]['message'])

    def test_trade_keeping_coverage_is_not_flagged(self):
        self.client['goals'] = [self.goal(10000, '2027-11-13')]
        r = self.trade('SLOW', 20000)                # 30,000 cash left still covers 10,000
        self.assertEqual(self.fired(r, 'goal.'), [])
        self.assertEqual(r['goals']['installments'][0]['coverage_after'], 1.0)

    def test_improving_trades_never_flagged(self):
        self.client['goals'] = [self.goal(20000, DAY, name='A'), self.goal(60000, '2030-01-01', name='B')]
        h = {'CASH_CAD': 10000, 'SLOW': 50000, 'NEVER': 40000}
        for ticker, amount, side in [('SLOW', 30000, 'sell'), ('NEVER', 40000, 'sell'), ('CASHLIKE', 5000, 'buy')]:
            r = self.trade(ticker, amount, side, holdings=h)
            self.assertEqual(self.fired(r, 'goal.'), [], ticker)

    def test_selling_shifts_money_to_earlier_goal_without_flag(self):
        # Selling SLOW fills the earlier goal; the later goal's own share can fall, but nothing needed by any
        # date is lost, so the rule does not flag it.
        self.client['goals'] = [self.goal(20000, DAY, name='Now'), self.goal(20000, '2030-01-01', name='Later')]
        h = {'CASH_CAD': 10000, 'SLOW': 25000}
        r = self.trade('SLOW', 25000, 'sell', holdings=h)
        rows = {i['goal']: i for i in r['goals']['installments']}
        self.assertLess(rows['Later']['coverage_after'], rows['Later']['coverage_before'])
        self.assertEqual(self.fired(r, 'goal.'), [])

    def test_recurring_expansion_and_other_income_offset(self):
        self.client['income'] = {'other_income_cad_per_year': 25000, 'source': 'test'}
        goal = {'name': 'Retirement', 'type': 'recurring', 'amount_cad_per_year': 50000, 'start_date': '2025-07-01',
                'years': 4, 'funded_by_other_income_cad_per_year': 20000, 'indexed_to_inflation': False,
                'priority': 'essential', 'source': 'test'}
        items, later, _ = expand_goals([goal], DAY, self.policy)
        self.assertEqual([i['date'] for i in items], ['2027-07-01', '2028-07-01'])  # 2025 and 2026 already past
        self.assertEqual([i['amount_cad'] for i in items], [30000, 30000])
        self.assertEqual([i['installment'] for i in items], [3, 4])
        goal['funded_by_other_income_cad_per_year'] = 60000                         # never below 0
        self.assertEqual(expand_goals([goal], DAY, self.policy)[0][0]['amount_cad'], 0)
        del goal['years']
        goal.update(end_date='2028-01-01', funded_by_other_income_cad_per_year=0)
        self.assertEqual([i['date'] for i in expand_goals([goal], DAY, self.policy)[0]], ['2027-07-01'])

    def test_planning_horizon_keeps_later_installments_as_long_term(self):
        goal = {'name': 'Retirement', 'type': 'recurring', 'amount_cad_per_year': 10000, 'start_date': '2027-01-01',
                'years': 30, 'indexed_to_inflation': False, 'priority': 'essential', 'source': 'test'}
        items, later, end = expand_goals([goal], DAY, self.policy)
        self.assertEqual(end, '2031-11-13')
        self.assertEqual(len(items), 5)
        self.assertEqual(later[0]['installments'], 25)

    def test_inflation_indexing(self):
        goal = {'name': 'R', 'type': 'recurring', 'amount_cad_per_year': 10000, 'start_date': '2028-11-13',
                'years': 2, 'indexed_to_inflation': True, 'priority': 'essential', 'source': 'test'}
        items, _, _ = expand_goals([goal], DAY, self.policy)
        infl = self.policy['inflation_assumption']
        self.assertAlmostEqual(items[0]['amount_cad'], 10000 * (1 + infl) ** (731/365.25))
        self.assertGreater(items[1]['amount_cad'], items[0]['amount_cad'])
        goal['indexed_to_inflation'] = False
        self.assertEqual(expand_goals([goal], DAY, self.policy)[0][0]['amount_cad'], 10000)

    def test_no_goals(self):
        self.client.pop('goals')
        r = self.trade('SLOW', 50000)
        self.assertFalse(r['goals']['applied'])
        self.assertIn('Client has no goals; goal coverage not applied.', r['coverage']['notes'])
        self.assertEqual(self.fired(r, 'goal.'), [])

    def test_missing_horizon_data_is_unresolved_yellow(self):
        self.client['goals'] = [self.goal(1000, '2027-11-13')]
        r = self.trade('NODATA', 1000)
        self.assertEqual(r['coverage']['unresolved'][0]['rule'], 'goal_coverage.NODATA')
        self.assertEqual(r['coverage']['unresolved'][0]['handling'], 'yellow')
        self.assertEqual(r['verdict'], 'yellow')


class EsgTests(Base):
    def setUp(self):
        super().setUp()
        self.client['preferences'] = {'esg': {'excluded_tickers': ['XOM'], 'excluded_sectors': ['Energy'],
                                              'max_lookthrough_excluded_share': 0.05,
                                              'prefer_esg_screened_funds': True, 'source': 'test'}}

    def test_excluded_ticker_red(self):
        r = self.trade('XOM', 1000)
        self.assertEqual(r['verdict'], 'red')
        self.assertEqual(self.fired(r, 'esg.excluded_ticker.XOM')[0]['severity'], 'red')

    def test_single_stock_in_excluded_sector_red(self):
        r = self.trade('OILCO', 1000)
        self.assertEqual([a['rule'] for a in self.fired(r, 'esg.')], ['esg.excluded_sector_stock.OILCO'])
        self.assertEqual(r['verdict'], 'red')

    def test_legacy_top_level_exclusions_use_same_screen(self):
        self.client['preferences'] = {'excluded_tickers': ['XOM']}
        self.assertEqual(self.fired(self.trade('XOM', 1), 'esg.excluded_ticker.XOM')[0]['severity'], 'red')

    def test_fund_lookthrough_above_and_below_threshold(self):
        above = self.trade('OILFUND', 1000)
        alert = self.fired(above, 'esg.fund_lookthrough.OILFUND')[0]
        self.assertEqual(alert['severity'], 'yellow')
        self.assertAlmostEqual(alert['fund_excluded_share'], 0.20)
        self.assertEqual(alert['limit'], 0.05)
        self.assertEqual(above['verdict'], 'yellow')
        below = self.trade('LOWOIL', 1000)
        self.assertEqual(self.fired(below, 'esg.'), [])
        self.client['preferences']['esg']['max_lookthrough_excluded_share'] = 0.25
        self.assertEqual(self.fired(self.trade('OILFUND', 1000), 'esg.'), [])

    def test_portfolio_level_exposure_above_max(self):
        r = self.trade('LOWOIL', 1000, holdings={'CASH_CAD': 50000, 'OILCO': 2400, 'SLOW': 47600})
        alerts = self.fired(r, 'esg.portfolio_lookthrough')
        self.assertEqual(alerts, [])  # 2.4% + 0.03% stays under 5%
        r = self.trade('OILFUND', 20000, holdings={'CASH_CAD': 50000, 'OILCO': 2400, 'SLOW': 47600})
        alert = self.fired(r, 'esg.portfolio_lookthrough')[0]
        self.assertEqual(alert['severity'], 'yellow')
        self.assertAlmostEqual(alert['after'], (2400 + 20000*0.2)/100000)

    def test_missing_sector_data_is_unresolved(self):
        r = self.trade('NOSECT', 1000)
        items = [u for u in r['coverage']['unresolved'] if u['rule'] == 'esg.fund_lookthrough.NOSECT']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['handling'], 'yellow')
        self.assertEqual(r['verdict'], 'yellow')

    def test_selling_excluded_asset_never_flagged(self):
        h = {'CASH_CAD': 10000, 'XOM': 20000, 'OILFUND': 20000, 'SLOW': 50000}
        for ticker in ('XOM', 'OILFUND'):
            r = self.trade(ticker, 10000, 'sell', holdings=h)
            self.assertEqual(self.fired(r, 'esg.'), [], ticker)
            self.assertTrue(any(c['rule'].startswith('esg.') and c['effect'] == 'improves' for c in r['checks']))

    def test_screened_fund_preference_is_informational(self):
        r = self.trade('ESGV', 1000)
        self.assertIn('ESG-screened', r['esg']['screened_fund_note'])
        self.assertIn('not an ESG-screened', self.trade('SPY', 1000)['esg']['screened_fund_note'])
        self.assertEqual(self.fired(self.trade('SPY', 1000), 'esg.'), [])


class PolicyTests(unittest.TestCase):
    def test_every_threshold_has_source_and_rationale(self):
        policy = json.loads((ROOT/'config/policy.json').read_text())
        sources = policy['threshold_sources']
        keys = ['yellow_buffer', 'hhi_watch', 'hhi_max', 'inflation_assumption', 'unresolved_handling', 'data_quality']
        keys += [f'goal_liquidity.{k}' for k in policy['goal_liquidity']]
        keys += [f'esg.{k}' for k in policy['esg']]
        keys += [f'profiles.*.{k}' for k in next(iter(policy['profiles'].values()))]
        for key in keys:
            self.assertIn(key, sources)
            self.assertIn(sources[key]['source'], ('NBF Fall 2026', 'Second Look team assumption'))
            self.assertTrue(sources[key]['rationale'])

    def test_unresolved_default_is_yellow(self):
        policy = json.loads((ROOT/'config/policy.json').read_text())
        self.assertEqual(policy['unresolved_handling']['default'], 'yellow')


if __name__ == '__main__':
    unittest.main()


class MessageFormatTests(unittest.TestCase):
    def test_limits_and_tickers_display_exactly(self):
        from second_look.esg import pct
        from second_look.rules import message
        self.assertEqual((pct(0.035), pct(0.05), pct(0.0)), ('3.5%', '5%', '0%'))
        c = {'rule': 'single_stock.NVDA', 'unit': 'weight', 'bound': 'max', 'before': 0.04, 'after': 0.14, 'limit': 0.1}
        self.assertTrue(message(c).startswith('NVDA position:'))
        self.assertTrue(message(dict(c, rule='single_stock.XIU.TO')).startswith('XIU.TO position:'))

"""Client schema: invalid inputs are rejected with a clear message."""
import copy
import json
import unittest
from pathlib import Path

from second_look.contracts import validate_client
from second_look.rules import evaluate_trade

ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-11-13'


class ClientSchemaTests(unittest.TestCase):
    def setUp(self):
        self.assets = json.loads((ROOT/'config/assets.json').read_text())
        self.policy = json.loads((ROOT/'config/policy.json').read_text())
        self.client = json.loads((ROOT/'examples/clients/robert.json').read_text())

    def rejects(self, mutate, message):
        client = copy.deepcopy(self.client)
        mutate(client)
        with self.assertRaises(ValueError) as ctx:
            validate_client(client, DAY, self.assets)
        self.assertIn(message, str(ctx.exception))

    def goal(self, c, name):
        return next(g for g in c['goals'] if g['name'] == name)

    def test_stored_personas_are_valid(self):
        for path in (ROOT/'examples/clients').glob('*.json'):
            validate_client(json.loads(path.read_text()), DAY, self.assets)

    def test_negative_amounts(self):
        self.rejects(lambda c: self.goal(c, 'Roof repair').update(amount_cad=-1), 'amount_cad must be finite and >= 0')
        self.rejects(lambda c: self.goal(c, 'Retirement income').update(amount_cad_per_year=-5),
                     'amount_cad_per_year must be finite')
        self.rejects(lambda c: c['income'].update(other_income_cad_per_year=-1), 'other_income_cad_per_year')

    def test_past_target_date(self):
        self.rejects(lambda c: self.goal(c, 'Roof repair').update(target_date='2025-01-01'), 'is in the past')

    def test_bad_dates_and_goal_end_before_start(self):
        self.rejects(lambda c: self.goal(c, 'Roof repair').update(target_date='2027/04/01'), 'must be a date YYYY-MM-DD')
        def end_first(c):
            g = self.goal(c, 'Retirement income'); g.pop('years'); g['end_date'] = '2027-01-01'
        self.rejects(end_first, 'end_date 2027-01-01 is not after start_date 2027-07-01')
        self.rejects(lambda c: self.goal(c, 'Retirement income').update(end_date='2057-07-01'),
                     'give exactly one of years or end_date')
        self.rejects(lambda c: self.goal(c, 'Retirement income').update(years=0), 'years must be a whole number')

    def test_goal_type_priority_and_flags(self):
        self.rejects(lambda c: self.goal(c, 'Roof repair').update(type='monthly'), 'type must be one_off or recurring')
        self.rejects(lambda c: self.goal(c, 'Roof repair').update(priority='urgent'), 'priority must be essential')
        self.rejects(lambda c: self.goal(c, 'Retirement income').update(indexed_to_inflation='yes'),
                     'indexed_to_inflation must be true or false')
        self.rejects(lambda c: c['goals'].append({'type': 'one_off'}), 'needs a name')

    def test_other_income_cannot_be_assigned_twice(self):
        self.rejects(lambda c: self.goal(c, 'Retirement income').update(funded_by_other_income_cad_per_year=10000),
                     'Goals assign CAD 10,000/year of other income')

    def test_tickers_outside_universe(self):
        self.rejects(lambda c: c['preferences'].update(esg={'excluded_tickers': ['XLK']}),
                     'Excluded ticker XLK is not in the asset universe')
        self.rejects(lambda c: c['preferences'].update(esg={'excluded_sectors': ['Oil']}), 'Unknown sector exclusion: Oil')
        self.rejects(lambda c: c['preferences'].update(esg={'max_lookthrough_excluded_share': 1.5}), 'between 0 and 1')
        self.rejects(lambda c: c['preferences'].update(esg={'colour': 'green'}), 'unknown fields: colour')

    def test_trade_and_holdings_outside_universe(self):
        holdings = {'CASH_CAD': 1000.0, 'SPY': 1000.0}
        req = {'client_id': 'robert', 'decision_date': DAY, 'ticker': 'XEF.TO', 'side': 'buy', 'amount_cad': 100}
        with self.assertRaises(ValueError) as ctx:
            evaluate_trade(req, self.client, holdings, self.assets, self.policy)
        self.assertIn('XEF.TO is not in the asset universe (the provided datathon data)', str(ctx.exception))
        req['ticker'] = 'SPY'
        with self.assertRaises(ValueError) as ctx:
            evaluate_trade(req, self.client, dict(holdings, XIT_TO=5.0), self.assets, self.policy)
        self.assertIn('XIT_TO is not in the asset universe', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()

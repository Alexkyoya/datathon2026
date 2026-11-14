import copy
import json
import math
import random
import unittest
from pathlib import Path
import pandas as pd
from second_look.cleaning import clean_data
from second_look.__main__ import make_snapshot
from second_look.rules import evaluate_trade
from second_look.service import evaluate_from_snapshot

ROOT = Path(__file__).resolve().parents[1]


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.assets=json.loads((ROOT/'config/assets.json').read_text())
        self.policy=json.loads((ROOT/'config/policy.json').read_text())
        self.client={'client_id':'test','profile':'Balanced','horizon_years':8,
                     'loss_tolerance_fraction':.2,'objectives':['growth'],'preferences':{}}
        self.holdings={'CASH_CAD':15000,'XBB.TO':16000,'AGG':14000,'TLT':14000,
                       'SPY':12000,'XIU.TO':12000,'ESGV':11000,'GLD':6000}

    def run_trade(self,ticker='SPY',amount=4000,side='buy',holdings=None):
        req={'client_id':'test','decision_date':'2026-11-13','ticker':ticker,'side':side,'amount_cad':amount}
        return evaluate_trade(req,self.client,self.holdings if holdings is None else holdings,self.assets,self.policy)

    def test_three_colors(self):
        self.assertEqual(self.run_trade()['verdict'],'green')
        self.assertEqual(self.run_trade('GLD',14000)['verdict'],'yellow')
        self.assertEqual(self.run_trade('TSLA',12000)['verdict'],'red')

    def test_stock_boundary_and_crypto_zero_cap(self):
        self.assertNotEqual(self.run_trade('TSLA',10000)['verdict'],'red')
        self.assertEqual(self.run_trade('TSLA',10000.01)['verdict'],'red')
        self.assertEqual(self.run_trade('BTC-USD',1)['verdict'],'red')

    def test_allocation_five_percentage_point_boundary(self):
        # Selling SPY increases cash from 15% to 25% / 25.01%.
        self.assertEqual(self.run_trade('SPY',10000,'sell')['verdict'],'yellow')
        self.assertEqual(self.run_trade('SPY',10001,'sell')['verdict'],'red')

    def test_minimum_allocation(self):
        r=self.run_trade('SPY',10000,'sell')
        c=next(c for c in r['checks'] if c['rule']=='allocation.equities.min')
        self.assertAlmostEqual(c['after'],.25)
        self.assertEqual(c['effect'],'creates')

    def test_reduction_green_without_new_breach(self):
        h=dict(self.holdings);h['CASH_CAD']=1000;h['TSLA']=14000
        r=self.run_trade('TSLA',2000,'sell',h)
        self.assertEqual(r['verdict'],'green')
        stock=next(c for c in r['breaches'] if c['rule']=='single_stock.TSLA')
        self.assertAlmostEqual(stock['after'],.12)
        self.assertEqual(stock['effect'],'reduces')

    def test_improvement_does_not_hide_new_breach(self):
        h=dict(self.holdings);h['CASH_CAD']=30000;h['XBB.TO']=1000
        r=self.run_trade('TSLA',12000,holdings=h)
        self.assertEqual(r['verdict'],'red')
        self.assertEqual(r['effect'],'mixed')

    def test_hhi_worsening_concentration(self):
        h={'CASH_CAD':20000,'SPY':40000,'XBB.TO':40000}
        r=self.run_trade('SPY',5000,holdings=h)
        hhi=next(c for c in r['checks'] if c['rule']=='hhi')
        self.assertEqual(hhi['severity'],'red')

    def test_existing_unchanged_breach_requires_review(self):
        h=dict(self.holdings);h['CASH_CAD']=3000;h['TSLA']=12000
        r=self.run_trade('SPY',100,holdings=h)
        self.assertEqual(r['verdict'],'yellow')

    def test_preference_and_cash_reserve(self):
        self.client['preferences']={'excluded_tickers':['XOM']}
        self.assertEqual(self.run_trade('XOM',1)['verdict'],'red')
        self.client['preferences']={'minimum_cash_cad':12000}
        self.assertEqual(self.run_trade()['verdict'],'red')

    def test_incomplete_sector_and_esg_not_green(self):
        self.client['profile']='Growth'
        # SPY/XIU/ESGV/QQQ now carry sourced sector weights; XEF.TO still has no look-through.
        self.assertEqual(self.run_trade()['verdict'],'green')
        r=self.run_trade('XEF.TO',4000)
        self.assertIsNone(r['verdict'])
        self.assertEqual(r['evaluation_status'],'partial')
        self.client['profile']='Balanced'
        self.client['preferences']={'esg_required':True}
        self.assertIsNone(self.run_trade()['verdict'])
        self.assertEqual(self.run_trade('TSLA',12000)['verdict'],'red')

    def test_etf_is_exempt_stock_cap_but_kept_in_hhi(self):
        r=self.run_trade()
        self.assertFalse(any(c['rule']=='single_stock.SPY' for c in r['checks']))
        self.assertAlmostEqual(r['hhi_after'],sum(v*v for v in r['position_weights_after'].values()))

    def test_reject_invalid_orders_and_no_mutation(self):
        original=copy.deepcopy(self.holdings)
        for amount in [0,-1,float('nan'),float('inf'),True,15001]:
            with self.assertRaises(ValueError): self.run_trade(amount=amount)
        with self.assertRaises(ValueError): self.run_trade('SPY',12001,'sell')
        self.run_trade()
        self.assertEqual(original,self.holdings)

    def test_profile_context_does_not_reclassify(self):
        first=self.run_trade()['verdict']
        self.client.update(horizon_years=1,loss_tolerance_fraction=.01,objectives=['capital_preservation'])
        self.assertEqual(self.run_trade()['verdict'],first)
        self.assertEqual(self.run_trade()['profile'],'Balanced')

    def test_unknown_preference_and_missing_cash_do_not_produce_green(self):
        self.client['preferences']={'unimplemented_restriction':True}
        self.assertIsNone(self.run_trade()['verdict'])
        h=dict(self.holdings);del h['CASH_CAD']
        with self.assertRaises(ValueError):self.run_trade(holdings=h)

    def test_conservation_random_valid_trades(self):
        rng=random.Random(42)
        for _ in range(100):
            r=self.run_trade(rng.choice(list(self.assets)),rng.uniform(.01,15000))
            self.assertAlmostEqual(sum(r['holdings_cad_before'].values()),sum(r['holdings_cad_after'].values()))
            self.assertAlmostEqual(sum(r['weights_after'].values()),1)
            self.assertTrue(all(v>=0 for v in r['holdings_cad_after'].values()))
            self.assertEqual(r['activate_agents'],r['verdict']=='red')
            json.dumps(r,allow_nan=False)


class CleaningTests(unittest.TestCase):
    def setUp(self):
        assets=json.loads((ROOT/'config/assets.json').read_text())
        self.quality=json.loads((ROOT/'config/policy.json').read_text())['data_quality']
        self.assets={t:assets[t] for t in ['SPY','XIU.TO','BTC-USD']}
        self.prices=pd.DataFrame([
            {'date':d,'ticker':t,'close':p,'adj_close':p*.9,'volume':100,'currency':self.assets[t]['currency']}
            for t,d,p in [('SPY','2026-11-06',100),('SPY','2026-11-09',110),
                          ('XIU.TO','2026-11-06',50),('XIU.TO','2026-11-09',51),
                          ('BTC-USD','2026-11-06',1000),('BTC-USD','2026-11-07',1010),
                          ('BTC-USD','2026-11-08',1020),('BTC-USD','2026-11-09',1030)]])
        self.fx=pd.DataFrame({'date':['2026-11-06','2026-11-09'],'usdcad':[1.4,1.5]})

    def test_cad_fx_direction_weekends_and_crypto(self):
        d,_=clean_data(self.prices,self.fx,self.assets,self.quality)
        sat=d[(d.ticker=='SPY') & d.snapshot_at.eq('2026-11-08')].iloc[0]
        self.assertEqual(sat.close_cad,140)
        self.assertTrue(sat.price_carried)
        cad=d[d.ticker=='XIU.TO'].iloc[0]
        self.assertEqual(cad.close_cad,50)
        coin=d[(d.ticker=='BTC-USD') & d.snapshot_at.eq('2026-11-08')].iloc[0]
        self.assertFalse(coin.price_carried)
        self.assertAlmostEqual(coin.close_cad,1414)
        self.assertTrue((d.price_available_at<=d.snapshot_at).all())

    def test_no_backfill_when_fx_starts_late(self):
        d,_=clean_data(self.prices,self.fx.iloc[1:],self.assets,self.quality)
        self.assertTrue(d[(d.ticker=='SPY') & (d.snapshot_at<'2026-11-10')].close_cad.isna().all())
        self.assertTrue(d[d.ticker=='XIU.TO'].close_cad.notna().all())

    def test_adjusted_returns_include_fx(self):
        d,_=clean_data(self.prices,self.fx,self.assets,self.quality)
        row=d[(d.ticker=='SPY') & d.snapshot_at.eq('2026-11-10')].iloc[0]
        self.assertAlmostEqual(row.return_cad_daily,(110*1.5)/(100*1.4)-1)

    def test_missing_and_stale_observations_are_flagged_without_future_fill(self):
        prices=self.prices.copy()
        # A distant crypto observation extends the calendar; old security/FX
        # observations become stale while the new crypto price remains current.
        last=prices.iloc[-1:].copy();last['date']='2026-11-25'
        prices=pd.concat([prices,last],ignore_index=True)
        aligned,report=clean_data(prices,self.fx.iloc[1:],self.assets,self.quality)
        early=aligned[(aligned.ticker=='SPY') & aligned.snapshot_at.eq('2026-11-07')].iloc[0]
        self.assertTrue(early.fx_missing)
        self.assertFalse(early.usable)
        self.assertTrue(pd.isna(early.close_cad))
        stale=aligned[(aligned.ticker=='SPY') & aligned.snapshot_at.eq('2026-11-26')].iloc[0]
        self.assertTrue(stale.price_stale and stale.fx_stale)
        self.assertFalse(stale.usable)
        coin=aligned[(aligned.ticker=='BTC-USD') & aligned.snapshot_at.eq('2026-11-26')].iloc[0]
        self.assertFalse(coin.price_stale)
        self.assertTrue(coin.fx_stale)
        self.assertTrue(pd.isna(coin.return_cad_daily))
        self.assertGreater(report['unusable_rows'],0)

    def test_duplicate_currency_bad_price_rejected(self):
        variants=[pd.concat([self.prices,self.prices.iloc[:1]]),self.prices.copy(),self.prices.copy()]
        variants[1].loc[0,'currency']='CAD'
        variants[2].loc[0,'adj_close']=-1
        for p in variants:
            with self.assertRaises(ValueError):clean_data(p,self.fx,self.assets,self.quality)

    def test_source_data_quality(self):
        assets=json.loads((ROOT/'config/assets.json').read_text())
        _,r=clean_data(pd.read_csv(ROOT/'data/raw/prices_daily.csv'),pd.read_csv(ROOT/'data/raw/fx_usdcad_daily.csv'),assets,self.quality)
        self.assertEqual(r['asset_count'],31)
        self.assertEqual(r['unusable_rows'],0)

    def test_dated_snapshot_and_future_quote_rejected(self):
        client=json.loads((ROOT/'examples/client.json').read_text())
        assets=json.loads((ROOT/'config/assets.json').read_text())
        policy=json.loads((ROOT/'config/policy.json').read_text())
        aligned,report=clean_data(pd.read_csv(ROOT/'data/raw/prices_daily.csv'),pd.read_csv(ROOT/'data/raw/fx_usdcad_daily.csv'),assets,self.quality)
        report.update(sources=[],source_sha256={})
        snapshot=make_snapshot(aligned,report,client['holdings_as_of'])
        req={'client_id':client['client_id'],'decision_date':client['holdings_as_of'],'ticker':'SPY','side':'buy','amount_cad':4000}
        self.assertEqual(evaluate_from_snapshot(req,client,snapshot,assets,policy)['verdict'],'green')
        snapshot['quotes']['SPY']['price_available_at']='2099-01-01T00:00:00+00:00'
        with self.assertRaises(ValueError):evaluate_from_snapshot(req,client,snapshot,assets,policy)


if __name__=='__main__':
    unittest.main()

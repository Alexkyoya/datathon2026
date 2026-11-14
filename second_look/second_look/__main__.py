from pathlib import Path
import argparse
import json
import pandas as pd
from .cleaning import run_cleaning
from .application import ProjectStore, SecondLookApplication


def make_snapshot(aligned, report, day):
    at = pd.Timestamp(day,tz='UTC')
    frame = aligned[aligned.snapshot_at.eq(at)]
    if frame.empty:
        raise ValueError('Snapshot date outside cleaned data')
    quotes = {}
    fields = ['close_cad','adj_close_cad','price_source_date','price_available_at',
              'fx_source_date','fx_available_at','fx_to_cad','price_age_days','fx_age_days']
    for _, row in frame.iterrows():
        if not row.usable:
            continue
        quotes[row.ticker] = {f:(None if pd.isna(row[f]) else str(row[f]) if isinstance(row[f],pd.Timestamp) else float(row[f])) for f in fields}
        quotes[row.ticker]['usable'] = True
    return {'decision_date':day,'snapshot_at':at.isoformat(),'quotes':quotes,
            'source_sha256':report['source_sha256'],'sources':report['sources']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['clean','demo','serve'])
    parser.add_argument('--root',default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--port',type=int,default=8000)
    args = parser.parse_args()
    root = Path(args.root)
    if args.command == 'serve':
        from .web import serve
        serve(root,args.port)
        return
    aligned, report = run_cleaning(root)
    if args.command == 'clean':
        print(json.dumps({k:report[k] for k in ['asset_count','source_rows','aligned_rows','unusable_rows']},indent=2))
        return
    day = aligned.snapshot_at.max().date().isoformat()
    snapshot = make_snapshot(aligned,report,day)
    client = json.loads((root/'examples/client.json').read_text())
    if client['holdings_as_of'] != day:
        raise ValueError('Stored fictional holdings date does not match latest snapshot')
    store = ProjectStore(root)
    store.save(f'snapshots/{day}.json',snapshot)
    app = SecondLookApplication(store)
    results = {}
    for name,ticker,amount in [('green','SPY',4000),('yellow','GLD',14000),('red','TSLA',12000)]:
        req = {'client_id':client['client_id'],'decision_date':day,'ticker':ticker,'side':'buy','amount_cad':amount}
        record = app.evaluate(req)
        result = record['result']
        if result['verdict'] != name:
            raise AssertionError(f'{name} demo unexpectedly {result["verdict"]}')
        results[name] = record
    store.save('demo_results.json',results)
    print(json.dumps({name:{'verdict':d['result']['verdict'],'ticker':d['request']['ticker'],
          'amount_cad':d['request']['amount_cad'],'hhi_after':d['result']['hhi_after']} for name,d in results.items()},indent=2))


if __name__ == '__main__':
    main()

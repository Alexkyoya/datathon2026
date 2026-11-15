"""Smoke-test a running local server: python tests/verify_http.py [base URL]."""
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:8000'


def call(path, payload=None, expected=200):
    data=None if payload is None else json.dumps(payload).encode()
    request=Request(base+path,data=data,headers={'Content-Type':'application/json'})
    try:
        response=urlopen(request,timeout=10)
    except HTTPError as exc:
        response=exc
    with response:
        assert response.status==expected,(path,response.status)
        body=response.read()
        return json.loads(body) if response.headers['Content-Type']=='application/json' else body


def main():
    for path in ['/','/app.js','/styles.css']:assert call(path)
    context=call('/api/context?client_id=demo_balanced')
    client=context['client']
    assert client['profile']=='Balanced'
    assert len(context['quotes'])==31 and context['account']['total_cad']>0
    for ticker,quote in context['quotes'].items():
        assert quote['price_source_date'][:10]<client['holdings_as_of']
        assert abs(quote['close_native']*quote['fx_to_cad']-quote['close_cad'])<1e-8
    records=[]
    def trade(ticker,amount):
        return {'client_id':client['client_id'],'decision_date':client['holdings_as_of'],
                'ticker':ticker,'side':'buy','amount_cad':amount}
    green=call('/api/evaluate',trade('SPY',4000));records.append(green)
    assert green['stage']=='simulation_continued' and green['result']['verdict']=='green'
    yellow=call('/api/evaluate',trade('GLD',14000));records.append(yellow)
    assert yellow['stage']=='awaiting_confirmation'
    confirmed=call('/api/simulations/'+yellow['simulation_id']+'/decision',{'decision':'confirm'});records.append(confirmed)
    assert confirmed['stage']=='simulation_continued' and confirmed['result']==yellow['result']
    red=call('/api/evaluate',trade('TSLA',12000));records.append(red)
    path='/api/simulations/'+red['simulation_id']+'/decision'
    assert call(path,{'decision':'confirm'},400)['verdict'] is None
    explained=call(path,{'decision':'explain','reason':'Fictional demo: I expect growth.'});records.append(explained)
    assert explained['stage']=='awaiting_agents' and explained['agent_status']=='not_configured'
    assert explained['result']==red['result'] and explained['result']['verdict']=='red'
    assert call(path,{'decision':'proceed'},400)['verdict'] is None
    continued=call(path,{'decision':'proceed','risk_acknowledged':True});records.append(continued)
    assert continued['stage']=='simulation_continued' and continued['risk_acknowledged'] is True
    assert continued['result']==red['result']
    assert call('/api/evaluate',trade('SPY',15001),400)['verdict'] is None
    assert call('/api/evaluate',dict(trade('SPY',10),profile='Maximum growth'),400)['verdict'] is None
    # Full schema validation is available when the checker environment has jsonschema.
    try:
        from jsonschema import Draft202012Validator,FormatChecker
    except ImportError:
        print('JSON Schema validation skipped (optional jsonschema checker unavailable)')
    else:
        schema=json.loads((Path(__file__).resolve().parents[1]/'contracts/simulation.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        validator=Draft202012Validator(schema,format_checker=FormatChecker())
        for record in records:validator.validate(record)
        print(f'All {len(records)} HTTP response records satisfy JSON Schema v2.0')
    print('PASS: static assets, stored context, green, yellow confirmation, red reason/handoff, immutable verdict, invalid funding/profile rejection')


if __name__=='__main__':main()

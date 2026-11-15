"""Validate this content kit locally. No LLM or AWS access is attempted."""
from pathlib import Path
import hashlib
import json
import math

ROOT=Path(__file__).resolve().parent

def read(name):
    return json.loads((ROOT/name).read_text())

def main():
    checks=[]
    for name in ['behavioral_knowledge.json','agent_settings.json','conversation_examples.json',
                 'evaluation_cases.json','numeric_fixtures.json','source_provenance.json']:
        read(name)
        checks.append('JSON parsed: '+name)
    knowledge=read('behavioral_knowledge.json')
    cards=knowledge['cards']; ids={c['id'] for c in cards}
    assert len(ids)==len(cards)
    for c in cards:
        if c['source']:
            assert c['source']['id']=='LOC2010'
            assert c['source']['pdf_pages']==[n+3 for n in c['source']['printed_pages']]
            assert all(1<=n<=16 for n in c['source']['printed_pages'])
            assert c['report_paraphrase']
        else:
            assert c['id']=='E01' and c['report_paraphrase'] is None
        assert c['conversation_design']['plausible_alternative_explanations']
        assert c['conversation_design']['limits_on_inference']
    checks.append('Unique cards, valid page mapping, alternative explanations and provenance')
    source=read('source_provenance.json')
    assert hashlib.sha256((ROOT/source['source_file']).read_bytes()).hexdigest()==source['sha256']
    checks.append('Source PDF hash matches unmodified supplied document')
    fixtures={f['tool_call_id']:f for f in read('numeric_fixtures.json')['fixtures']}
    for f in fixtures.values():
        assert f['fixture_only'] is True
        r=f['result']; original=f['original_holdings_cad']; plan=f['future_plan']
        effective=f['effective_holdings_before_trade_cad']
        assert math.isclose(sum(original.values())+plan['contribution_cad'],sum(effective.values()))
        assert math.isclose(effective['CASH_CAD'],original['CASH_CAD']+plan['contribution_cad']-sum(plan['planned_purchases_cad'].values()))
        for t,amount in plan['planned_purchases_cad'].items():
            assert math.isclose(effective[t],original.get(t,0)+amount)
        total=r['portfolio_value_cad']; after=r['holdings_cad_after']
        assert math.isclose(sum(after.values()),total)
        assert math.isclose(sum(r['position_weights_after'].values()),1)
        for t,v in after.items():
            assert math.isclose(v/total,r['position_weights_after'][t],abs_tol=1e-12)
        assert math.isclose(sum(r['weights_after'].values()),1)
        assert math.isclose(r['hhi_after'],sum(w*w for w in r['position_weights_after'].values()))
        for c in r['checks']:
            gap=max(0,c['after']-c['limit']) if c['bound']=='max' else max(0,c['limit']-c['after'])
            assert math.isclose(c['gap_after'],gap,abs_tol=1e-10)
        assert f['portfolio_within_limits']==(not any(c['gap_after']>0 for c in r['checks']))
    assert fixtures['T_CURRENT']['result']['verdict']=='red'
    assert not fixtures['T_DEPOSIT_CASH']['portfolio_within_limits']
    assert fixtures['T_DEPOSIT_ALLOCATED']['portfolio_within_limits']
    expected={'T_CURRENT':(.15,.1,.3),'T_DEPOSIT_CASH':(.1,.4,.2),'T_DEPOSIT_ALLOCATED':(.1,.1,.5)}
    for tid,(stock,cash,fixed) in expected.items():
        r=fixtures[tid]['result']
        assert math.isclose(r['position_weights_after']['TSLA'],stock)
        assert math.isclose(r['weights_after']['cash'],cash)
        assert math.isclose(r['weights_after']['fixed_income'],fixed)
    checks.append('Numeric fixture totals, weights, HHI, residual breaches and README/example figures reconcile')
    examples=read('conversation_examples.json')['examples']
    for e in examples:
        assert set(e['concept_ids'])<=ids
        assert e['messages'] and e['why_this_is_useful']
        for m in e['messages']:
            assert m['role'] in ('user','assistant','tool_result')
            if m['role']=='tool_result':assert m['content'] in fixtures
    cases=read('evaluation_cases.json')['cases']
    assert len({e['id'] for e in examples})==len(examples)
    assert len({e['id'] for e in cases})==len(cases)
    for e in cases:
        assert set(e['concept_ids'])<=ids
        assert e['expected_behaviors'] and e['hard_fail_if']
        for k,v in e['context'].items():
            if k in ('tool_fixture_id','current_assessment_fixture'):assert v in fixtures
    checks.append('Dialogue and evaluation concept/fixture references resolve')
    settings=read('agent_settings.json')
    for key in ('instructions_file','knowledge_file'):
        assert (ROOT/settings[key]).is_file()
    assert 'LLM' in (ROOT/'system_prompt.txt').read_text()
    checks.append('Runtime content paths exist')
    result={'content_validation':'passed','knowledge_cards':len(cards),
            'report_cards':sum(c['source'] is not None for c in cards),
            'runtime_enabled_cards':sum(c['runtime_enabled'] for c in cards),
            'dialogue_examples':len(examples),'evaluation_cases':len(cases),'numeric_fixtures':len(fixtures),
            'checks':checks,'llm_behavior_evaluation':'NOT RUN',
            'aws_deployment':'NOT PERFORMED','scope':'Content structure, references and fixture arithmetic only; not a clinical instrument, model benchmark or deployment verification.'}
    (ROOT/'VALIDATION.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()

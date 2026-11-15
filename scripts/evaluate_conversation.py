"""Optional live Bedrock hold-out review using fictional sessions and local tools.

Default: four small cases. --all exercises the kit's 24 cases. No infrastructure,
account updates or trades are performed. Human rubric review is still required.
"""
import argparse
import copy
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'second_look'))
sys.path.insert(1, str(ROOT/'agents'))
from second_look.application import FileStore, ProjectStore, SecondLookApplication
from second_look.conversation import ConversationService, ConversationTools, evidence, render_answer
from conversation import ConversationalCoordinator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Explicitly enable paid Bedrock inference')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--output', default='/tmp/second-look-model-evaluation.json')
    args = parser.parse_args()
    kit = ROOT/'second_look_conversation_kit'
    suite = json.loads((kit/'evaluation_cases.json').read_text())
    selected = suite['cases'] if args.all else [c for c in suite['cases'] if c['id'] in ('EVAL01', 'EVAL03', 'EVAL10', 'EVAL24')]
    import boto3
    credentials = boto3.Session().get_credentials()
    report = {'evaluation_type': 'Live configured Bedrock model; fictional sessions, cached synthetic reports, local tool boundaries',
              'rubric': suite['rubric'], 'cases_requested': [c['id'] for c in selected], 'results': [],
              'content_validation_is_not_model_evaluation': True,
              'aws_credentials_available': bool(credentials), 'live_model_verified': False}
    if not args.live or not credentials:
        report['status'] = 'not_run'
        report['reason'] = 'Pass --live to enable inference.' if not args.live else 'No AWS credentials available in this process.'
        report['cases_not_run'] = [c['id'] for c in suite['cases']]
    else:
        project = ProjectStore(ROOT/'second_look')
        fixtures = {f['tool_call_id']: f for f in json.loads((kit/'numeric_fixtures.json').read_text())['fixtures']}
        current = fixtures['T_CURRENT']
        snapshot = project.load('snapshots/2026-11-13.json')
        coordinator = ConversationalCoordinator()
        # A saved source snapshot supplies dated evidence. No Lambda call or live web search is implied.
        news = json.loads((ROOT/'data/news_snapshot.json').read_text())
        coordinator.news = lambda ticker, day: {'items': [i for i in news['items'] if
            i['ticker'] in (ticker, 'MACRO') and i['published_date'] <= day], 'decision_date': day}
        with tempfile.TemporaryDirectory(prefix='second-look-model-eval-') as directory:
            store = FileStore(directory)
            for name in ('assets', 'policy', 'reference_portfolios'):
                store.save(f'config/{name}.json', project.load(f'config/{name}.json'))
            store.save('snapshots/2026-11-13.json', snapshot)
            client = dict(current['client'], fictional=True, cash_cad=current['original_holdings_cad']['CASH_CAD'],
                holdings_as_of='2026-11-13', holdings={t: v/snapshot['quotes'][t]['close_cad'] for t, v in
                    current['original_holdings_cad'].items() if t != 'CASH_CAD'})
            store.save('clients/training_example_only.json', client)
            for case in selected:
                record = SecondLookApplication(store).evaluate(current['candidate_request'])
                inputs = store.load(f"assessment_inputs/{record['simulation_id']}.json")
                service = ConversationService(store, coordinator)
                state, _ = service._load(record, 'unused.json')
                state['initial_analysis'] = {'specialist_reports': {'note': 'Cached fictional development context; specialists not called during this evaluation.'}, 'explanation': {}}
                state['messages'] = [{'id': case['id'], 'role': 'user', 'text': case['user_message'], 'status': 'pending'}]
                state['evaluation_context'] = case['context']
                fixture_id = case['context'].get('tool_fixture_id')
                if fixture_id:
                    fixture = fixtures[fixture_id]
                    data = dict(fixture, data_status='hypothetical')
                    state['evidence']['fixture'] = evidence('fixture', data, record, 'hypothetical', 'Fictional computed kit fixture; not live account data')
                # Explicitly inject availability at the tool boundary for EVAL20.
                class EvaluationTools(ConversationTools):
                    def invoke(self, name, **kwargs):
                        if case['context'].get('tool_status') == 'unavailable' and name in (
                                'get_portfolio_exposure', 'recalculate_candidate', 'simulate_future_cash_plan'):
                            return {'data_status': 'unavailable', 'error': 'Calculation tool unavailable in this evaluation case.'}
                        return super().invoke(name, **kwargs)
                tools = EvaluationTools(store, record, inputs, state, coordinator, lambda: None, case['id'])
                outcome = {'id': case['id'], 'question': case['user_message'], 'expected_behaviors': case['expected_behaviors'],
                           'hard_fail_if': case['hard_fail_if'], 'human_rubric_scores': None}
                original = copy.deepcopy(record)
                started = time.time()
                try:
                    answer = coordinator.respond(record, inputs, state, tools)
                    report['live_model_verified'] = True
                    text, refs = render_answer(answer, state['evidence'])
                    outcome.update(response=text, evidence_refs=refs, tool_trace=state['tool_trace'],
                                   backend_unchanged=record == original, contract_validation='passed')
                except Exception as exc:
                    outcome.update(error=f'{type(exc).__name__}: {exc}', contract_validation='failed_or_unavailable')
                outcome['seconds'] = round(time.time() - started, 1)
                print(f"{case['id']}: {outcome['seconds']}s {'ok' if 'response' in outcome else 'ERROR'}", flush=True)
                report['results'].append(outcome)
        report['status'] = 'awaiting_human_review' if report['live_model_verified'] else 'unavailable'
    times = [r['seconds'] for r in report.get('results', []) if 'seconds' in r]
    if times:
        report['average_seconds'] = round(sum(times) / len(times), 1)
    Path(args.output).write_text(json.dumps(report, indent=2, default=str)+'\n')
    print(json.dumps({k: report[k] for k in ('status', 'live_model_verified', 'aws_credentials_available', 'cases_requested')}))
    if times:
        print('Average reply time:', report['average_seconds'], 's over', len(times), 'cases')
    print('Review artifact:', args.output)


if __name__ == '__main__':
    main()

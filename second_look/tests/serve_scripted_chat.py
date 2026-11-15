"""UI QA ONLY: explicitly scripted replies over the real persisted chat backend.

From second_look/: ../.venv/bin/python tests/serve_scripted_chat.py [port]
Never used by the normal server or AWS handler; no live AI claim is made.
"""
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from second_look.web import make_handler


class ScriptedCoordinator:
    max_tool_calls = 3

    def initial(self, record, inputs, reason):
        return {'specialist_reports': {'note': 'SCRIPTED UI TEST; specialists not called'}, 'explanation': {}, 'evidence': {}}

    def respond(self, record, inputs, state, tools):
        if len(state['messages']) == 1:
            return {'assistant_message': 'Scripted test reply: your stated strategy and the account limits are separate. What would you like to compare?', 'evidence_refs': ['current']}
        if len(state['messages']) == 3:
            result = tools.invoke('simulate_future_cash_plan', changes={'amount': 50000, 'currency': 'CAD',
                'expected_timing': 'next week', 'certainty': 'expected but not guaranteed', 'cash_use': 'cash'})
            return {'assistant_message': 'Scripted test reply: this contribution scenario remains conditional. The current assessment stays separate.', 'evidence_refs': [result['tool_call_id']]}
        return {'assistant_message': 'Scripted test reply: I can answer a follow-up without repeating the specialists.', 'evidence_refs': ['current']}


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8018
    print(f'SCRIPTED UI TEST ONLY: http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), make_handler(Path(__file__).resolve().parents[1], ScriptedCoordinator())).serve_forever()

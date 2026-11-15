"""Local frontend adapter over the same application service used by Lambda."""
import json
import uuid
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .application import ProjectStore, SecondLookApplication
from .market import build_context
from .conversation import ConversationService, LazyCoordinator
from .application import StateConflict


def make_handler(root, coordinator=None):
    root = Path(root)
    runtime = ProjectStore(root)
    app = SecondLookApplication(runtime)
    chat = ConversationService(runtime, coordinator or LazyCoordinator(root.parent))
    web = root/'web'

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, payload, content_type='application/json'):
            encoded = json.dumps(payload, allow_nan=False).encode() if content_type == 'application/json' else payload
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith('/api/simulations/') and path.endswith('/conversation'):
                try:
                    params = parse_qs(urlsplit(self.path).query)
                    return self.send(200, chat.get(path.split('/')[-2], params['client_id'][0], params['input_version'][0]))
                except (ValueError, KeyError, OSError) as exc:
                    return self.send(409 if isinstance(exc, StateConflict) else 400, {'error': str(exc)})
            if self.path == '/api/context' or self.path.startswith('/api/context?'):
                try:
                    cid = parse_qs(urlsplit(self.path).query).get('client_id', ['robert'])[0]
                    clients = [p.stem for p in (root/'examples/clients').glob('*.json')] + ['demo_balanced']
                    return self.send(200, build_context(runtime, cid, clients))
                except (OSError, ValueError, KeyError) as exc:
                    return self.send(503, {'error': f'Run python -m second_look demo first: {exc}'})
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
            path = urlsplit(self.path).path
            if path not in files:
                return self.send(404, {'error': 'Not found'})
            name, kind = files[path]
            self.send(200, (web/name).read_bytes(), kind)

        def do_POST(self):
            origin = self.headers.get('Origin')
            if origin and origin != 'http://'+self.headers.get('Host', ''):
                return self.send(403, {'error': 'Origin rejected'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('Invalid request length')
                payload = json.loads(self.rfile.read(length))
                if self.path == '/api/evaluate':
                    record = app.evaluate(payload)
                elif self.path.startswith('/api/simulations/') and self.path.endswith('/conversation'):
                    record = chat.send(self.path.split('/')[-2], payload, request_id=uuid.uuid4().hex)
                elif self.path.startswith('/api/simulations/') and self.path.endswith('/confirm-contribution'):
                    record = chat.confirm_contribution(self.path.split('/')[-2], payload)
                elif self.path.startswith('/api/simulations/') and self.path.endswith('/decision'):
                    if set(payload)-{'decision', 'reason', 'risk_acknowledged'} or 'decision' not in payload:
                        raise ValueError('Decision accepts decision, optional reason and risk_acknowledged')
                    sid = self.path.split('/')[-2]
                    record = app.decide(sid, payload['decision'], payload.get('reason'), payload.get('risk_acknowledged', False))
                else:
                    return self.send(404, {'error': 'Not found'})
                self.send(200, record)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.send(409 if isinstance(exc, StateConflict) else 400, {'status': 'invalid_input', 'verdict': None, 'error': str(exc)})

    return Handler


def serve(root, port=8000):
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(root))
    print(f'Second Look local simulation: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()

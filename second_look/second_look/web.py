"""Local frontend adapter over the same application service used by Lambda."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .application import ProjectStore, SecondLookApplication
from .spectrum import build_references


def make_handler(root):
    root = Path(root)
    runtime = ProjectStore(root)
    app = SecondLookApplication(runtime)
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
            if self.path == '/api/context':
                try:
                    client = runtime.load('clients/demo_balanced.json')
                    assets = runtime.load('config/assets.json')
                    policy = runtime.load('config/policy.json')
                    refs = runtime.load('config/reference_portfolios.json')
                    return self.send(200, {'client': client, 'tickers': sorted(assets),
                                          'references': build_references(refs, assets, policy),
                                          'reference_config': refs})
                except (OSError, ValueError, KeyError) as exc:
                    return self.send(503, {'error': f'Run python -m second_look demo first: {exc}'})
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/styles.css': ('styles.css', 'text/css; charset=utf-8')}
            if self.path not in files:
                return self.send(404, {'error': 'Not found'})
            name, kind = files[self.path]
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
                elif self.path.startswith('/api/simulations/') and self.path.endswith('/decision'):
                    if set(payload)-{'decision', 'reason'} or 'decision' not in payload:
                        raise ValueError('Decision accepts only decision and optional reason')
                    sid = self.path.split('/')[-2]
                    record = app.decide(sid, payload['decision'], payload.get('reason'))
                else:
                    return self.send(404, {'error': 'Not found'})
                self.send(200, record)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.send(400, {'status': 'invalid_input', 'verdict': None, 'error': str(exc)})

    return Handler


def serve(root, port=8000):
    server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(root))
    print(f'Second Look local simulation: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()

#!/usr/bin/env python3
import http.server, socketserver, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
PORT=int(os.environ.get('PORT','8000'))
class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control','no-cache')
        super().end_headers()
print(f'Open http://localhost:{PORT}/app/')
class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

try:
    with ReusableTCPServer(('127.0.0.1',PORT),Handler) as httpd:
        httpd.serve_forever()
except OSError as exc:
    if getattr(exc, 'errno', None) == 48:
        print(f'Port {PORT} is already in use. The app may already be running at http://localhost:{PORT}/app/.')
        print(f'To use another port: PORT={PORT + 1} python3 scripts/serve.py')
    else:
        raise

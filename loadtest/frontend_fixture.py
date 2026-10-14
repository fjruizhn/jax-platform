"""P100 synthetic API/WS boundary. Never an authentication or backend benchmark.

HTTP contracts: frontend/src/{store/useJaxStore.js,apariencia/sincronizarApariencia.js,
api/proyectos.js}. WS subset: frontend/src/api/websocket.js; framing RFC6455
https://www.rfc-editor.org/rfc/rfc6455.html (masked, unfragmented client frames).
All fixture data is explicit configuration; only loopback sockets are accepted.
"""
import argparse
import base64
from collections import deque
import hashlib
import hmac
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import struct
import threading
import time
from urllib.parse import urlsplit


class FixtureServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    request_queue_size = 1024

    def __init__(self, address, config):
        if address[0] != '127.0.0.1' or address[1] in (8080, 5173):
            raise ValueError('fixture requires private IPv4 loopback and a non-production port')
        self.config = config
        for key in ('email', 'password', 'user', 'project', 'appearance', 'state',
                    'allowed_origins', 'max_body_bytes', 'event_limit'):
            if key not in config:
                raise ValueError(f'missing fixture config {key}')
        if config['max_body_bytes'] <= 0 or config['event_limit'] <= 0:
            raise ValueError('fixture bounds must be positive')
        self._secret = secrets.token_bytes(32)
        self._revoked = set()
        self._observations = deque(maxlen=config['event_limit'])
        self._lock = threading.Lock()
        self._thread = None
        super().__init__(address, FixtureHandler)
        port = self.server_address[1]
        self.hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        self.base_url = f'http://127.0.0.1:{port}'

    def start(self):
        self._thread = threading.Thread(target=self.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self._thread.start()
        return self

    def close(self):
        if self._thread:
            self.shutdown()
            self._thread.join(timeout=3)
        self.server_close()

    def issue_token(self):
        nonce = secrets.token_hex(16)
        signature = hmac.new(self._secret, nonce.encode(), hashlib.sha256).hexdigest()
        return f'{nonce}.{signature}'

    def accepts(self, token):
        if not isinstance(token, str) or len(token) != 97:
            return False
        nonce, separator, signature = token.partition('.')
        expected = hmac.new(self._secret, nonce.encode(), hashlib.sha256).hexdigest()
        with self._lock:
            return separator == '.' and hmac.compare_digest(expected, signature) and token not in self._revoked

    def record(self, observation):
        with self._lock:
            self._observations.append(observation)

    def observations(self):
        """Unpersisted protocol observations, excluding auth/cookies/bodies."""
        with self._lock:
            return list(self._observations)

    def events(self):
        """Allowlisted metadata only. No query values, headers or payloads."""
        fields = ('method', 'type', 'status', 'request_bytes', 'response_bytes', 'duration_ms', 'auth_ok', 'host', 'origin')
        return [dict(path=urlsplit(item['target']).path,
                     **{key: item[key] for key in fields if key in item})
                for item in self.observations()]


class FixtureHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def do_PUT(self):
        self._handle()

    def _reply(self, status, data, headers=None):
        body = json.dumps(data, separators=(',', ':'), ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)
        self._status = status
        self._response_bytes = len(body) if self.command != 'HEAD' else 0

    def _cookie(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:  # fail-soft: cookie parse failure means no fixture session; refresh denies with 401.
            return None
        return cookie['p100_refresh'].value if 'p100_refresh' in cookie else None

    def _handle(self):
        started = time.monotonic()
        self._status, self._response_bytes, count = 500, 0, 0
        type_ = 'http'
        auth_ok = False
        try:
            if self.headers.get('Host') not in self.server.hosts:
                self.close_connection = True
                return self._reply(421, {'detail': 'fixture_host_invalid'})
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                return self._reply(400, {'detail': 'fixture_content_length_required'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                self.close_connection = True
                return self._reply(400, {'detail': 'fixture_invalid_length'})
            if length < 0 or length > self.server.config['max_body_bytes']:
                self.close_connection = True
                return self._reply(413, {'detail': 'fixture_body_limit'})
            raw = self.rfile.read(length)
            count = len(raw)
            if count != length:
                self.close_connection = True
                return self._reply(400, {'detail': 'fixture_incomplete_body'})
            path = urlsplit(self.path).path
            if self.headers.get('Upgrade', '').lower() == 'websocket':
                type_ = 'websocket'
                auth_ok = self._websocket(path)
                return
            settings = self.server.config
            if self.command == 'GET' and path == '/api/apariencia':
                return self._reply(200, settings['appearance'])
            if self.command == 'POST' and path == '/api/auth/login':
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    return self._reply(400, {'detail': 'fixture_invalid_json'})
                if not isinstance(data, dict) or data.get('email') != settings['email'] or data.get('password') != settings['password']:
                    return self._reply(401, {'detail': 'fixture_credentials_invalid'})
                token = self.server.issue_token()
                return self._reply(200, {'access_token': token, **settings['user']},
                                   {'Set-Cookie': f'p100_refresh={token}; Path=/api/auth; HttpOnly; SameSite=Lax'})
            if self.command == 'POST' and path == '/api/auth/refresh':
                cookie = self._cookie()
                if not self.server.accepts(cookie):
                    return self._reply(401, {'detail': 'fixture_no_session'})
                return self._reply(200, {'access_token': cookie})
            if self.command == 'POST' and path == '/api/auth/logout':
                cookie = self._cookie()
                if self.server.accepts(cookie):
                    with self.server._lock:
                        self.server._revoked.add(cookie)
                return self._reply(200, {'ok': True},
                                   {'Set-Cookie': 'p100_refresh=; Path=/api/auth; Max-Age=0; HttpOnly; SameSite=Lax'})
            endpoints = {'/api/auth/me': settings['user'], '/api/state': settings['state'],
                         '/api/proyectos': {'proyectos': [settings['project']], 'siguiente': None}}
            if self.command == 'GET' and path in endpoints:
                auth = self.headers.get('Authorization', '')
                if not auth.startswith('Bearer ') or not self.server.accepts(auth[7:]):
                    return self._reply(401, {'detail': 'fixture_no_session'})
                return self._reply(200, endpoints[path])
            return self._reply(404, {'detail': 'fixture_route_not_found'})
        except (OSError, EOFError):
            self.close_connection = True
        finally:
            self.server.record({'target': self.path, 'host': self.headers.get('Host'),
                                'origin': self.headers.get('Origin'), 'method': self.command,
                                'type': type_, 'status': self._status, 'request_bytes': count,
                                'response_bytes': self._response_bytes,
                                'duration_ms': round((time.monotonic() - started) * 1000, 3), 'auth_ok': auth_ok})

    def _send_frame(self, opcode, data):
        if len(data) < 126:
            header = bytes([128 | opcode, len(data)])
        elif len(data) <= 65535:
            header = bytes([128 | opcode, 126]) + struct.pack('!H', len(data))
        else:
            raise ValueError('fixture frame exceeds supported subset')
        self.wfile.write(header + data)
        self.wfile.flush()

    def _read_exact(self, length):
        data = self.rfile.read(length)
        if len(data) != length:
            raise EOFError('closed socket')
        return data

    def _read_frame(self):
        first, second = self._read_exact(2)
        length = second & 127
        if first & 112 or not first & 128 or not second & 128 or length == 127:
            raise ValueError('unsupported/malformed fixture WebSocket frame')
        if length == 126:
            length = struct.unpack('!H', self._read_exact(2))[0]
        if length > 4096 or (first & 15) >= 8 and length > 125:
            raise ValueError('fixture frame limit')
        mask = self._read_exact(4)
        data = self._read_exact(length)
        return first & 15, bytes(value ^ mask[i % 4] for i, value in enumerate(data))

    def _websocket(self, path):
        self.close_connection = True
        if self.command != 'GET' or path != f'/ws/{self.server.config["user"]["user_id"]}':
            self._reply(404, {'detail': 'fixture_ws_route'})
            return False
        if self.headers.get('Origin') not in self.server.config['allowed_origins']:
            self._reply(403, {'detail': 'fixture_ws_origin'})
            return False
        try:
            key = self.headers['Sec-WebSocket-Key']
            if len(base64.b64decode(key, validate=True)) != 16 or self.headers.get('Sec-WebSocket-Version') != '13':
                raise ValueError('invalid handshake')
            if 'upgrade' not in self.headers.get('Connection', '').lower().split(', '):
                raise ValueError('upgrade required')
        except (ValueError, TypeError):
            self._reply(400, {'detail': 'fixture_ws_handshake'})
            return False
        self.send_response(101)
        self.send_header('Upgrade', 'websocket')
        self.send_header('Connection', 'Upgrade')
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        self.send_header('Sec-WebSocket-Accept', accept)
        self.end_headers()
        self._status = 101
        authenticated = False
        try:
            opcode, payload = self._read_frame()
            data = json.loads(payload) if opcode == 1 else None
            if not isinstance(data, dict) or data.get('type') != 'auth' or not self.server.accepts(data.get('token')):
                self._send_frame(8, struct.pack('!H', 4001))
                return False
            self._send_frame(1, b'{"type":"auth_ok"}')
            authenticated = True
            # Record acceptance immediately so the browser can prove the actual boundary.
            self.server.record({'target': self.path, 'host': self.headers.get('Host'),
                                'origin': self.headers.get('Origin'), 'method': 'GET', 'type': 'websocket',
                                'status': 101, 'auth_ok': True, 'request_bytes': len(payload),
                                'response_bytes': 18, 'duration_ms': 0})
            while True:
                opcode, payload = self._read_frame()
                if opcode == 8:
                    self._send_frame(8, payload)
                    break
                if opcode == 9:
                    self._send_frame(10, payload)
                elif opcode != 10:
                    self._send_frame(8, struct.pack('!H', 1003))
                    break
        except (OSError, EOFError):  # fail-soft: disconnected WS is closed; auth_ok remains false unless already sent.
            pass
        except (ValueError, UnicodeDecodeError):
            self._send_frame(8, struct.pack('!H', 1002))
        finally:
            self.close_connection = True
        return authenticated


def write_private_json(path, data):
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--ready-file', required=True)
    parser.add_argument('--events-file', required=True)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    server = FixtureServer(('127.0.0.1', args.port), config).start()
    stop = threading.Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stop.set())
    write_private_json(args.ready_file, {'base_url': server.base_url, 'pid': os.getpid(), 'synthetic_boundary': True})
    try:
        stop.wait()
    finally:
        server.close()
        write_private_json(args.events_file, server.events())


if __name__ == '__main__':
    main()

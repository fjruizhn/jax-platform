"""Functional checks use only ephemeral loopback sockets and synthetic credentials."""
import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import time
import unittest

try:
    from loadtest.frontend_fixture import FixtureServer
except ImportError:
    FixtureServer = None


def config():
    return {
        'email': 'p100@example.invalid', 'password': 'synthetic-only-p100',
        'user': {'id': 270, 'user_id': 270, 'email': 'p100@example.invalid',
                 'name': 'P100 fixture', 'role': 'user', 'must_change_password': False},
        'project': {'id': 'p100-project', 'nombre': 'p100-proyecto-marcador',
                    'descripcion': 'Synthetic P100 boundary', 'papel': 'OWNER'},
        'appearance': {'system_name': 'P100 fixture', 'lang_default': 'es', 'theme_default': 'dark'},
        'state': {'facets': {}, 'active_pipelines': {}, 'las_manos_alive': True,
                  'kill_switch_active': False},
        'allowed_origins': ['http://axioma-ia.io:49001'], 'max_body_bytes': 2 * 1024 * 1024,
        'event_limit': 1000,
    }


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(FixtureServer, 'FixtureServer not implemented')
        self.server = FixtureServer(('127.0.0.1', 0), config()).start()
        self.port = self.server.server_address[1]
        self.addCleanup(self.server.close)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        data = json.dumps(body) if isinstance(body, dict) else body
        conn.request(method, path, data, headers or {})
        response = conn.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), json.loads(payload) if payload else None
        conn.close()
        return result

    def login(self):
        status, headers, body = self.request('POST', '/api/auth/login',
                                           {'email': config()['email'], 'password': config()['password']})
        self.assertEqual(status, 200)
        return body['access_token'], headers['Set-Cookie'].split(';')[0]

    def test_refresh_requires_fixture_cookie_and_login_uses_actual_ui_fields(self):
        self.assertEqual(self.request('POST', '/api/auth/refresh')[0], 401)
        self.assertEqual(self.request('POST', '/api/auth/login', {'email': config()['email'], 'password': 'bad'})[0], 401)
        token, cookie = self.login()
        status, _, refreshed = self.request('POST', '/api/auth/refresh', headers={'Cookie': cookie})
        self.assertEqual(status, 200)
        self.assertEqual(refreshed['access_token'], token)
        status, _, user = self.request('GET', '/api/auth/me', headers={'Authorization': 'Bearer ' + token})
        self.assertEqual(status, 200)
        self.assertEqual(user['user_id'], 270)
        self.assertEqual(user['must_change_password'], False)
        self.assertEqual(self.request('GET', '/api/auth/me', headers={'Authorization': 'Bearer invented'})[0], 401)

    def test_public_appearance_and_authenticated_project_state(self):
        self.assertEqual(self.request('GET', '/api/apariencia')[2], config()['appearance'])
        self.assertEqual(self.request('GET', '/api/state')[0], 401)
        token, _ = self.login()
        auth = {'Authorization': 'Bearer ' + token}
        self.assertEqual(self.request('GET', '/api/state', headers=auth)[2], config()['state'])
        status, _, result = self.request('GET', '/api/proyectos?vista=activos&limite=50', headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(result, {'proyectos': [config()['project']], 'siguiente': None})

    def test_unknown_api_keeps_body_size_query_host_origin_without_logging_secrets(self):
        payload = b'x' * (1024 * 1024)
        status, _, _ = self.request('POST', '/api/missing?probe=a%2Fb', payload,
                                  {'Host': f'localhost:{self.port}', 'Origin': config()['allowed_origins'][0],
                                   'Authorization': 'Bearer should-never-be-logged'})
        self.assertEqual(status, 404)
        observed = self.server.observations()[-1]
        self.assertEqual(observed['target'], '/api/missing?probe=a%2Fb')
        self.assertEqual(observed['host'], f'localhost:{self.port}')
        self.assertEqual(observed['origin'], config()['allowed_origins'][0])
        self.assertEqual(observed['request_bytes'], len(payload))
        serialized = json.dumps(self.server.events())
        self.assertNotIn('should-never-be-logged', serialized)
        self.assertNotIn('probe=a', serialized)
        self.assertNotIn(config()['password'], serialized)

    def test_rejects_non_loopback_bind_forbidden_ports_and_invalid_host(self):
        for address in [('0.0.0.0', 0), ('127.0.0.1', 8080), ('127.0.0.1', 5173)]:
            with self.assertRaises(ValueError):
                FixtureServer(address, config())
        self.assertEqual(self.request('GET', '/api/apariencia', headers={'Host': 'evil.invalid'})[0], 421)

    def websocket(self, token, origin=None, target='/ws/270?probe=a%2Fb'):
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=3)
        self.addCleanup(sock.close)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f'GET {target} HTTP/1.1\r\nHost: localhost:{self.port}\r\n'
                   f'Origin: {origin or config()["allowed_origins"][0]}\r\n'
                   'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                   f'Sec-WebSocket-Key: {key}\r\n\r\n')
        sock.sendall(request.encode())
        header = b''
        while not header.endswith(b'\r\n\r\n'):
            piece = sock.recv(1)
            if not piece:
                break
            header += piece
        if b'101 Switching' not in header:
            return header, None
        expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        self.assertIn(expected, header)
        payload = json.dumps({'type': 'auth', 'token': token}).encode()
        mask = b'1234'
        length = bytes([128 | len(payload)]) if len(payload) < 126 else b'\xfe' + struct.pack('!H', len(payload))
        sock.sendall(b'\x81' + length + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(payload)))
        first, second = sock.recv(2)
        count = second & 127
        if count == 126:
            count = struct.unpack('!H', sock.recv(2))[0]
        response = b''
        while len(response) < count:
            response += sock.recv(count - len(response))
        return first & 15, response

    def test_real_websocket_upgrade_auth_ok_and_auth_failure(self):
        token, _ = self.login()
        opcode, payload = self.websocket(token)
        self.assertEqual(opcode, 1)
        self.assertEqual(json.loads(payload), {'type': 'auth_ok'})
        # The server sends auth_ok before recording the observation; wait for
        # that condition rather than racing the handler thread after recv().
        deadline = time.monotonic() + 3
        observation = None
        while observation is None and time.monotonic() < deadline:
            records = [x for x in self.server.observations() if x['type'] == 'websocket']
            observation = records[-1] if records else None
            if observation is None:
                time.sleep(0.01)
        self.assertIsNotNone(observation, 'WebSocket acceptance was not recorded')
        self.assertEqual(observation['target'], '/ws/270?probe=a%2Fb')
        self.assertEqual(observation['host'], f'localhost:{self.port}')
        self.assertEqual(observation['origin'], config()['allowed_origins'][0])
        opcode, payload = self.websocket('invented')
        self.assertEqual(opcode, 8)
        self.assertEqual(struct.unpack('!H', payload[:2])[0], 4001)

    def test_websocket_rejects_foreign_origin_and_wrong_user(self):
        token, _ = self.login()
        header, _ = self.websocket(token, origin='http://external.invalid')
        self.assertIn(b'403', header)
        header, _ = self.websocket(token, target='/ws/999')
        self.assertIn(b'404', header)

    def test_body_bound_and_malformed_login_fail_without_exposing_input(self):
        # The server rejects from Content-Length before reading a body. Sending
        # 2 MiB first races its close and can raise BrokenPipe on CI runners.
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            conn.putrequest('POST', '/api/missing')
            conn.putheader('Content-Length', str(config()['max_body_bytes'] + 1))
            conn.endheaders()
            self.assertEqual(conn.getresponse().status, 413)
        finally:
            conn.close()
        self.assertEqual(self.request('POST', '/api/auth/login', 'not json')[0], 400)
        self.assertEqual(self.request('POST', '/api/auth/login', '[]')[0], 401)
        self.assertNotIn('not json', json.dumps(self.server.events()))

    def test_logout_invalidates_issued_access_and_refresh(self):
        token, cookie = self.login()
        self.assertEqual(self.request('POST', '/api/auth/logout', headers={'Cookie': cookie})[0], 200)
        self.assertEqual(self.request('POST', '/api/auth/refresh', headers={'Cookie': cookie})[0], 401)
        self.assertEqual(self.request('GET', '/api/state', headers={'Authorization': 'Bearer ' + token})[0], 401)


if __name__ == '__main__':
    unittest.main()

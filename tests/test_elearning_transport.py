"""Credentialed Canvas reads use actual TLS and revocable controlled sockets."""
import ctypes
import http.server
import json
import ssl
import subprocess
import threading

import pytest

from core.security.elearning_credentials import ElearningCredentialStore


@pytest.fixture
def api(tmp_path, monkeypatch):
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
                    '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    received = []
    entered, release = threading.Event(), threading.Event()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            received.append((self.path, dict(self.headers)))
            if self.path == '/login':
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<script>location.href="https://id.fudan.edu.cn/synthetic-login"</script>')
                return
            if self.path == '/landing':
                self.send_response(200)
                self.send_header('Set-Cookie', 'session=SYNTHETIC_COOKIE; Path=/; Secure; HttpOnly')
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(b'<p>Synthetic signed-in account</p>')
                return
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/unapproved')
                self.end_headers()
                return
            if self.path == '/error':
                self.send_response(403)
                body = ('reflected ' + self.headers.get('Authorization', '')).encode()
            else:
                self.send_response(200)
                body = (json.dumps([{'id': 7, 'filename': 'Synthetic.pdf', 'size': 13,
                                    'url': 'https://' + self.headers['Host'] + '/download'}]).encode()
                        if self.path.startswith('/api/v1/courses/42/files') else
                        b'SYNTHETIC_PDF' if self.path == '/download' else
                        b'[{"id":42,"name":"Synthetic course"}]' if self.path.startswith('/api/v1/courses')
                        else b'{"id":42,"login_id":"alice"}')
            self.send_header('Content-Type', 'application/pdf' if self.path == '/download' else 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if self.path == '/slow':
                self.wfile.flush()
                entered.set()
                release.wait(4)
            try:
                self.wfile.write(body)
            except OSError:
                pass
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    original = ssl.SSLContext.load_default_certs
    def load(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.load_verify_locations(cafile=str(cert))
    monkeypatch.setattr(ssl.SSLContext, 'load_default_certs', load)
    root = tmp_path / 'account'
    root.mkdir()
    url = f'https://127.0.0.1:{server.server_port}'
    (root / 'elearning.json').write_text(json.dumps({'base_url': url, 'username': 'alice',
                                                   'token': 'SYNTHETIC_TOKEN', 'password': 'SYNTHETIC_PASSWORD'}))
    store = ElearningCredentialStore(root, user_id='local-default')
    store.migrate_settings()
    try:
        yield store, url, received, entered, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def transport(store, **kwargs):
    from core.security.elearning_transport import ElearningTransport
    return ElearningTransport(store, check=kwargs.pop('check', lambda: None), **kwargs)


def test_actual_tls_token_request_never_decrypts_unneeded_password(api, monkeypatch):
    from core.security.keychain import MacOSKeychain
    store, _, received, *_ = api
    original = MacOSKeychain.get
    def selected(self, key):
        value = original(self, key)
        if value == 'SYNTHETIC_PASSWORD':
            raise AssertionError('API token requests must not decrypt password')
        return value
    monkeypatch.setattr(MacOSKeychain, 'get', selected)
    with transport(store).open('/api/v1/users/self/profile') as response:
        assert json.load(response)['id'] == 42
    assert received[0][1]['Authorization'] == 'Bearer SYNTHETIC_TOKEN'
    assert 'Cookie' not in received[0][1]


def test_original_session_cookie_is_resolved_only_for_bound_receiver(api):
    store, _, received, *_ = api
    store.save_field('token', '', expected_revision=store.inventory()['revision'])
    store.save_session({'cookies': [{'domain': '127.0.0.1', 'name': 'session', 'value': 'SYNTHETIC_COOKIE',
                                     'path': '/', 'secure': True, 'expires': -1}]},
                       expected_revision=store.inventory()['session_revision'])
    with transport(store).open('/api/v1/courses') as response:
        assert response.status == 200
    assert received[0][1]['Cookie'] == 'session=SYNTHETIC_COOKIE'
    assert 'Authorization' not in received[0][1]


def test_rotation_stops_old_client_and_fresh_client_uses_new_token(api):
    store, _, received, *_ = api
    old = transport(store)
    with old.open('/api/v1/courses') as response:
        response.read()
    store.save_field('token', 'SYNTHETIC_REPLACEMENT', expected_revision=store.inventory()['revision'])
    with pytest.raises(PermissionError):
        old.open('/api/v1/courses')
    with transport(store).open('/api/v1/courses') as response:
        response.read()
    assert [headers['Authorization'] for _, headers in received] == [
        'Bearer SYNTHETIC_TOKEN', 'Bearer SYNTHETIC_REPLACEMENT']


def test_locked_keychain_does_not_send_cached_authentication(api, isolated_keychain):
    store, _, received, *_ = api
    client = transport(store)
    with client.open('/api/v1/courses') as response:
        response.read()
    backend = isolated_keychain(store.path)
    backend.security.SecKeychainLock.argtypes = [ctypes.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    with pytest.raises(OSError):
        client.open('/api/v1/courses')
    assert len(received) == 1


def test_actual_connection_disable_interrupts_inflight_read_and_reopen_is_fresh(api):
    from core.security.connections import ConnectionControl, service_id
    store, url, _, entered, release = api
    identifier, _ = service_id(url)
    control = ConnectionControl(catalog=lambda: [{'id': identifier}])
    client = transport(store)
    response = client.open('/slow')
    errors = []
    def read():
        try:
            response.read()
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=read)
    worker.start()
    assert entered.wait(2)
    try:
        disabled = control.save(identifier, enabled=False, expected_revision=0)
        worker.join(2)
        assert not worker.is_alive() and errors
        control.save(identifier, enabled=True, expected_revision=disabled['revision'])
        with pytest.raises(PermissionError):
            response.read()
        release.set()
        with transport(store).open('/api/v1/courses') as fresh:
            assert fresh.status == 200
    finally:
        release.set()
        response.close()
        worker.join(2)


def test_other_api_receiver_and_redirect_are_rejected_before_forwarding_credentials(api):
    store, _, received, *_ = api
    client = transport(store)
    with pytest.raises(PermissionError):
        client.open('https://other.example.test/api/v1/courses')
    assert not received
    with pytest.raises(PermissionError):
        client.open('/redirect')
    assert [path for path, _ in received] == ['/redirect']


def test_authenticated_error_body_never_becomes_tool_error_text(api):
    store, _, _, *_ = api
    with pytest.raises(PermissionError) as error:
        transport(store).open('/error')
    assert 'SYNTHETIC_' not in str(error.value)
    assert 'reflected' not in str(error.value)


def test_unrelated_password_rotation_does_not_invalidate_token_client(api):
    store, _, received, *_ = api
    client = transport(store)
    store.save_field('password', 'SYNTHETIC_OTHER_PASSWORD', expected_revision=store.inventory()['revision'])
    with client.open('/api/v1/courses') as response:
        response.read()
    assert received[0][1]['Authorization'] == 'Bearer SYNTHETIC_TOKEN'


def test_real_resource_grant_revocation_interrupts_inflight_api_read(api):
    from common.runtime_identity import identity_scope
    from core.security.resource_grants import ResourceGrantStore
    from core.security.service import SecurityService
    store, _, _, entered, release = api
    resource = {'kind': 'course', 'id': store.inventory()['account_id'] + ':42'}
    grants = ResourceGrantStore()
    grant = grants.issue(user_id='local-default', subject='session:default:elearning-api',
                         resource=resource, operations=['read'])
    with identity_scope(session_id='elearning-api', agent_id='default'):
        client = transport(store, check=lambda: SecurityService.authorize_resource(resource, 'read'))
        response = client.open('/slow')
    errors = []
    def read():
        try:
            response.read()
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=read)
    worker.start()
    assert entered.wait(2)
    try:
        grants.revoke(grant['grant_id'], user_id='local-default', expected_revision=grant['revision'])
        worker.join(2)
        assert not worker.is_alive() and errors
        with pytest.raises(PermissionError):
            client.open('/api/v1/courses')
    finally:
        release.set()
        response.close()
        worker.join(2)

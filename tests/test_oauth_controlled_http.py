"""Synthetic OAuth auth uses real controlled sockets, never redirects or proxies."""
import http.server
import json
import threading
import time
from urllib.parse import parse_qs

import pytest

from agent.tools.mcp import mcp_oauth as oauth


@pytest.fixture
def auth_server(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, '_store_path', lambda: str(tmp_path / 'mcp_oauth.json'))
    requests = []
    started, finish = threading.Event(), threading.Event()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, value):
            content = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            if self.path == '/slow':
                self.wfile.flush()
                started.set()
                finish.wait(5)
            try:
                self.wfile.write(content)
            except OSError:
                pass
        def do_GET(self):
            requests.append(('GET', self.path, {}))
            if self.path == '/.well-known/oauth-protected-resource':
                self.reply({'authorization_servers': [url]})
            elif self.path.startswith('/.well-known/'):
                self.reply({'issuer': url, 'authorization_endpoint': url + '/authorize',
                            'token_endpoint': url + '/token', 'registration_endpoint': url + '/register'})
            else:
                self.reply({'access_token': 'SYNTHETIC_REDIRECT_TOKEN'})
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
            requests.append(('POST', self.path, parse_qs(body)))
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/another-receiver')
                self.end_headers()
            else:
                self.reply({'access_token': 'SYNTHETIC_NEW_TOKEN', 'expires_in': 3600})
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield url, requests, started, finish
    finally:
        finish.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def client(url, endpoint='/token'):
    oauth.save_server_record('synthetic', dict(resource_url=url + '/mcp',
        access_token='SYNTHETIC_OLD_TOKEN', refresh_token='SYNTHETIC_REFRESH',
        client_id='public-client', metadata={'token_endpoint': url + endpoint}))
    return oauth.OAuthHandler('synthetic', url + '/mcp', url + '/callback')


def test_auth_to_explicit_local_resource_ignores_proxy_environment(auth_server, monkeypatch):
    url, received, *_ = auth_server
    value = client(url)
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    monkeypatch.setenv('no_proxy', '')
    assert value.refresh()
    assert value.get_valid_access_token() == 'SYNTHETIC_NEW_TOKEN'
    assert received == [('POST', '/token', {'grant_type': ['refresh_token'],
                                          'refresh_token': ['SYNTHETIC_REFRESH'], 'client_id': ['public-client']})]


def test_declared_local_resource_metadata_discovery_still_works(auth_server):
    url, received, *_ = auth_server
    metadata = oauth.discover_metadata(url + '/mcp')
    assert metadata['token_endpoint'] == url + '/token'
    assert [row[:2] for row in received] == [('GET', '/.well-known/oauth-protected-resource'),
                                           ('GET', '/.well-known/oauth-authorization-server')]


def test_auth_redirect_never_reaches_another_receiver(auth_server):
    url, received, *_ = auth_server
    value = client(url, '/redirect')
    assert value.refresh() is False
    assert [row[:2] for row in received] == [('POST', '/redirect')]
    assert value.get_valid_access_token() == 'SYNTHETIC_OLD_TOKEN'


def test_disabled_resource_refuses_auth_and_metadata_before_network(auth_server):
    from core.security.connections import ConnectionStore, current_user, service_id
    url, received, *_ = auth_server
    value = client(url)
    identifier, _ = service_id(url)
    ConnectionStore().save(current_user(), identifier, False, 0)
    assert value.refresh() is False
    assert oauth.discover_metadata(url + '/mcp') is None
    assert received == []


def test_a_different_local_auth_endpoint_requires_an_explicit_local_binding(auth_server):
    url, received, *_ = auth_server
    value = client(url)
    value.resource_url = 'https://remote.example.invalid/mcp'
    value._persist()
    assert value.refresh() is False
    assert received == []


def test_public_client_with_an_empty_optional_secret_can_refresh(auth_server):
    url, received, *_ = auth_server
    value = client(url)
    value._persist(client_secret='')
    assert value.refresh()
    assert received[-1][2]['refresh_token'] == ['SYNTHETIC_REFRESH']
    assert 'client_secret' not in received[-1][2]


def test_disabling_resource_cancels_inflight_auth_and_reenable_does_not_revive_it(auth_server):
    from core.security.connections import ConnectionControl, service_id
    url, received, started, finish = auth_server
    value = client(url, '/slow')
    identifier, _ = service_id(url)
    control = ConnectionControl(catalog=lambda: [{'id': identifier}])
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(value.refresh()))
    worker.start()
    try:
        assert started.wait(2)
        disabled = control.save(identifier, enabled=False, expected_revision=0)
        worker.join(2)
        assert not worker.is_alive() and outcomes == [False]
        assert value.access_token == 'SYNTHETIC_OLD_TOKEN'
        control.save(identifier, enabled=True, expected_revision=disabled['revision'])
        finish.set()
        assert outcomes == [False]
        assert value.refresh()
        assert value.get_valid_access_token() == 'SYNTHETIC_NEW_TOKEN'
        assert [row[:2] for row in received] == [('POST', '/slow'), ('POST', '/slow')]
    finally:
        finish.set()
        worker.join(2)


def test_oauth_response_cannot_outlive_its_callers_budget(auth_server):
    from core.plugins.caller import invocation_budget
    url, _, _, finish = auth_server
    value = client(url, '/slow')
    started = time.monotonic()
    try:
        with invocation_budget(150):
            assert value.refresh() is False
        assert time.monotonic() - started < 1
        assert value.access_token == 'SYNTHETIC_OLD_TOKEN'
    finally:
        finish.set()

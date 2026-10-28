"""Original MCP OAuth settings recover credentials without reviving old callbacks."""
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from agent.tools.mcp import mcp_oauth as oauth
from tests.test_capability_enablement import _manager, _wait
from tests.fixtures.mcp_streamable_http_server import make_handler


@pytest.fixture
def owner_server(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLA_HEADLESS', '1')
    monkeypatch.setattr(oauth, '_store_path', lambda: str(tmp_path / 'mcp_oauth.json'))
    monkeypatch.setattr(oauth, '_PENDING', {})
    tokens = []
    token_started, token_finish = threading.Event(), threading.Event()
    token_finish.set()
    base = make_handler()
    class Handler(base):
        def do_GET(self):
            if self.path == '/.well-known/oauth-protected-resource':
                self._json(200, {'authorization_servers': [url]})
            else:
                self._json(200, {'issuer': url, 'authorization_endpoint': url + '/authorize',
                    'token_endpoint': url + '/token', 'registration_endpoint': url + '/register'})
        def do_POST(self):
            if self.path == '/register':
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self._json(200, {'client_id': 'synthetic-client'})
            elif self.path == '/token':
                values = parse_qs(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode())
                tokens.append(values)
                token_started.set()
                token_finish.wait(5)
                self._json(200, {'access_token': 'SYNTHETIC_OWNER_TOKEN', 'refresh_token': 'SYNTHETIC_REFRESH', 'expires_in': 3600})
            elif self.headers.get('Authorization') != 'Bearer SYNTHETIC_OWNER_TOKEN':
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self._json(401, {})
            else:
                super().do_POST()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = _manager(tmp_path, monkeypatch)
    from common.runtime_identity import current_identity
    from agent.tools.capability_settings import CapabilitySettings
    manager.identity = current_identity()
    manager.capability_settings = CapabilitySettings(manager.workspace_root, user_id=manager.identity.user_id)
    config = {'name': 'Documents', 'type': 'streamable-http', 'url': url + '/mcp'}
    monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [config])
    try:
        yield manager, url, tokens, (token_started, token_finish)
    finally:
        token_finish.set()
        manager.shutdown_mcp()
        server.shutdown()
        server.server_close()
        thread.join(2)


def callback(monkeypatch, state):
    from channel.desktop import desktop_channel as desktop
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'input', lambda **kwargs: SimpleNamespace(
        code='synthetic-code', state=state, error='', error_description=''))
    return desktop.McpOAuthCallbackHandler().GET()


def pending_state():
    assert oauth._PENDING
    return next(iter(oauth._PENDING))


def test_real_owner_authorization_finishes_after_initial_unauthenticated_client_closes(owner_server, monkeypatch):
    manager, url, tokens, *_ = owner_server
    manager.set_mcp_enabled('Documents', True)
    _wait(lambda: manager.list_mcp_status().get('Documents') == 'needs_auth')
    assert '授权成功' in callback(monkeypatch, pending_state())
    _wait(lambda: manager.list_mcp_status().get('Documents') == 'ready')
    assert len(tokens) == 1
    assert oauth.load_server_record('Documents')['access_token'] == 'SYNTHETIC_OWNER_TOKEN'
    assert manager.capability_settings.enabled('mcp', 'Documents')
    from tests.fixtures.egress import approve_synthetic_egress
    with approve_synthetic_egress(url):
        result = manager._mcp_registry.get('Documents').call_tool('echo', {'text': 'owner-authorized'})
    assert 'echo:owner-authorized' in result
    assert 'SYNTHETIC_OWNER_TOKEN' not in result and 'stella-keychain:' not in result


def test_disabled_provider_cannot_finish_a_real_stale_authorization(owner_server, monkeypatch):
    manager, _, tokens, *_ = owner_server
    manager.set_mcp_enabled('Documents', True)
    _wait(lambda: manager.list_mcp_status().get('Documents') == 'needs_auth')
    state = pending_state()
    manager.set_mcp_enabled('Documents', False)
    assert '会话已过期' in callback(monkeypatch, state)
    assert tokens == []
    assert not oauth.load_server_record('Documents').get('access_token')


def test_original_owner_control_shows_pending_url_and_remove_cancels_its_callback(owner_server, monkeypatch):
    manager, url, tokens, *_ = owner_server
    manager.capability_settings.set_enabled('tool', 'mcp_Documents_echo', False)
    manager.set_mcp_enabled('Documents', True)
    _wait(lambda: manager.list_mcp_status().get('Documents') == 'needs_auth')
    state = pending_state()
    status = manager.manage_mcp_authorization('Documents', 'status')
    assert parse_qs(urlsplit(status['authorization_url']).query)['state'] == [state]
    assert status['saved'] and status['can_authorize']
    result = manager.manage_mcp_authorization('Documents', 'remove')
    assert not result['saved'] and result['state'] == 'not-configured'
    assert manager._load_mcp_configs()[0]['url'] == url + '/mcp'
    assert manager.capability_settings.enabled('mcp', 'Documents')
    assert not manager.capability_settings.enabled('tool', 'mcp_Documents_echo')
    assert not oauth._PENDING
    assert '授权成功' not in callback(monkeypatch, state)
    assert tokens == []
    fresh = manager.manage_mcp_authorization('Documents', 'authorize')
    fresh_state = parse_qs(urlsplit(fresh['authorization_url']).query)['state'][0]
    assert '授权成功' in callback(monkeypatch, fresh_state)
    _wait(lambda: manager.list_mcp_status().get('Documents') == 'ready')
    assert len(tokens) == 1
    assert not manager.capability_settings.enabled('tool', 'mcp_Documents_echo')


@pytest.mark.parametrize('action', ['status', 'authorize', 'cancel', 'remove'])
def test_agent_cannot_use_owner_oauth_controls(owner_server, action):
    from common.runtime_identity import identity_scope
    manager, _, tokens, *_ = owner_server
    with identity_scope(session_id='ordinary-agent'), pytest.raises(PermissionError):
        manager.manage_mcp_authorization('Documents', action)
    assert tokens == []


def test_reauthorize_and_cancel_keep_the_original_credential_references(owner_server):
    from pathlib import Path
    manager, url, tokens, *_ = owner_server
    oauth.save_server_record('Documents', dict(resource_url=url + '/mcp', client_id='synthetic-client', client_secret=None,
        access_token='SYNTHETIC_OWNER_TOKEN', refresh_token='SYNTHETIC_REFRESH', expires_at=0.0,
        metadata={'authorization_endpoint': url + '/authorize', 'token_endpoint': url + '/token',
                  'registration_endpoint': url + '/register'}))
    before = Path(oauth._store_path()).read_bytes()
    result = manager.manage_mcp_authorization('Documents', 'authorize')
    assert result['authorization_url'] and result['saved']
    assert Path(oauth._store_path()).read_bytes() == before
    result = manager.manage_mcp_authorization('Documents', 'cancel')
    assert result['authorization_url'] is None and result['saved']
    assert Path(oauth._store_path()).read_bytes() == before
    assert tokens == []


def test_oauth_owner_http_control_requires_authentication_and_owner(owner_server, monkeypatch):
    from channel.desktop import desktop_channel as desktop
    from common.runtime_identity import identity_scope
    manager, _, _, *_ = owner_server
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager', lambda: manager)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps({'name': 'Documents', 'action': 'oauth-status'}).encode())
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    reply = json.loads(desktop.McpCatalogHandler().POST())
    assert reply['status'] == 'success' and reply['oauth']['state'] == 'not-configured'
    with identity_scope(session_id='ordinary-agent'):
        assert json.loads(desktop.McpCatalogHandler().POST())['status'] == 'error'
    def reject():
        raise PermissionError('Unauthenticated')
    monkeypatch.setattr(desktop, '_require_auth', reject)
    with pytest.raises(PermissionError):
        desktop.McpCatalogHandler().POST()


def test_removal_of_an_empty_account_rejects_an_already_created_handlers_late_publication(owner_server):
    _, url, _, *_ = owner_server
    stale = oauth.OAuthHandler('Documents', url + '/mcp', url + '/callback')
    oauth.clear_server_record('Documents')
    assert stale._absorb_token_response({'access_token': 'SYNTHETIC_LATE_TOKEN'}) is False
    assert not oauth.load_server_record('Documents')


def test_status_inspects_references_without_decrypting_credentials(owner_server, isolated_keychain, monkeypatch):
    from pathlib import Path
    manager, url, _, *_ = owner_server
    oauth.save_server_record('Documents', dict(resource_url=url + '/mcp', access_token='SYNTHETIC_OWNER_TOKEN'))
    backend = isolated_keychain(Path(oauth._store_path()))
    def forbidden(*args):
        pytest.fail('Owner status must not decrypt stored values')
    monkeypatch.setattr(backend, 'get', forbidden)
    monkeypatch.setattr('core.security.env_store.keychain_for', lambda path: backend)
    status = manager.manage_mcp_authorization('Documents', 'status')
    assert status['state'] == 'configured'
    assert 'SYNTHETIC_' not in json.dumps(status) and 'stella-keychain:' not in json.dumps(status)


def test_unconfigured_oauth_account_keeps_an_original_mcp_configuration_entry(owner_server, monkeypatch, tmp_path):
    from core.capabilities.catalog import CapabilityCatalog
    manager, url, _, *_ = owner_server
    oauth.save_server_record('Documents', dict(resource_url=url + '/mcp', access_token='SYNTHETIC_OWNER_TOKEN'))
    monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [])
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager', lambda: manager)
    detail = CapabilityCatalog(tmp_path).detail('mcp', 'Documents')
    assert detail['retained'] is True and 'configuration' in detail['tabs']
    assert detail['source'] == 'retained'
    status = manager.manage_mcp_authorization('Documents', 'remove')
    assert not status['saved'] and not status['can_authorize']


def test_oauth_callback_error_is_escaped_and_cancels_its_pending_state(owner_server, monkeypatch):
    from channel.desktop import desktop_channel as desktop
    manager, _, _, *_ = owner_server
    result = manager.manage_mcp_authorization('Documents', 'authorize')
    state = parse_qs(urlsplit(result['authorization_url']).query)['state'][0]
    payload = '<script>window.stellaOAuthInjected=true</script>'
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'input', lambda **kwargs: SimpleNamespace(
        code='', state=state, error='access_denied', error_description=payload))
    response = desktop.McpOAuthCallbackHandler().GET()
    assert payload not in response and '&lt;script&gt;' in response
    assert oauth.pop_pending(state) is None


def test_owner_status_tracks_token_exchange_until_it_finishes(owner_server, monkeypatch):
    manager, _, _, (started, finish) = owner_server
    result = manager.manage_mcp_authorization('Documents', 'authorize')
    state = parse_qs(urlsplit(result['authorization_url']).query)['state'][0]
    finish.clear()
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(callback(monkeypatch, state)))
    worker.start()
    try:
        assert started.wait(2)
        status = manager.manage_mcp_authorization('Documents', 'status')
        assert status['authorizing'] is True
        assert status['authorization_url'] is None
    finally:
        finish.set()
        worker.join(3)
    assert not worker.is_alive() and '授权成功' in outcomes[0]
    _wait(lambda: manager.list_mcp_status().get('Documents') == 'ready')
    assert manager.manage_mcp_authorization('Documents', 'status')['state'] == 'configured'


def test_completed_authorization_does_not_expire_a_later_refresh(owner_server, monkeypatch):
    _, url, _, _ = owner_server
    value = oauth.OAuthHandler('Documents', url + '/mcp', url + '/callback')
    assert value.ensure_registered()
    authorization = value.build_authorization_url()
    state = parse_qs(urlsplit(authorization).query)['state'][0]
    assert oauth.pop_pending(state) is value
    assert value.finish_authorization('synthetic-code')
    later = oauth.time.time() + 601
    monkeypatch.setattr(oauth.time, 'time', lambda: later)
    assert value.refresh()


@pytest.mark.parametrize('action', ['cancel', 'remove'])
def test_owner_can_stop_an_inflight_exchange_without_restoring_removed_credentials(owner_server, monkeypatch, action):
    from pathlib import Path
    manager, url, _, (started, finish) = owner_server
    oauth.save_server_record('Documents', dict(resource_url=url + '/mcp', client_id='synthetic-client', client_secret=None,
        access_token='SYNTHETIC_OWNER_TOKEN', refresh_token='SYNTHETIC_REFRESH', expires_at=0.0,
        metadata={'authorization_endpoint': url + '/authorize', 'token_endpoint': url + '/token'}))
    original = Path(oauth._store_path()).read_bytes()
    result = manager.manage_mcp_authorization('Documents', 'authorize')
    state = parse_qs(urlsplit(result['authorization_url']).query)['state'][0]
    finish.clear()
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(callback(monkeypatch, state)))
    worker.start()
    try:
        assert started.wait(2)
        result = manager.manage_mcp_authorization('Documents', action)
        worker.join(2)
        assert not worker.is_alive() and '授权成功' not in outcomes[0]
        assert result['authorization_url'] is None and not result['authorizing']
        if action == 'cancel':
            assert Path(oauth._store_path()).read_bytes() == original
            assert oauth.load_server_record('Documents')['access_token'] == 'SYNTHETIC_OWNER_TOKEN'
        else:
            assert not oauth.load_server_record('Documents')
    finally:
        finish.set()
        worker.join(2)

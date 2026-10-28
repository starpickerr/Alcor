"""Original eLearning operations through the explicit trusted Core connector."""
import json

import pytest

from common.runtime_identity import identity_scope
from core.security.credential_values import REFERENCE
from tests.test_managed_mcp_domains import server_config
from tests.test_elearning_transport import api as api  # noqa: F401


@pytest.fixture
def connector(tmp_path):
    from core.services.elearning_connector import ElearningConnector
    config = server_config('elearning', tmp_path / 'elearning')
    with identity_scope(user_id='test-user'):
        yield ElearningConnector(config, check=lambda: None)


def test_original_save_publishes_references_and_retains_courses_and_destination(connector):
    saved = connector.invoke('save_settings', {'payload': {'username': 'alice', 'password': 'SYNTHETIC_PASSWORD',
                                                          'courses': ['42'], 'enabled': True}})
    raw = json.loads((connector.config.data_root / 'elearning.json').read_text())
    assert raw['password'].startswith(REFERENCE)
    assert raw['courses'] == ['42']
    assert saved['credential_states']['password'] == 'configured'
    assert 'SYNTHETIC_PASSWORD' not in json.dumps(saved)
    saved = connector.invoke('save_settings', {'payload': {'courses': ['43']}})
    after = json.loads((connector.config.data_root / 'elearning.json').read_text())
    assert after['password'] == raw['password'] and saved['courses'] == ['43']


def test_actual_course_discovery_uses_bound_native_token(api):  # noqa: F811
    from core.services.elearning_connector import ElearningConnector
    store, _, received, *_ = api
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    connector = ElearningConnector(config, check=lambda: None)
    result = connector.invoke('list_courses', {})
    assert result == [{'id': '42', 'name': 'Synthetic course'}]
    assert all(headers['Authorization'] == 'Bearer SYNTHETIC_TOKEN' for _, headers in received)


def test_failed_original_save_keeps_current_document_and_credentials(connector, monkeypatch):
    from core.security.keychain import MacOSKeychain
    connector.invoke('save_settings', {'payload': {'username': 'alice', 'password': 'SYNTHETIC_PASSWORD', 'courses': ['42']}})
    path = connector.config.data_root / 'elearning.json'
    before = path.read_bytes()
    original = MacOSKeychain.get
    def failed(self, key):
        value = original(self, key)
        return None if value == 'SYNTHETIC_FAILED' else value
    monkeypatch.setattr(MacOSKeychain, 'get', failed)
    with pytest.raises(OSError):
        connector.invoke('save_settings', {'payload': {'password': 'SYNTHETIC_FAILED', 'courses': ['99']}})
    assert path.read_bytes() == before


def test_original_known_legacy_settings_are_copied_and_migrated_without_removing_source(connector, tmp_path):
    legacy = tmp_path / 'briefing'
    legacy.mkdir()
    path = legacy / 'elearning.json'
    path.write_text(json.dumps({'username': 'alice', 'password': 'SYNTHETIC_OLD_PASSWORD', 'courses': ['42']}))
    before = path.read_bytes()
    connector.config['args'] += ['--legacy-dir', str(legacy)]
    connector.config.legacy_roots = tuple(legacy / name for name in (
        'elearning.json', 'elearning-sessions', 'elearning-courses', 'elearning-file-sync'))
    view = connector.invoke('get_settings', {})
    assert view['courses'] == ['42'] and view['credential_states']['password'] == 'configured'
    assert json.loads((connector.config.data_root / 'elearning.json').read_text())['password'].startswith(REFERENCE)
    assert path.read_bytes() == before


def test_agent_cannot_use_original_settings_to_edit_credentials(connector):
    with identity_scope(session_id='agent', agent_id='default'), pytest.raises(PermissionError):
        connector.invoke('save_settings', {'payload': {'username': 'alice', 'password': 'SYNTHETIC_PASSWORD'}})


def test_sync_cannot_bypass_original_output_scope(api, tmp_path):  # noqa: F811
    from core.services.elearning_connector import ElearningConnector
    store, _, received, *_ = api
    output = tmp_path / 'courseware'
    output.mkdir()
    cfg = json.loads(store.path.read_text())
    store.publish_settings({**cfg, 'courses': ['42'], 'file_sync_root': str(output)},
                           expected_revision=store.inventory()['revision'])
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    with pytest.raises(PermissionError):
        ElearningConnector(config, check=lambda: None).invoke('sync_files', {})
    assert not received and not list(output.iterdir())


def test_token_business_does_not_require_unrelated_password(api):  # noqa: F811
    from core.security import credential_values, env_store
    from core.services.elearning_connector import ElearningConnector
    store, _, received, *_ = api
    cfg = json.loads(store.path.read_text())
    env_store.keychain_for(store.path).delete(credential_values.account(store._name(cfg, 'password'), cfg['password']))
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    assert ElearningConnector(config, check=lambda: None).invoke('list_courses', {}) == [
        {'id': '42', 'name': 'Synthetic course'}]
    assert len(received) == 1


def test_agent_cannot_use_unmigrated_plaintext_as_authentication(api):  # noqa: F811
    from core.services.elearning_connector import ElearningConnector
    from core.security.resource_grants import ResourceGrantStore
    store, _, received, *_ = api
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    ResourceGrantStore().issue(user_id='local-default', subject='session:default:legacy-reader',
        resource={'kind': 'connection', 'id': 'mcp:elearning'}, operations=['read'])
    connector = ElearningConnector(config, check=lambda: None)
    with identity_scope(session_id='legacy-reader', agent_id='default'):
        assert connector.invoke('list_courses', {}) == [{'id': '42', 'name': 'Synthetic course'}]
    store.path.write_text(json.dumps({**json.loads(store.path.read_text()), 'token': 'SYNTHETIC_UNMIGRATED'}))
    with identity_scope(session_id='legacy-reader', agent_id='default'), pytest.raises(OSError):
        connector.invoke('list_courses', {})
    assert len(received) == 1


def test_existing_session_business_uses_native_cookie(api):  # noqa: F811
    from core.services.elearning_connector import ElearningConnector
    store, _, received, *_ = api
    store.save_field('token', '', expected_revision=store.inventory()['revision'])
    store.save_session({'cookies': [{'domain': '127.0.0.1', 'name': 'session', 'value': 'SYNTHETIC_COOKIE',
                                     'path': '/', 'secure': True, 'expires': -1}]},
                       expected_revision=store.inventory()['session_revision'])
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    assert ElearningConnector(config, check=lambda: None).invoke('list_courses', {}) == [
        {'id': '42', 'name': 'Synthetic course'}]
    assert received[0][1]['Cookie'] == 'session=SYNTHETIC_COOKIE'
    assert 'Authorization' not in received[0][1]


def test_cached_cookie_client_stops_after_session_removal(api, monkeypatch):  # noqa: F811
    from core.services.elearning_connector import ElearningConnector
    from core.services.trusted_account_modules import load_business
    store, _, received, *_ = api
    store.save_field('token', '', expected_revision=store.inventory()['revision'])
    store.save_session({'cookies': [{'domain': '127.0.0.1', 'name': 'session', 'value': 'SYNTHETIC_COOKIE',
                                     'path': '/', 'secure': True, 'expires': -1}]},
                       expected_revision=store.inventory()['session_revision'])
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    service = load_business(config)['service']
    original = service.course_list
    def interrupted(client):
        rows = original(client)
        assert client.get('/api/v1/courses')[0][0]['id'] == 42
        store.clear_session(expected_revision=store.inventory()['session_revision'])
        with pytest.raises((OSError, PermissionError)):
            client.get('/api/v1/courses')
        return rows
    monkeypatch.setattr(service, 'course_list', interrupted)
    ElearningConnector(config, check=lambda: None).invoke('list_courses', {})
    assert len(received) == 2


@pytest.mark.parametrize('draft', [False, True, 'new', 'disabled', 'revoked'])
def test_original_sso_login_with_real_browser_and_native_session(api, monkeypatch, draft):  # noqa: F811
    """Actual Chromium fills the existing selectors; only the synthetic SSO page is routed."""
    from playwright.sync_api import Browser, BrowserContext
    from core.services.elearning_connector import ElearningConnector
    store, url, received, *_ = api
    store.save_field('token', '', expected_revision=store.inventory()['revision'])
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    original_context, original_page = Browser.new_context, BrowserContext.new_page
    browser_events = []
    sso_requests = []
    username = 'bob' if draft == 'new' else 'alice'
    def new_context(self, **kwargs):
        # The test browser alone trusts this ephemeral self-signed local server.
        # Core still verifies the fixture's CA and actual TLS receiver.
        return original_context(self, **{**kwargs, 'ignore_https_errors': True})
    def new_page(self):
        def sso(route):
            sso_requests.append(route.request.url)
            if draft == 'revoked':
                from core.security.connections import ConnectionControl, service_id
                identifier, _ = service_id(url)
                control = ConnectionControl(catalog=lambda: [{'id': identifier}])
                disabled = control.save(identifier, enabled=False, expected_revision=0)
                control.save(identifier, enabled=True, expected_revision=disabled['revision'])
            route.fulfill(content_type='text/html; charset=utf-8', body='''<input id="login-username">
<input id="login-password" type="password"><button onclick="
if(document.getElementById('login-username').value===''' + "'" + username + "'" + ''' &&
document.getElementById('login-password').value==='SYNTHETIC_PASSWORD')
location.href=''' + "'" + url + "/landing'" + '''">登录</button>''')
        self.route('https://id.fudan.edu.cn/synthetic-login', sso)
        page = original_page(self)
        page.on('domcontentloaded', lambda: browser_events.append((page.url, page.content())))
        page.on('pageerror', lambda error: browser_events.append(str(error)))
        page.on('requestfailed', lambda request: browser_events.append((request.url, request.failure)))
        return page
    monkeypatch.setattr(Browser, 'new_context', new_context)
    monkeypatch.setattr(BrowserContext, 'new_page', new_page)
    connector = ElearningConnector(config, check=lambda: None)
    method = 'test_connection' if draft else 'list_courses'
    arguments = {'payload': {'username': username, 'password': 'SYNTHETIC_PASSWORD'}} if draft else {}
    before = store.path.read_bytes()
    if draft in ('disabled', 'revoked'):
        from core.security.connections import ConnectionControl, service_id
        identifier, _ = service_id(url)
        control = ConnectionControl(catalog=lambda: [{'id': identifier}])
        if draft == 'disabled':
            control.save(identifier, enabled=False, expected_revision=0)
        with pytest.raises(Exception):
            connector.invoke(method, arguments)
        if draft == 'disabled':
            assert not received and not browser_events
        else:
            assert sso_requests and control.inventory()['connections'][0]['enabled']
            assert not any(path == '/landing' for path, _ in received)
        assert not store.session_path(json.loads(store.path.read_text())).exists()
        return
    try:
        result = connector.invoke(method, arguments)
    except Exception:
        raise AssertionError(browser_events) from None
    assert result == [{'id': '42', 'name': 'Synthetic course'}]
    assert store.path.read_bytes() == before
    session = store.session_path(json.loads(store.path.read_text()))
    if draft == 'new':
        assert not session.exists()
        assert not store.session_path({'base_url': url, 'username': 'bob'}).exists()
    else:
        assert json.loads(session.read_text())['credential'].startswith(REFERENCE)
        assert store.load_session()['cookies'][0]['value'] == 'SYNTHETIC_COOKIE'
    assert any(path == '/api/v1/users/self/profile' and row.get('Cookie') == 'session=SYNTHETIC_COOKIE'
               for path, row in received)

"""Static MCP authentication uses original config documents and exact bound entries."""
import ctypes as c
import json
import sys

import pytest

from common.runtime_identity import identity_scope
from core.security.credential_values import REFERENCE

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Real macOS Keychain integration')


def document(tmp_path, *, form='mapping'):
    rows = [{'name': 'Documents', 'type': 'streamable-http', 'url': 'https://mcp.example.invalid/v1',
             'headers': {'Authorization': 'Bearer SYNTHETIC_STATIC_MCP', 'Accept': 'application/json',
                         'X-API-Key': 'SYNTHETIC_SECOND_KEY'}},
            {'name': 'Local', 'type': 'stdio', 'command': '/fixed/python', 'args': ['server.py'],
             'env': {'API_TOKEN': 'SYNTHETIC_LOCAL_TOKEN', 'LANGUAGE': 'en'}}]
    if form == 'mapping':
        data = {'mcpServers': {row['name']: {key: value for key, value in row.items() if key != 'name'} for row in rows}}
    elif form == 'config':
        data = {'mcp_servers': rows, 'agent': False, 'unrelated': {'keep': True}}
    else:
        data = rows
    path = tmp_path / ('config.json' if form == 'config' else 'mcp.json')
    path.write_text(json.dumps(data))
    return path


def store(path):
    from core.security.mcp_credentials import McpCredentialStore
    return McpCredentialStore(path, root_key='mcp_servers' if path.name == 'config.json' else None)


@pytest.mark.parametrize('form', ['mapping', 'list', 'config'])
def test_owner_migration_preserves_original_format_and_other_servers(tmp_path, form):
    path = document(tmp_path, form=form)
    service = store(path)
    before = json.loads(path.read_text())
    view = service.inventory('Documents', migrate=True)
    assert {item['key']: item['state'] for item in view['fields']} == {'Authorization': 'configured', 'X-API-Key': 'configured'}
    assert 'SYNTHETIC_STATIC_MCP' not in path.read_text()
    assert 'SYNTHETIC_SECOND_KEY' not in path.read_text()
    assert 'SYNTHETIC_LOCAL_TOKEN' in path.read_text()  # Only the selected capability is migrated.
    assert 'SYNTHETIC' not in json.dumps(view) and REFERENCE not in json.dumps(view)
    configs = service.configs()
    assert configs[0]['headers']['Authorization'].startswith(REFERENCE)
    assert configs[0]['headers']['Accept'] == 'application/json'
    assert configs[1]['env'] == {'API_TOKEN': 'SYNTHETIC_LOCAL_TOKEN', 'LANGUAGE': 'en'}
    after = json.loads(path.read_text())
    assert type(after) is type(before)
    if form == 'config':
        assert after['unrelated'] == {'keep': True} and after['agent'] is False


def test_selected_repair_never_decrypts_other_credential_and_stale_saves_keep_the_draft(tmp_path, monkeypatch):
    from core.security.keychain import MacOSKeychain
    path = document(tmp_path)
    service = store(path)
    view = service.inventory('Documents', migrate=True)
    before = json.loads(path.read_text())
    def cannot_read_previous(self, name):
        value = original(self, name)
        if value in {'Bearer SYNTHETIC_STATIC_MCP', 'SYNTHETIC_SECOND_KEY'}:
            raise AssertionError('An unchanged saved secret was decrypted')
        return value
    original = MacOSKeychain.get
    monkeypatch.setattr(MacOSKeychain, 'get', cannot_read_previous)
    result = service.save('Documents', 'headers', 'Authorization', 'Bearer SYNTHETIC_REPAIRED', expected_revision=view['revision'])
    after = json.loads(path.read_text())
    assert after['mcpServers']['Documents']['headers']['X-API-Key'] == before['mcpServers']['Documents']['headers']['X-API-Key']
    assert after['mcpServers']['Documents']['headers']['Authorization'] != before['mcpServers']['Documents']['headers']['Authorization']
    assert result['revision'] != view['revision']
    unchanged = path.read_bytes()
    with pytest.raises(ValueError):
        service.save('Documents', 'headers', 'Authorization', 'Bearer SYNTHETIC_STALE', expected_revision=view['revision'])
    assert path.read_bytes() == unchanged


def test_failed_migration_and_publication_keep_valid_config_and_current_reference(tmp_path, monkeypatch):
    path = document(tmp_path)
    service = store(path)
    before = path.read_bytes()
    def unavailable(*args):
        raise PermissionError('Synthetic unavailable Keychain')
    with monkeypatch.context() as patch:
        patch.setattr('core.security.env_store.keychain_for', unavailable)
        view = service.inventory('Documents', migrate=True)
    assert path.read_bytes() == before
    assert all(item['state'] == 'repair_required' for item in view['fields'])
    view = service.inventory('Documents', migrate=True)
    before = path.read_bytes()
    monkeypatch.setattr('core.security.storage.private_atomic_write', unavailable)
    with pytest.raises(PermissionError):
        service.save('Documents', 'headers', 'Authorization', 'SYNTHETIC_UNPUBLISHED', expected_revision=view['revision'])
    assert path.read_bytes() == before


def test_runtime_authentication_reloads_native_entries_and_never_sends_unbound_or_missing_reference(tmp_path, isolated_keychain):
    from core.security.mcp_credentials import runtime_headers
    path = document(tmp_path)
    service = store(path)
    service.inventory('Documents', migrate=True)
    config = service.configs()[0]
    headers = runtime_headers(config, config['url'])
    assert headers == {'Authorization': 'Bearer SYNTHETIC_STATIC_MCP', 'Accept': 'application/json', 'X-API-Key': 'SYNTHETIC_SECOND_KEY'}
    with pytest.raises(PermissionError):
        runtime_headers(dict(config), config['url'])
    with pytest.raises(PermissionError):
        runtime_headers(config, 'https://other.example.invalid/collect')
    backend = isolated_keychain(path)
    backend.security.SecKeychainLock.argtypes = [c.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    assert all(item['state'] == 'unavailable' for item in service.inventory('Documents')['fields'])
    with pytest.raises((OSError, PermissionError)):
        runtime_headers(config, config['url'])


def test_changed_receiver_cannot_inherit_saved_authentication_and_old_config_is_inactive(tmp_path):
    from core.security.mcp_credentials import runtime_headers
    path = document(tmp_path)
    service = store(path)
    service.inventory('Documents', migrate=True)
    old = service.configs()[0]
    changed = json.loads(path.read_text())
    changed['mcpServers']['Documents']['url'] = 'https://other.example.invalid/v1'
    path.write_text(json.dumps(changed))
    with pytest.raises(PermissionError):
        runtime_headers(old, old['url'])
    current = service.configs()[0]
    with pytest.raises((OSError, PermissionError)):
        runtime_headers(current, current['url'])


def test_local_env_credentials_are_sealed_but_cannot_be_injected_into_an_ordinary_mcp(tmp_path):
    from agent.tools.mcp.mcp_client import McpClient
    path = document(tmp_path)
    service = store(path)
    service.inventory('Local', migrate=True)
    local = service.configs()[1]
    assert local['env']['API_TOKEN'].startswith(REFERENCE)
    with pytest.raises(PermissionError):
        McpClient(local)._build_stdio_env(local['env'])


def test_owner_can_delete_selected_field_while_other_native_entries_are_unavailable(tmp_path, isolated_keychain):
    path = document(tmp_path)
    service = store(path)
    view = service.inventory('Documents', migrate=True)
    backend = isolated_keychain(path)
    backend.security.SecKeychainLock.argtypes = [c.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    result = service.save('Documents', 'headers', 'Authorization', '', expected_revision=view['revision'])
    assert 'Authorization' not in service.configs()[0]['headers']
    assert [item['key'] for item in result['fields']] == ['X-API-Key']


def test_ambiguous_credentials_preserve_valid_json_and_agent_cannot_manage_metadata(tmp_path):
    path = document(tmp_path, form='list')
    rows = json.loads(path.read_text())
    rows.append(dict(rows[0]))
    path.write_text(json.dumps(rows))
    before = path.read_bytes()
    with pytest.raises(ValueError):
        store(path).inventory('Documents', migrate=True)
    assert path.read_bytes() == before
    path = document(tmp_path)
    with identity_scope(session_id='agent-run', agent_id='default'):
        with pytest.raises(PermissionError):
            store(path).inventory('Documents')


def test_reference_in_a_nonstandard_header_can_be_repaired_without_reassigning_the_old_item(tmp_path):
    from core.security.mcp_credentials import runtime_headers
    path = document(tmp_path)
    service = store(path)
    service.inventory('Documents', migrate=True)
    data = json.loads(path.read_text())
    headers = data['mcpServers']['Documents']['headers']
    headers['X-Custom-Authentication'] = headers.pop('Authorization')
    path.write_text(json.dumps(data))
    before = service.inventory('Documents')
    assert next(item for item in before['fields'] if item['key'] == 'X-Custom-Authentication')['state'] == 'repair_required'
    service.save('Documents', 'headers', 'X-Custom-Authentication', 'SYNTHETIC_REPAIRED_CUSTOM', expected_revision=before['revision'])
    config = service.configs()[0]
    assert runtime_headers(config, config['url'])['X-Custom-Authentication'] == 'SYNTHETIC_REPAIRED_CUSTOM'


@pytest.mark.parametrize('value', ['Bearer SYNTHETIC\r\nInjected: value', 'Bearer SYNTHETIC_\U0001f511'])
def test_invalid_http_header_secret_is_rejected_without_publication_or_value_in_error(tmp_path, value):
    path = document(tmp_path)
    service = store(path)
    before = path.read_bytes()
    view = service.inventory('Documents')
    with pytest.raises(ValueError) as error:
        service.save('Documents', 'headers', 'Authorization', value, expected_revision=view['revision'])
    assert 'SYNTHETIC' not in str(error.value)
    assert path.read_bytes() == before


@pytest.mark.parametrize('transport', ['streamable-http', 'sse'])
def test_real_static_authentication_works_and_locked_or_changed_credentials_cannot_use_cached_headers(tmp_path, isolated_keychain, transport):
    import threading
    from http.server import ThreadingHTTPServer
    from agent.tools.mcp.mcp_client import McpClient
    from tests.fixtures.mcp_streamable_http_server import make_handler
    from tests.fixtures.egress import approve_synthetic_egress
    accepted, received = ['Bearer SYNTHETIC_STATIC_MCP'], []
    reflect_error = [False]
    base = make_handler()
    class Handler(base):
        def _json(self, status, payload):
            if reflect_error[0] and (payload.get('result') or {}).get('content'):
                status, payload = 403, {'detail': self.headers.get('Authorization') + ' ' + self.headers.get('X-API-Key')}
            super()._json(status, payload)
        def do_GET(self):
            if transport == 'sse' and self.headers.get('Authorization') == accepted[0] and self.headers.get('X-API-Key') == 'SYNTHETIC_SECOND_KEY':
                body = b'data: /messages\n\n'
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(404, {})
        def do_POST(self):
            received.append((self.headers.get('Authorization'), self.headers.get('X-API-Key')))
            if received[-1] != (accepted[0], 'SYNTHETIC_SECOND_KEY'):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self._json(401, {})
            else:
                super().do_POST()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}/mcp'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = document(tmp_path)
    data = json.loads(path.read_text())
    data['mcpServers']['Documents']['url'] = url
    data['mcpServers']['Documents']['type'] = transport
    path.write_text(json.dumps(data))
    service = store(path)
    service.inventory('Documents', migrate=True)
    client = McpClient(service.configs()[0])
    replacement = None
    try:
        assert client.initialize() is True
        with approve_synthetic_egress(url):
            assert 'echo:authenticated' in client.call_tool('echo', {'text': 'authenticated'})
        reflect_error[0] = True
        with approve_synthetic_egress(url), pytest.raises(OSError) as rejected:
            client.call_tool('echo', {'text': 'remote authentication error'})
        assert 'SYNTHETIC' not in str(rejected.value)
        reflect_error[0] = False
        view = service.inventory('Documents')
        service.save('Documents', 'headers', 'Authorization', 'Bearer SYNTHETIC_ROTATED', expected_revision=view['revision'])
        count = len(received)
        with approve_synthetic_egress(url), pytest.raises((OSError, PermissionError, RuntimeError)):
            client.call_tool('echo', {'text': 'old configuration'})
        assert len(received) == count
        accepted[0] = 'Bearer SYNTHETIC_ROTATED'
        replacement = McpClient(service.configs()[0])
        assert replacement.initialize() is True
        with approve_synthetic_egress(url):
            assert 'echo:rotated' in replacement.call_tool('echo', {'text': 'rotated'})
        backend = isolated_keychain(path)
        backend.security.SecKeychainLock.argtypes = [c.c_void_p]
        assert backend.security.SecKeychainLock(backend.keychain) == 0
        count = len(received)
        with approve_synthetic_egress(url), pytest.raises((OSError, PermissionError, RuntimeError)):
            replacement.call_tool('echo', {'text': 'locked configuration'})
        assert len(received) == count
    finally:
        client.shutdown()
        if replacement:
            replacement.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_tool_manager_loads_original_document_with_trusted_reference_binding_and_preserves_invalid_input(tmp_path, monkeypatch):
    from tests.test_capability_enablement import _manager
    from core.security.mcp_credentials import runtime_headers
    from config import conf
    path = document(tmp_path)
    store(path).inventory('Documents', migrate=True)
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, '_mcp_json_path', lambda: str(path))
    config = manager._load_user_mcp_configs()[0]
    assert runtime_headers(config, config['url'])['Authorization'] == 'Bearer SYNTHETIC_STATIC_MCP'
    monkeypatch.setitem(conf(), 'mcp_servers', [{'name': 'Fallback', 'command': 'unused'}])
    path.write_text('{"mcpServers":false}')
    with pytest.raises(ValueError):
        manager._load_user_mcp_configs()
    assert path.read_text() == '{"mcpServers":false}'


def test_config_json_fallback_uses_the_same_persisted_references_instead_of_stale_runtime_values(tmp_path, monkeypatch):
    from tests.test_capability_enablement import _manager
    from core.security.mcp_credentials import runtime_headers
    import config as config_module
    path = document(tmp_path, form='config')
    store(path).inventory('Documents', migrate=True)
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, '_mcp_json_path', lambda: str(tmp_path / 'absent-mcp.json'))
    monkeypatch.setattr(config_module, 'get_data_root', lambda: str(tmp_path))
    monkeypatch.setitem(config_module.conf(), 'mcp_servers', [{'name': 'stale'}])
    config = manager._load_user_mcp_configs()[0]
    assert config['name'] == 'Documents'
    assert runtime_headers(config, config['url'])['Authorization'] == 'Bearer SYNTHETIC_STATIC_MCP'


def test_original_manager_credential_controls_migrate_without_starting_worker_and_respect_owner(tmp_path, monkeypatch):
    from tests.test_capability_enablement import _manager
    path = document(tmp_path)
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, '_mcp_json_path', lambda: str(path))
    view = manager.manage_mcp_credentials('Documents', 'status')
    assert 'SYNTHETIC_STATIC_MCP' not in path.read_text()
    assert manager._mcp_registry is None
    assert 'SYNTHETIC' not in json.dumps(view) and REFERENCE not in json.dumps(view)
    changed = manager.manage_mcp_credentials('Documents', 'save', area='headers', key='Authorization',
        value='Bearer SYNTHETIC_FROM_OWNER', expected_revision=view['revision'])
    assert changed['revision'] != view['revision']
    assert manager._mcp_registry is None
    with identity_scope(session_id='agent', agent_id='default'):
        with pytest.raises(PermissionError):
            manager.manage_mcp_credentials('Documents', 'status')


def test_original_mcp_api_exposes_only_owner_status_and_selected_credential_changes(tmp_path, monkeypatch):
    from channel.desktop import desktop_channel as channel
    from tests.test_capability_enablement import _manager
    from agent.tools import tool_manager
    path = document(tmp_path)
    manager = _manager(tmp_path, monkeypatch)
    monkeypatch.setattr(manager, '_mcp_json_path', lambda: str(path))
    monkeypatch.setattr(tool_manager, 'ToolManager', lambda: manager)
    monkeypatch.setattr(channel, '_require_auth', lambda: None)
    monkeypatch.setattr(channel.web, 'header', lambda *args: None)
    def request(**body):
        monkeypatch.setattr(channel.web, 'data', lambda: json.dumps({'name': 'Documents', **body}).encode())
        return json.loads(channel.McpCatalogHandler().POST())
    response = request(action='credential-status')
    assert response['status'] == 'success'
    assert 'SYNTHETIC' not in json.dumps(response) and REFERENCE not in json.dumps(response)
    revision = response['credentials']['revision']
    response = request(action='credential-save', area='headers', key='Authorization', value='Bearer SYNTHETIC_HTTP_API', expectedRevision=revision)
    assert response['status'] == 'success'
    assert 'SYNTHETIC_HTTP_API' not in path.read_text()
    assert manager._mcp_registry is None
    with identity_scope(session_id='agent', agent_id='default'):
        assert request(action='credential-status')['status'] == 'error'
    before = path.read_bytes()
    assert request(action='credential-save', area='headers', key='Authorization', value='SYNTHETIC_REJECTED', expectedRevision=revision, user='other')['status'] == 'error'
    assert path.read_bytes() == before


def test_static_credential_directory_reports_native_state_and_original_entry_without_decryption(tmp_path, isolated_keychain, monkeypatch):
    from core.security import credentials
    from core.security.keychain import MacOSKeychain
    path = document(tmp_path)
    service = store(path)
    service.inventory('Documents', migrate=True)
    config = service.configs()[0]
    def no_decryption(*args):
        raise AssertionError('Directory attempted to read a saved credential value')
    monkeypatch.setattr(MacOSKeychain, 'get', no_decryption)
    rows = list(credentials.mcp_configuration_credentials(config))
    assert len(rows) == 2 and all(row['configured'] is True for row in rows)
    assert all(row['configure_url'] == '/skills/mcp/Documents?section=configuration' for row in rows)
    assert 'SYNTHETIC' not in json.dumps(rows) and REFERENCE not in json.dumps(rows)
    backend = isolated_keychain(path)
    backend.security.SecKeychainLock.argtypes = [c.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    assert all(row['configured'] is None for row in credentials.mcp_configuration_credentials(config))


def test_explicit_nonstandard_secret_fields_are_migrated_without_guessing_other_configuration(tmp_path):
    from core.security.mcp_credentials import runtime_headers
    path = document(tmp_path)
    data = json.loads(path.read_text())
    remote, local = data['mcpServers']['Documents'], data['mcpServers']['Local']
    remote['secretHeaders'] = ['X-Custom-Authentication']
    remote['headers']['X-Custom-Authentication'] = 'SYNTHETIC_DECLARED_HEADER'
    local['secretEnv'] = ['CUSTOM_AUTH']
    local['env']['CUSTOM_AUTH'] = 'SYNTHETIC_DECLARED_ENV'
    path.write_text(json.dumps(data))
    service = store(path)
    first = service.inventory('Documents', migrate=True)
    assert any(item['key'] == 'X-Custom-Authentication' and item['state'] == 'configured' for item in first['fields'])
    second = service.inventory('Local', migrate=True)
    assert any(item['key'] == 'CUSTOM_AUTH' and item['state'] == 'configured' for item in second['fields'])
    assert 'SYNTHETIC_DECLARED' not in path.read_text()
    configs = service.configs()
    assert configs[1]['env']['LANGUAGE'] == 'en'
    assert runtime_headers(configs[0], configs[0]['url'])['X-Custom-Authentication'] == 'SYNTHETIC_DECLARED_HEADER'
    # The original declaration also keeps an empty field editable after deletion.
    deleted = service.save('Documents', 'headers', 'X-Custom-Authentication', '', expected_revision=second['revision'])
    assert next(item for item in deleted['fields'] if item['key'] == 'X-Custom-Authentication')['state'] == 'not_configured'
    service.save('Documents', 'headers', 'X-Custom-Authentication', 'SYNTHETIC_REENTERED', expected_revision=deleted['revision'])


def test_bad_mcp_configuration_does_not_hide_unrelated_model_credential_status(monkeypatch):
    from types import SimpleNamespace
    from core.security import credentials
    from agent.permission import center
    from agent.tools import tool_manager
    monkeypatch.setattr(center, 'secrets', lambda: [dict(name='OPENAI_API_KEY', source='model', configured=True)])
    monkeypatch.setattr(credentials, 'plugin_credentials', lambda: iter(()))
    def invalid():
        raise ValueError('SYNTHETIC_PRIVATE_CONFIG_DETAIL')
    monkeypatch.setattr(tool_manager, 'ToolManager', lambda: SimpleNamespace(_load_mcp_configs=invalid))
    rows = list(credentials.credential_metadata())
    assert any(item['owner'] == 'model' and item['configured'] is True for item in rows)
    assert any(item['owner'] == 'mcp' and item['configured'] is None for item in rows)
    assert 'SYNTHETIC' not in json.dumps(rows)


def test_changing_configuration_source_closes_the_previous_live_client_even_when_json_fields_match(tmp_path, monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from agent.tools.tool_manager import ToolManager
    from agent.tools.capability_settings import CapabilitySettings
    from common.runtime_identity import current_identity
    from tests.test_capability_enablement import _manager, _wait
    from tests.fixtures.mcp_streamable_http_server import make_handler
    import config as config_module
    base = make_handler()
    class Handler(base):
        def do_POST(self):
            if self.headers.get('Authorization') == 'Bearer SYNTHETIC_STATIC_MCP':
                super().do_POST()
            else:
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self._json(401, {})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = document(tmp_path, form='config')
    data = json.loads(path.read_text())
    data['mcp_servers'] = data['mcp_servers'][:1]
    data['mcp_servers'][0]['url'] = f'http://127.0.0.1:{server.server_port}/mcp'
    path.write_text(json.dumps(data))
    store(path).inventory('Documents', migrate=True)
    manager = _manager(tmp_path, monkeypatch)
    manager.identity = current_identity()
    manager.capability_settings = CapabilitySettings(manager.workspace_root, user_id=manager.identity.user_id)
    monkeypatch.setattr(manager, '_load_mcp_configs', ToolManager._load_mcp_configs.__get__(manager))
    override = tmp_path / 'workspace-mcp.json'
    monkeypatch.setattr(manager, '_mcp_json_path', lambda: str(override))
    monkeypatch.setattr(config_module, 'get_data_root', lambda: str(tmp_path))
    try:
        manager.set_mcp_enabled('Documents', True)
        _wait(lambda: manager.list_mcp_status().get('Documents') == 'ready')
        previous = manager._mcp_registry.get('Documents')
        override.write_text(json.dumps({'mcp_servers': json.loads(path.read_text())['mcp_servers']}))
        manager.refresh_mcp_if_changed()
        assert previous._closed is True
        # The copied reference belongs to the old configuration namespace.
        assert all(item['state'] == 'repair_required' for item in store(override).inventory('Documents')['fields'])
    finally:
        manager.shutdown_mcp()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_header_name_case_changes_update_the_same_field_without_publishing_duplicate_authentication(tmp_path):
    from core.security.mcp_credentials import runtime_headers
    path = document(tmp_path)
    service = store(path)
    view = service.inventory('Documents', migrate=True)
    service.save('Documents', 'headers', 'authorization', 'Bearer SYNTHETIC_CASE_REPAIR', expected_revision=view['revision'])
    headers = json.loads(path.read_text())['mcpServers']['Documents']['headers']
    assert [key for key in headers if key.lower() == 'authorization'] == ['Authorization']
    config = service.configs()[0]
    assert runtime_headers(config, config['url'])['Authorization'] == 'Bearer SYNTHETIC_CASE_REPAIR'


def test_cookie_authentication_is_a_declared_credential_rather_than_an_ordinary_header(tmp_path):
    from core.security.mcp_credentials import runtime_headers
    path = document(tmp_path)
    data = json.loads(path.read_text())
    data['mcpServers']['Documents']['headers']['Cookie'] = 'session=SYNTHETIC_SESSION_COOKIE'
    path.write_text(json.dumps(data))
    service = store(path)
    view = service.inventory('Documents', migrate=True)
    assert any(field['key'] == 'Cookie' and field['state'] == 'configured' for field in view['fields'])
    assert 'SYNTHETIC_SESSION_COOKIE' not in path.read_text()
    config = service.configs()[0]
    assert runtime_headers(config, config['url'])['Cookie'] == 'session=SYNTHETIC_SESSION_COOKIE'

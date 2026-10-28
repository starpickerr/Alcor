"""Credential management only projects owner metadata; it never exports values."""
import json

import pytest

from common.runtime_identity import identity_scope


def test_directory_deduplicates_references_and_projects_only_public_metadata():
    from core.security.credentials import CredentialDirectory
    rows = [dict(reference='store:shared', owner='mcp:example', account='student', kind='password',
                 configured=True, configure_url='/capabilities/mcp/example?section=configuration', value='PRIVATE'),
            dict(reference='store:shared', owner='plugin:example', account='student', kind='password',
                 configured=True, configure_url='/capabilities/plugins/example?section=configuration', token='PRIVATE')]
    directory = CredentialDirectory(metadata=lambda: rows)
    result = directory.inventory()
    assert len(result['credentials']) == 1
    credential = result['credentials'][0]
    assert credential['owners'] == ['mcp:example', 'plugin:example']
    assert credential['configured'] is True
    assert credential['account'] == 'student'
    assert 'PRIVATE' not in json.dumps(result)
    assert not {'value', 'token', 'password', 'reference'} & credential.keys()


def test_directory_is_owner_only_and_does_not_invoke_provider_on_denial():
    from core.security.credentials import CredentialDirectory
    def forbidden():
        raise AssertionError('Agent must not enumerate owner metadata')
    with identity_scope(session_id='agent'), pytest.raises(PermissionError):
        CredentialDirectory(metadata=forbidden).inventory()


def test_directory_rejects_external_configuration_links_and_malformed_status():
    from core.security.credentials import CredentialDirectory
    result = CredentialDirectory(metadata=lambda: [
        dict(reference='one', owner='mcp:example', account='', kind='token', configured='secret', configure_url='https://evil.invalid'),
        dict(reference='two', owner='mcp:example', account='', kind='token', configured=False, configure_url='//evil.invalid')]).inventory()
    assert all(row['configure_url'] is None and row['configured'] is None for row in result['credentials'][:1])
    assert result['credentials'][1]['configure_url'] is None


def test_disabled_owner_uses_declaration_without_starting_mcp(monkeypatch):
    from core.security.credentials import credential_metadata
    from types import SimpleNamespace
    monkeypatch.setattr('agent.permission.center.secrets', lambda: [])
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager', lambda: SimpleNamespace(_load_mcp_configs=lambda: [
        dict(name='elearning', enabled=False, type='stdio', env={'CANVAS_TOKEN': ''})]))
    monkeypatch.setattr('core.security.credentials.plugin_credentials', lambda: [])
    def forbidden(*args, **kwargs):
        raise AssertionError('Catalog must not start a disabled owner')
    monkeypatch.setattr('application.api.Application.port', forbidden)
    result = list(credential_metadata())
    assert any(row['owner'] == 'mcp:elearning' and row['configured'] is None for row in result)


def test_agent_cannot_use_legacy_secret_management_or_enumeration():
    from agent.permission.center import set_secret, secrets
    with identity_scope(session_id='agent'):
        with pytest.raises(PermissionError):
            secrets()
        with pytest.raises(PermissionError):
            set_secret('INVALID-NAME', 'must-not-be-saved')


def test_sensitive_resources_http_requires_auth_and_owner(monkeypatch):
    from channel.desktop import desktop_channel as desktop
    def unauthenticated():
        raise PermissionError('unauthenticated')
    monkeypatch.setattr(desktop, '_require_auth', unauthenticated)
    with pytest.raises(PermissionError):
        desktop.SecuritySensitiveResourcesHandler().GET()


def test_retired_credential_tool_cannot_read_or_mutate_even_when_called_directly(tmp_path, monkeypatch):
    from agent.tools.env_config.env_config import EnvConfig
    monkeypatch.setenv('HOME', str(tmp_path))
    folder = tmp_path / '.stella'
    folder.mkdir()
    saved = folder / '.env'
    saved.write_text('OWNER_PRIVATE_KEY=DO_NOT_ENUMERATE\n')
    tool = EnvConfig()
    for action in ('list', 'get', 'set', 'delete'):
        result = tool.execute({'action': action, 'key': 'OWNER_PRIVATE_KEY', 'value': 'tampered'})
        assert result.status == 'error'
        assert 'DO_NOT_ENUMERATE' not in str(result)
        assert 'OWNER_PRIVATE_KEY' not in str(result)
    assert saved.read_text() == 'OWNER_PRIVATE_KEY=DO_NOT_ENUMERATE\n'


def test_credential_tool_is_not_discovered_and_full_access_cannot_authorize_it():
    from agent.tools.tool_manager import ToolManager
    from agent.permission.gateway import SecurityGateway, DecisionKind
    manager = ToolManager()
    manager._load_tools_from_init()
    assert 'env_config' not in manager.tool_classes
    with identity_scope(session_id='agent'):
        decision = SecurityGateway.check('full-access', 'env_config', {'action': 'list'})
    assert decision.kind is DecisionKind.DENY


def test_owner_credential_save_does_not_follow_predictable_temporary_symlinks(tmp_path, monkeypatch):
    from core.security.env_store import EnvironmentFile
    from core.security.config_credentials import write_document as _write_config
    monkeypatch.setenv('HOME', str(tmp_path))
    store = EnvironmentFile()
    store._ensure_env_file()
    unrelated = tmp_path / 'unrelated.txt'
    unrelated.write_text('KEEP_OWNER_DATA')
    from pathlib import Path
    Path(store.env_path + '.tmp').symlink_to(unrelated)
    store._write_env_file({'TEST_KEY': 'SYNTHETIC_SECRET'})
    assert unrelated.read_text() == 'KEEP_OWNER_DATA'
    assert not Path(store.env_path).is_symlink()
    config = tmp_path / 'config.json'
    config.with_suffix('.security-tmp').symlink_to(unrelated)
    _write_config(config, {'open_ai_api_key': 'SYNTHETIC_SECRET'})
    assert unrelated.read_text() == 'KEEP_OWNER_DATA'
    assert not config.is_symlink()


def test_oauth_tokens_and_legacy_temporary_file_are_protected_from_agent_access(tmp_path, monkeypatch):
    from agent.tools.utils.credentials import is_credential_path
    monkeypatch.setenv('HOME', str(tmp_path))
    for name in ('mcp_oauth.json', 'mcp_oauth.json.tmp'):
        path = tmp_path / '.stella' / name
        assert is_credential_path(str(path)), f'OAuth credential path exposed: {name}'


def test_oauth_owner_directory_reports_status_without_exporting_tokens(tmp_path, monkeypatch):
    from pathlib import Path
    from agent.tools.mcp import mcp_oauth
    from core.security.credentials import credential_metadata
    from types import SimpleNamespace
    monkeypatch.setenv('HOME', str(tmp_path))
    path = Path(mcp_oauth._store_path())
    path.write_text(json.dumps({'example': {'access_token': 'NEVER_EXPORT', 'refresh_token': 'ALSO_PRIVATE'}}))
    monkeypatch.setattr('agent.permission.center.secrets', lambda: [])
    monkeypatch.setattr('core.security.credentials.plugin_credentials', lambda: [])
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager', lambda: SimpleNamespace(_load_mcp_configs=lambda: [dict(name='example', type='streamable-http', url='https://service.invalid')]))
    rows = list(credential_metadata())
    assert any(row['owner'] == 'mcp:example' and row['kind'] == 'oauth' and row['configured'] is True for row in rows)
    assert 'NEVER_EXPORT' not in json.dumps(rows) and 'ALSO_PRIVATE' not in json.dumps(rows)


def test_oauth_persistence_does_not_follow_old_predictable_temporary_path(tmp_path, monkeypatch):
    from pathlib import Path
    from agent.tools.mcp import mcp_oauth
    monkeypatch.setenv('HOME', str(tmp_path))
    path = Path(mcp_oauth._store_path())
    unrelated = tmp_path / 'owner.txt'
    unrelated.write_text('KEEP')
    Path(str(path) + '.tmp').symlink_to(unrelated)
    mcp_oauth._save_store({'example': {'access_token': 'SYNTHETIC_TOKEN'}})
    assert unrelated.read_text() == 'KEEP'
    assert mcp_oauth.load_server_record('example')['access_token'] == 'SYNTHETIC_TOKEN'


def test_agent_cannot_enumerate_oauth_owner_status():
    from agent.tools.mcp.mcp_oauth import credential_statuses
    with identity_scope(session_id='agent-oauth'), pytest.raises(PermissionError):
        credential_statuses()

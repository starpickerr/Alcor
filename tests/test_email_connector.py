"""The explicitly trusted mailbox connector reuses the pinned business code."""
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from common.runtime_identity import identity_scope
from core.security.credential_values import REFERENCE
from tests.test_imap_transport import imap_server as imap_server  # noqa: F401
from tests.test_managed_mcp_domains import server_config


@pytest.fixture
def connector(tmp_path):
    from core.services.email_connector import EmailConnector
    config = server_config('email', tmp_path / 'email')
    with identity_scope(user_id='test-user'):
        yield EmailConnector(config, check=lambda: None)


def call(connector, method, **kwargs):
    return connector.invoke(method, kwargs)


def test_original_account_save_encrypts_before_publication_and_blank_edit_keeps_reference(connector):
    saved = call(connector, 'save_account', payload={'host': 'imap.example.test', 'user': 'alice',
                                                  'password': 'SYNTHETIC_PASSWORD'})
    path = connector.config.data_root / 'email.db'
    with sqlite3.connect(path) as db:
        reference = db.execute('SELECT password FROM email_accounts').fetchone()[0]
    assert reference.startswith(REFERENCE)
    assert b'SYNTHETIC_PASSWORD' not in path.read_bytes()
    assert saved['credential_status'] == 'configured'
    saved = call(connector, 'save_account', payload={'id': saved['id'], 'folder': 'Archive'})
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT password FROM email_accounts').fetchone()[0] == reference
    assert saved['folder'] == 'Archive'


def test_original_business_calls_authenticate_without_returning_material(connector, imap_server):  # noqa: F811
    port, commands, *_ = imap_server
    saved = call(connector, 'save_account', payload={'host': '127.0.0.1', 'port': port, 'user': 'alice',
                                                  'password': 'SYNTHETIC_PASSWORD'})
    assert call(connector, 'test_connection', payload={'id': saved['id']})['connected']
    assert call(connector, 'list_folders', account_id=saved['id'])['selected'] == 'INBOX'
    result = call(connector, 'fetch_messages', account_id=saved['id'])
    assert result['items'][0]['title'] == 'Synthetic email'
    assert 'SYNTHETIC_PASSWORD' not in json.dumps(result) and REFERENCE not in json.dumps(result)
    assert any(b'LOGIN alice "SYNTHETIC_PASSWORD"' in command for command in commands)


def test_changed_destination_cannot_reuse_saved_password(connector, imap_server):  # noqa: F811
    port, commands, *_ = imap_server
    saved = call(connector, 'save_account', payload={'host': '127.0.0.1', 'port': port, 'user': 'alice',
                                                  'password': 'SYNTHETIC_PASSWORD'})
    with pytest.raises((PermissionError, ValueError, OSError)):
        call(connector, 'test_connection', payload={'id': saved['id'], 'host': 'other.example.test'})
    assert not commands
    with pytest.raises((PermissionError, ValueError, OSError)):
        call(connector, 'save_account', payload={'id': saved['id'], 'host': 'other.example.test'})
    assert call(connector, 'get_account', account_id=saved['id'])['host'] == '127.0.0.1'


def test_agent_cannot_edit_credentials_or_forge_resource_scope(connector):
    saved = call(connector, 'save_account', payload={'host': 'imap.example.test', 'user': 'alice',
                                                  'password': 'SYNTHETIC_PASSWORD'})
    from core.security.resource_grants import ResourceGrantStore
    ResourceGrantStore().issue(user_id='test-user', subject='session:default:agent-session',
                               resource={'kind': 'connection', 'id': 'mcp:email'}, operations=['read'])
    with identity_scope(session_id='agent-session', agent_id='default'):
        with pytest.raises(PermissionError):
            call(connector, 'save_account', payload={'id': saved['id'], 'password': 'SYNTHETIC_STOLEN'})
        public = call(connector, 'list_accounts')
        assert 'credential_status' not in json.dumps(public)
        assert 'credential_revision' not in json.dumps(public)
        with pytest.raises(PermissionError):
            connector.invoke('fetch_messages', {'account_id': saved['id']},
                             meta={'resourceScope': {'userId': 'test-user', 'resources': []}})


def test_unpinned_connector_code_cannot_execute(tmp_path):
    from core.services.email_connector import EmailConnector
    config = server_config('email', tmp_path / 'email')
    repo = tmp_path / 'source'
    source = Path(config['args'][0]).parent
    shutil.copytree(source, repo, ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '.pytest_cache'))
    sentinel = tmp_path / 'executed'
    with (repo / 'email_mcp/service.py').open('a') as stream:
        stream.write('\nfrom pathlib import Path\nPath(' + repr(str(sentinel)) + ').touch()\n')
    config['args'][0] = str(repo / 'mcp_server.py')
    with identity_scope(user_id='test-user'), pytest.raises(PermissionError):
        EmailConnector(config, check=lambda: None).invoke('list_accounts', {})
    assert not sentinel.exists()


def test_connector_executes_verified_snapshot_when_disk_changes_after_hash(tmp_path, monkeypatch):
    import core.services.email_connector as module
    config = server_config('email', tmp_path / 'email')
    repo = tmp_path / 'source'
    shutil.copytree(Path(config['args'][0]).parent, repo,
                    ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '.pytest_cache'))
    config['args'][0] = str(repo / 'mcp_server.py')
    sentinel = tmp_path / 'executed'
    real_digest = module.source_digest
    def changed_after_snapshot(root, **kwargs):
        result = real_digest(root, **kwargs)
        with (repo / 'email_mcp/service.py').open('a') as stream:
            stream.write('\nfrom pathlib import Path\nPath(' + repr(str(sentinel)) + ').touch()\n')
        (repo / 'email_mcp/schema.sql').write_text('INVALID SQL AFTER VERIFICATION')
        return result
    monkeypatch.setattr(module, 'source_digest', changed_after_snapshot)
    monkeypatch.setattr(module, '_MODULES', {})
    with identity_scope(user_id='test-user'):
        assert module.EmailConnector(config, check=lambda: None).invoke('list_accounts', {})['mailboxes'] == []
    assert not sentinel.exists()


def test_legacy_environment_account_is_sealed_once_and_deletion_does_not_revive_it(connector, monkeypatch):
    connector.config['args'][-1] = 'local-default'
    monkeypatch.setenv('IMAP_HOST', 'imap.example.test')
    monkeypatch.setenv('IMAP_USER', 'legacy')
    monkeypatch.setenv('IMAP_PASSWORD', 'SYNTHETIC_LEGACY')
    with identity_scope(user_id='local-default'):
        view = call(connector, 'list_accounts')['mailboxes'][0]
        assert view['id'] == 'environment' and view['credential_status'] == 'configured'
        path = connector.config.data_root / 'email.db'
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT password FROM email_accounts').fetchone()[0].startswith(REFERENCE)
        call(connector, 'save_account', payload={'id': 'environment', 'folder': 'Archive'})
        call(connector, 'delete_account', account_id='environment')
        assert call(connector, 'list_accounts')['mailboxes'] == []


def test_other_user_never_inherits_global_environment_account(connector, monkeypatch):
    monkeypatch.setenv('IMAP_HOST', 'imap.example.test')
    monkeypatch.setenv('IMAP_USER', 'legacy')
    monkeypatch.setenv('IMAP_PASSWORD', 'SYNTHETIC_LEGACY')
    assert call(connector, 'list_accounts')['mailboxes'] == []


def test_missing_environment_reference_cannot_be_restored_from_cached_process_value(connector, monkeypatch, isolated_keychain):
    from core.security.env_store import EnvironmentFile, _entries
    from core.security.credential_values import account
    connector.config['args'][-1] = 'local-default'
    monkeypatch.setenv('IMAP_HOST', 'imap.example.test')
    monkeypatch.setenv('IMAP_USER', 'legacy')
    monkeypatch.setenv('IMAP_PASSWORD', 'SYNTHETIC_CACHED')
    with identity_scope(user_id='local-default'):
        env = EnvironmentFile()
        env.update_secret('IMAP_PASSWORD', 'SYNTHETIC_CACHED')
        reference = _entries(env.env_path)['IMAP_PASSWORD']
        isolated_keychain(env.env_path).delete(account('IMAP_PASSWORD', reference))
        view = call(connector, 'list_accounts')['mailboxes'][0]
        assert view['credential_status'] == 'unavailable'
        with pytest.raises(OSError):
            call(connector, 'test_connection', payload={'id': 'environment'})
        repaired = call(connector, 'save_account', payload={'id': 'environment', 'password': 'SYNTHETIC_REPAIRED'})
        assert repaired['credential_status'] == 'configured'


def test_stale_original_form_cannot_replace_newer_account_settings(connector):
    saved = call(connector, 'save_account', payload={'host': 'imap.example.test', 'user': 'alice',
                                                  'password': 'SYNTHETIC_PASSWORD'})
    current = call(connector, 'save_account', payload={'id': saved['id'], 'folder': 'Archive',
                                                       'credential_revision': saved['credential_revision']})
    with pytest.raises(ValueError, match='changed'):
        call(connector, 'save_account', payload={'id': saved['id'], 'password': 'SYNTHETIC_STALE',
                                                'credential_revision': saved['credential_revision']})
    assert call(connector, 'get_account', account_id=saved['id']) == current


def test_directory_reads_disabled_mailbox_status_without_worker_or_decryption(connector, monkeypatch):
    from core.security.credentials import CredentialDirectory
    from core.security.keychain import MacOSKeychain
    from application.api import Application
    from agent.tools.tool_manager import ToolManager
    call(connector, 'save_account', payload={'host': 'imap.example.test', 'user': 'alice',
                                            'password': 'SYNTHETIC_PASSWORD'})
    connector.config['enabled'] = False
    monkeypatch.setattr(ToolManager, '_load_mcp_configs', lambda self: [connector.config])
    def forbidden(*args):
        raise AssertionError('Directory must not start a Worker or decrypt saved mailbox passwords')
    monkeypatch.setattr(Application, 'port', forbidden)
    monkeypatch.setattr(MacOSKeychain, 'get', forbidden)
    rows = [row for row in CredentialDirectory().inventory()['credentials'] if 'mcp:email' in row['owners']]
    assert len(rows) == 1 and rows[0]['account'] == 'alice' and rows[0]['configured'] is True


def test_one_legacy_migration_failure_does_not_hide_an_existing_usable_account(connector, monkeypatch):
    from tests.email_domain import EmailService
    from core.security.keychain import MacOSKeychain
    saved = call(connector, 'save_account', payload={'host': 'imap.example.test', 'user': 'alice',
                                                  'password': 'SYNTHETIC_PASSWORD'})
    EmailService(connector.config.data_root / 'email.db').save_account(
        {'action': 'add', 'host': 'imap.example.test', 'user': 'bob', 'password': 'SYNTHETIC_BOB'})
    original = MacOSKeychain.get
    def failed(self, key):
        value = original(self, key)
        return None if value == 'SYNTHETIC_BOB' else value
    monkeypatch.setattr(MacOSKeychain, 'get', failed)
    rows = call(connector, 'list_accounts')['mailboxes']
    assert rows[0]['id'] == saved['id'] and rows[0]['credential_status'] == 'configured'
    assert rows[1]['credential_status'] == 'unavailable'


def test_original_account_save_failure_preserves_record_and_can_retry(connector, monkeypatch):
    from core.security.keychain import MacOSKeychain
    saved = call(connector, 'save_account', payload={'host': 'imap.example.test', 'user': 'alice',
                                                  'password': 'SYNTHETIC_PASSWORD'})
    original = MacOSKeychain.get
    def failed(self, key):
        value = original(self, key)
        return None if value == 'SYNTHETIC_FAILED_SAVE' else value
    with monkeypatch.context() as scoped:
        scoped.setattr(MacOSKeychain, 'get', failed)
        with pytest.raises(OSError):
            call(connector, 'save_account', payload={'id': saved['id'], 'folder': 'Archive',
                                                    'password': 'SYNTHETIC_FAILED_SAVE'})
    assert call(connector, 'get_account', account_id=saved['id']) == saved
    retried = call(connector, 'save_account', payload={'id': saved['id'], 'password': 'SYNTHETIC_REPAIRED'})
    assert retried['credential_status'] == 'configured'

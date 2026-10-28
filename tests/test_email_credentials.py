"""Email credentials use the existing account rows and real isolated Keychain."""
import json
import sqlite3
import sys

import pytest

from common.runtime_identity import identity_scope
from core.security.credential_values import REFERENCE
from tests.email_domain import EmailService

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Native macOS Keychain')


@pytest.fixture
def accounts(tmp_path):
    service = EmailService(tmp_path / 'email.db')
    rows = [service.save_account({'action': 'add', 'host': 'imap.example.test',
                                 'user': user, 'password': 'SYNTHETIC_' + user})
            for user in ('alice', 'bob')]
    service.set_cursors(rows[0]['id'], {'last_uid': '42'})
    from core.security.email_credentials import EmailCredentialStore
    return service, EmailCredentialStore(service.db_path, user_id='local-default'), rows


def stored(service):
    with sqlite3.connect(service.db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM email_accounts ORDER BY position')]


def test_migration_preserves_original_accounts_cursors_and_restart(accounts):
    service, store, rows = accounts
    before = stored(service)
    view = store.migrate()
    after = stored(service)
    for original, migrated in zip(before, after):
        assert migrated['password'].startswith(REFERENCE)
        assert {k: v for k, v in migrated.items() if k != 'password'} == {
            k: v for k, v in original.items() if k != 'password'}
    assert service.get_cursors(rows[0]['id']) == {'last_uid': '42'}
    assert b'SYNTHETIC_' not in service.db_path.read_bytes()
    assert all(row['state'] == 'configured' for row in view)
    assert REFERENCE not in json.dumps(view) and 'SYNTHETIC_' not in json.dumps(view)
    from core.security.email_credentials import EmailCredentialStore
    restarted = EmailCredentialStore(service.db_path, user_id='local-default')
    assert restarted.resolve(rows[0]['id']) == 'SYNTHETIC_alice'
    assert restarted.migrate() == view


def test_failed_migration_retains_all_original_rows(accounts, monkeypatch):
    service, store, _ = accounts
    before = stored(service)
    from core.security.keychain import MacOSKeychain
    original = MacOSKeychain.get
    def read(self, key):
        value = original(self, key)
        return None if value == 'SYNTHETIC_bob' else value
    monkeypatch.setattr(MacOSKeychain, 'get', read)
    with pytest.raises(OSError, match='verification'):
        store.migrate()
    assert stored(service) == before


def test_single_account_repair_and_delete_do_not_decrypt_other_accounts(accounts, monkeypatch):
    service, store, rows = accounts
    view = store.migrate()
    before = stored(service)
    from core.security.keychain import MacOSKeychain
    def forbidden(*args):
        raise AssertionError('Unchanged credentials must not be decrypted')
    original = MacOSKeychain.get
    def only_new(self, key):
        value = original(self, key)
        if value in ('SYNTHETIC_alice', 'SYNTHETIC_bob'):
            forbidden()
        return value
    monkeypatch.setattr(MacOSKeychain, 'get', only_new)
    repaired = store.save_password(rows[0]['id'], 'SYNTHETIC_REPAIRED', expected_revision=view[0]['revision'])
    assert repaired['state'] == 'configured'
    assert stored(service)[1] == before[1]
    deleted = store.save_password(rows[0]['id'], '', expected_revision=repaired['revision'])
    assert deleted['state'] == 'not_configured'
    assert stored(service)[0]['password'] == ''
    assert stored(service)[1] == before[1]
    assert service.get_cursors(rows[0]['id']) == {'last_uid': '42'}


def test_sql_publication_failure_keeps_old_credential_usable(accounts):
    service, store, rows = accounts
    view = store.migrate()
    before = stored(service)
    with sqlite3.connect(service.db_path) as db:
        db.execute("CREATE TRIGGER reject_password BEFORE UPDATE OF password ON email_accounts "
                   "BEGIN SELECT RAISE(ABORT, 'synthetic transaction failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_password(rows[0]['id'], 'SYNTHETIC_NEW', expected_revision=view[0]['revision'])
    assert stored(service) == before
    assert store.resolve(rows[0]['id']) == 'SYNTHETIC_alice'


def test_failed_commit_rolls_back_rows_without_retiring_old_keychain_item(accounts, monkeypatch):
    service, store, rows = accounts
    view = store.migrate()
    before = stored(service)
    connect = sqlite3.connect
    class FailedCommit(sqlite3.Connection):
        def commit(self):
            raise sqlite3.OperationalError('synthetic commit failure')
    def failing_connect(*args, **kwargs):
        return connect(*args, **kwargs, factory=FailedCommit)
    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite3, 'connect', failing_connect)
        with pytest.raises(sqlite3.OperationalError, match='commit failure'):
            store.save_password(rows[0]['id'], 'SYNTHETIC_UNCOMMITTED', expected_revision=view[0]['revision'])
    assert stored(service) == before
    assert store.resolve(rows[0]['id']) == 'SYNTHETIC_alice'


def test_locked_keychain_status_and_runtime_never_return_cached_secret(accounts, isolated_keychain):
    import ctypes
    from core.security.keychain import KeychainUnavailable
    _, store, rows = accounts
    store.migrate()
    assert store.resolve(rows[0]['id']) == 'SYNTHETIC_alice'
    backend = isolated_keychain(store.path)
    backend.security.SecKeychainLock.argtypes = [ctypes.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    assert store.inventory()[0]['state'] == 'unavailable'
    with pytest.raises(KeychainUnavailable):
        store.resolve(rows[0]['id'])


def test_stale_save_cannot_overwrite_changed_endpoint_or_other_account_reference(accounts):
    service, store, rows = accounts
    view = store.migrate()
    with sqlite3.connect(service.db_path) as db:
        db.execute('UPDATE email_accounts SET host=? WHERE id=?', ('changed.example.test', rows[0]['id']))
    with pytest.raises(ValueError, match='changed'):
        store.save_password(rows[0]['id'], 'SYNTHETIC_STALE', expected_revision=view[0]['revision'])
    assert store.inventory()[0]['state'] == 'repair_required'
    with pytest.raises(OSError):
        store.resolve(rows[0]['id'])
    with sqlite3.connect(service.db_path) as db:
        db.execute('UPDATE email_accounts SET password=? WHERE id=?',
                   (stored(service)[1]['password'], rows[0]['id']))
    with pytest.raises(OSError):
        store.resolve(rows[0]['id'])


def test_malformed_reference_is_preserved_and_owner_can_repair_only_that_row(accounts):
    service, store, rows = accounts
    store.migrate()
    with sqlite3.connect(service.db_path) as db:
        db.execute('UPDATE email_accounts SET password=? WHERE id=?', (REFERENCE + 'broken', rows[0]['id']))
    before = stored(service)
    view = store.inventory()
    assert view[0]['state'] == 'repair_required'
    assert stored(service) == before
    store.save_password(rows[0]['id'], 'SYNTHETIC_REPAIRED', expected_revision=view[0]['revision'])
    assert store.resolve(rows[0]['id']) == 'SYNTHETIC_REPAIRED'
    assert stored(service)[1] == before[1]


def test_agent_cannot_manage_or_enumerate_email_credentials(accounts):
    _, store, rows = accounts
    with identity_scope(agent_id='synthetic-agent', session_id='synthetic-session'):
        with pytest.raises(PermissionError):
            store.inventory()
        with pytest.raises(PermissionError):
            store.migrate()
        with pytest.raises(PermissionError):
            store.save_password(rows[0]['id'], 'SYNTHETIC_STOLEN', expected_revision='anything')


def test_lost_item_is_repairable_and_metadata_read_does_not_decrypt(accounts, isolated_keychain, monkeypatch):
    from core.security.credential_values import account
    from core.security.keychain import MacOSKeychain
    service, store, rows = accounts
    store.migrate()
    row = stored(service)[0]
    isolated_keychain(store.path).delete(account(store._name(row), row['password']))
    def forbidden(*args):
        raise AssertionError('Owner status must not decrypt')
    with monkeypatch.context() as scoped:
        scoped.setattr(MacOSKeychain, 'get', forbidden)
        view = store.inventory()
        assert view[0]['state'] == 'repair_required'
        assert view[1]['state'] == 'configured'
    with pytest.raises(OSError):
        store.resolve(rows[0]['id'])
    store.save_password(rows[0]['id'], 'SYNTHETIC_REPAIRED', expected_revision=view[0]['revision'])
    assert store.resolve(rows[0]['id']) == 'SYNTHETIC_REPAIRED'

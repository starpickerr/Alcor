"""Original eLearning JSON and account session files use native verified refs."""
import ctypes
import json
import sys

import pytest

from common.runtime_identity import identity_scope
from core.security.credential_values import REFERENCE

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Native macOS Keychain')


@pytest.fixture
def credentials(tmp_path):
    from core.security.elearning_credentials import ElearningCredentialStore
    root = tmp_path / 'elearning'
    root.mkdir()
    config = {'base_url': 'https://elearning.example.test', 'username': 'alice', 'enabled': True,
              'password': 'SYNTHETIC_PASSWORD', 'token': 'SYNTHETIC_TOKEN', 'courses': ['42'],
              'file_sync_root': '/keep/courseware', 'custom': {'keep': True}}
    (root / 'elearning.json').write_text(json.dumps(config))
    store = ElearningCredentialStore(root, user_id='alice-owner')
    session = store.session_path(config)
    session.parent.mkdir()
    session.write_text(json.dumps({'cookies': [{'name': 'session', 'value': 'SYNTHETIC_COOKIE',
                                               'domain': 'elearning.example.test', 'path': '/'}]}))
    (root / 'elearning-cursors.json').write_text('{"keep":"cursor"}')
    return store, config


def test_settings_and_session_migrate_in_their_original_paths_and_reload(credentials):
    store, original = credentials
    store.migrate_settings()
    store.migrate_session()
    saved = json.loads(store.path.read_text())
    assert saved['password'].startswith(REFERENCE) and saved['token'].startswith(REFERENCE)
    assert {k: v for k, v in saved.items() if k not in ('password', 'token')} == {
        k: v for k, v in original.items() if k not in ('password', 'token')}
    session_path = store.session_path(saved)
    assert json.loads(session_path.read_text())['credential'].startswith(REFERENCE)
    assert 'SYNTHETIC_' not in store.path.read_text() + session_path.read_text()
    assert (store.root / 'elearning-cursors.json').read_text() == '{"keep":"cursor"}'
    assert all(row['state'] == 'configured' for row in store.inventory()['fields'])
    from core.security.elearning_credentials import ElearningCredentialStore
    restarted = ElearningCredentialStore(store.root, user_id='alice-owner')
    assert restarted.resolve('token') == 'SYNTHETIC_TOKEN'
    assert restarted.load_session()['cookies'][0]['value'] == 'SYNTHETIC_COOKIE'


def test_failed_settings_verification_retains_original_document(credentials, monkeypatch):
    store, _ = credentials
    before = store.path.read_bytes()
    from core.security.keychain import MacOSKeychain
    original = MacOSKeychain.get
    def missing(self, key):
        value = original(self, key)
        return None if value == 'SYNTHETIC_TOKEN' else value
    monkeypatch.setattr(MacOSKeychain, 'get', missing)
    with pytest.raises(OSError):
        store.migrate_settings()
    assert store.path.read_bytes() == before


def test_session_verification_failure_retains_original_session_file(credentials, monkeypatch):
    store, config = credentials
    path = store.session_path(config)
    before = path.read_bytes()
    from core.security.keychain import MacOSKeychain
    monkeypatch.setattr(MacOSKeychain, 'get', lambda *args: None)
    with pytest.raises(OSError):
        store.migrate_session()
    assert path.read_bytes() == before


def test_repair_one_field_and_use_token_without_decrypting_unrelated_password(credentials, monkeypatch):
    store, _ = credentials
    store.migrate_settings()
    before = json.loads(store.path.read_text())
    from core.security.keychain import MacOSKeychain
    original = MacOSKeychain.get
    def only_needed(self, key):
        value = original(self, key)
        if value == 'SYNTHETIC_PASSWORD':
            raise AssertionError('Unrelated password must not be decrypted')
        return value
    monkeypatch.setattr(MacOSKeychain, 'get', only_needed)
    assert store.resolve('token') == 'SYNTHETIC_TOKEN'
    view = store.inventory()
    store.save_field('token', 'SYNTHETIC_REPAIRED', expected_revision=view['revision'])
    assert json.loads(store.path.read_text())['password'] == before['password']
    assert store.resolve('token') == 'SYNTHETIC_REPAIRED'


def test_locked_storage_does_not_return_cached_token_or_session(credentials, isolated_keychain):
    store, _ = credentials
    store.migrate_settings()
    store.migrate_session()
    assert store.resolve('token') == 'SYNTHETIC_TOKEN'
    assert store.load_session()['cookies']
    backend = isolated_keychain(store.path)
    backend.security.SecKeychainLock.argtypes = [ctypes.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    with pytest.raises(OSError):
        store.resolve('token')
    with pytest.raises(OSError):
        store.load_session()
    assert all(row['state'] == 'unavailable' for row in store.inventory()['fields'])


def test_changed_account_and_copied_session_reference_cannot_inherit_credentials(credentials):
    store, _ = credentials
    store.migrate_settings()
    store.migrate_session()
    old = json.loads(store.path.read_text())
    old_session = store.session_path(old).read_bytes()
    changed = {**old, 'username': 'bob'}
    store.path.write_text(json.dumps(changed))
    store.session_path(changed).write_bytes(old_session)
    with pytest.raises(OSError):
        store.resolve('token')
    with pytest.raises(OSError):
        store.load_session()
    assert all(row['state'] == 'repair_required' for row in store.inventory()['fields'])


def test_valid_json_with_bad_field_and_bad_session_is_preserved_and_individually_repairable(credentials):
    store, original = credentials
    malformed = {**original, 'token': {'do': 'not reset this JSON'}}
    store.path.write_text(json.dumps(malformed))
    store.session_path(original).write_text('{"cookies":"not-a-list","keep":true}')
    view = store.inventory()
    assert {row['key']: row['state'] for row in view['fields']}['token'] == 'repair_required'
    assert {row['key']: row['state'] for row in view['fields']}['session'] == 'repair_required'
    store.save_field('password', 'SYNTHETIC_REPAIRED', expected_revision=view['revision'])
    assert json.loads(store.path.read_text())['token'] == malformed['token']
    assert json.loads(store.session_path(original).read_text()) == {'cookies': 'not-a-list', 'keep': True}


def test_stale_owner_save_and_agent_management_are_rejected(credentials):
    store, _ = credentials
    store.migrate_settings()
    before = store.inventory()
    store.save_field('token', 'SYNTHETIC_NEW', expected_revision=before['revision'])
    with pytest.raises(ValueError, match='changed'):
        store.save_field('password', 'SYNTHETIC_STALE', expected_revision=before['revision'])
    with identity_scope(session_id='agent'):
        with pytest.raises(PermissionError):
            store.inventory()
        with pytest.raises(PermissionError):
            store.save_field('token', '', expected_revision=before['revision'])


def test_verified_session_rotation_is_revision_bound_and_owner_can_remove_it(credentials):
    store, config = credentials
    store.migrate_settings()
    store.migrate_session()
    before = store.inventory()
    data = {'cookies': [{'domain': 'elearning.example.test', 'name': 'session', 'value': 'SYNTHETIC_ROTATED'}]}
    store.save_session(data, expected_revision=before['session_revision'])
    assert store.load_session() == data
    with pytest.raises(ValueError, match='changed'):
        store.save_session({'cookies': []}, expected_revision=before['session_revision'])
    store.clear_session(expected_revision=store.inventory()['session_revision'])
    assert not store.session_path(config).exists()
    assert store.resolve('token') == 'SYNTHETIC_TOKEN'
    assert json.loads(store.path.read_text())['courses'] == ['42']


def test_late_login_after_account_change_cannot_publish_session(credentials):
    store, config = credentials
    before = store.inventory()
    store.path.write_text(json.dumps({**config, 'username': 'bob'}))
    with pytest.raises(ValueError, match='changed'):
        store.save_session({'cookies': [{'domain': 'elearning.example.test', 'name': 'session',
                                         'value': 'SYNTHETIC_OLD_LOGIN'}]}, expected_revision=before['session_revision'])
    assert not store.session_path({**config, 'username': 'bob'}).exists()


def test_invalid_reference_wrapper_is_not_reported_as_a_usable_session(credentials):
    store, config = credentials
    path = store.session_path(config)
    path.write_text('{"credential":"not-a-reference"}')
    before = path.read_bytes()
    view = store.inventory()
    assert next(row for row in view['fields'] if row['key'] == 'session')['state'] == 'repair_required'
    with pytest.raises(OSError):
        store.load_session()
    assert path.read_bytes() == before
    store.clear_session(expected_revision=view['session_revision'])
    assert not path.exists()


def test_failed_session_file_publication_keeps_old_reference_and_login(credentials, monkeypatch):
    store, config = credentials
    store.migrate_session()
    before = store.session_path(config).read_bytes()
    revision = store.inventory()['session_revision']
    def failed(*args):
        raise OSError('synthetic atomic replacement failure')
    with monkeypatch.context() as scoped:
        scoped.setattr(store, '_write', failed)
        with pytest.raises(OSError):
            store.save_session({'cookies': [{'domain': 'elearning.example.test', 'name': 'session',
                                             'value': 'SYNTHETIC_UNPUBLISHED'}]}, expected_revision=revision)
    assert store.session_path(config).read_bytes() == before
    assert store.load_session()['cookies'][0]['value'] == 'SYNTHETIC_COOKIE'

"""OAuth records share verified Keychain references without stale token fallback."""
import ctypes as c
import json
import sys

import pytest

from agent.tools.mcp import mcp_oauth as oauth
from core.security.credential_values import REFERENCE

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Real macOS Keychain integration')


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / 'mcp_oauth.json'
    monkeypatch.setattr(oauth, '_store_path', lambda: str(path))
    return path


def record(token='SYNTHETIC_ACCESS'):
    return dict(resource_url='https://mcp.example.invalid/api', access_token=token,
                refresh_token='SYNTHETIC_REFRESH', client_secret='SYNTHETIC_CLIENT_SECRET',
                client_id='public-client', metadata={'token_endpoint': 'https://auth.example.invalid/token'}, expires_at=0)


def handler():
    return oauth.OAuthHandler('example', record()['resource_url'], 'http://127.0.0.1/callback')


def test_oauth_tokens_use_verified_references_and_restart_resolves_them(store):
    oauth.save_server_record('example', record())
    text = store.read_text()
    assert 'SYNTHETIC_' not in text
    assert REFERENCE in text
    assert oauth.load_server_record('example')['access_token'] == 'SYNTHETIC_ACCESS'
    assert handler().get_valid_access_token() == 'SYNTHETIC_ACCESS'
    assert store.stat().st_mode & 0o777 == 0o600


def test_failed_token_publication_preserves_disk_and_runtime(store, monkeypatch):
    oauth.save_server_record('example', record())
    client = handler()
    before = store.read_bytes()
    def unavailable(path):
        raise PermissionError('Synthetic Keychain failure')
    monkeypatch.setattr('core.security.env_store.keychain_for', unavailable)
    assert client._absorb_token_response({'access_token': 'SYNTHETIC_NEW'}) is False
    assert client.access_token == 'SYNTHETIC_ACCESS'
    assert store.read_bytes() == before


def test_locked_keychain_clears_cached_tokens_without_changing_valid_json(store, isolated_keychain):
    oauth.save_server_record('example', record())
    client = handler()
    before = store.read_bytes()
    backend = isolated_keychain(store)
    backend.security.SecKeychainLock.argtypes = [c.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    assert client.get_valid_access_token() is None
    assert not client.access_token
    assert oauth.load_server_record('example')['access_token'] == ''
    assert store.read_bytes() == before
    assert oauth.credential_statuses()[0]['configured'] is None


def test_changed_resource_does_not_inherit_another_endpoints_credentials(store):
    oauth.save_server_record('example', record())
    changed = oauth.OAuthHandler('example', 'https://other.example.invalid/api', 'http://127.0.0.1/callback')
    assert changed.get_valid_access_token() is None
    assert not changed.refresh_token and not changed.client_secret and not changed.metadata
    assert oauth.load_server_record('example')['access_token'] == 'SYNTHETIC_ACCESS'


def test_cached_handler_reloads_changed_or_deleted_credentials(store):
    oauth.save_server_record('example', record())
    client = handler()
    oauth.save_server_record('example', record('SYNTHETIC_REPLACEMENT'))
    assert client.get_valid_access_token() == 'SYNTHETIC_REPLACEMENT'
    oauth.clear_server_record('example')
    assert client.get_valid_access_token() is None
    assert not client.refresh_token and not client.client_secret


def test_metadata_update_keeps_unchanged_references_without_decryption(store, monkeypatch):
    oauth.save_server_record('example', record())
    client = handler()
    before = json.loads(store.read_text())['example']
    def no_decrypt(path):
        raise AssertionError('Unchanged OAuth fields must not require Keychain access')
    monkeypatch.setattr('core.security.env_store.keychain_for', no_decrypt)
    client.metadata = {**client.metadata, 'issuer': 'https://auth.example.invalid'}
    client._persist()
    saved = json.loads(store.read_text())['example']
    for key in ('access_token', 'refresh_token', 'client_secret'):
        assert saved[key] == before[key]


def test_unreadable_or_malformed_store_cannot_be_silently_replaced(store):
    store.write_text('{"example": ["KEEP_OWNER_DATA"]}')
    before = store.read_bytes()
    with pytest.raises(ValueError):
        oauth.save_server_record('another', record())
    assert store.read_bytes() == before


def test_existing_plaintext_record_migrates_without_losing_other_fields(store):
    store.write_text(json.dumps({'example': record()}))
    client = handler()
    client._persist()
    assert 'SYNTHETIC_' not in store.read_text()
    assert oauth.load_server_record('example')['metadata'] == record()['metadata']
    assert handler().get_valid_access_token() == 'SYNTHETIC_ACCESS'


def test_registration_failure_does_not_publish_a_new_client_secret(store, monkeypatch):
    client = handler()
    client.metadata = {'registration_endpoint': 'https://auth.example.invalid/register'}
    monkeypatch.setattr(oauth, '_http_post_json', lambda *args, **kwargs: {'client_id': 'public-new-client', 'client_secret': 'SYNTHETIC_REGISTER_SECRET'})
    def unavailable(path):
        raise PermissionError('Synthetic Keychain failure')
    monkeypatch.setattr('core.security.env_store.keychain_for', unavailable)
    assert client._register_client() is False
    assert not client.client_id and not client.client_secret
    assert not store.exists()


def test_access_token_rotation_does_not_decrypt_or_rewrite_other_fields(store, isolated_keychain, monkeypatch):
    oauth.save_server_record('example', record())
    oauth.save_server_record('other', record('SYNTHETIC_OTHER_ACCESS'))
    client = handler()
    before = json.loads(store.read_text())
    backend = isolated_keychain(store)
    original = backend.get
    def only_verify_new_value(name):
        value = original(name)
        assert value == 'SYNTHETIC_NEW_ACCESS', 'Rotation decrypted an unchanged credential'
        return value
    monkeypatch.setattr(backend, 'get', only_verify_new_value)
    monkeypatch.setattr('core.security.env_store.keychain_for', lambda path: backend)
    assert client._absorb_token_response({'access_token': 'SYNTHETIC_NEW_ACCESS'})
    saved = json.loads(store.read_text())
    assert saved['other'] == before['other']
    for key in ('refresh_token', 'client_secret'):
        assert saved['example'][key] == before['example'][key]


def test_token_response_cannot_place_a_storage_reference_into_runtime(store):
    oauth.save_server_record('example', record())
    client = handler()
    reference = json.loads(store.read_text())['example']['access_token']
    before = store.read_bytes()
    assert client._absorb_token_response({'access_token': reference}) is False
    assert client.access_token == 'SYNTHETIC_ACCESS'
    assert store.read_bytes() == before


def test_stale_handler_cannot_overwrite_newer_authorization(store):
    oauth.save_server_record('example', record())
    stale = handler()
    oauth.save_server_record('example', record('SYNTHETIC_NEWER_AUTHORIZATION'))
    before = store.read_bytes()
    assert stale._absorb_token_response({'access_token': 'SYNTHETIC_STALE_REFRESH'}) is False
    assert store.read_bytes() == before
    assert handler().get_valid_access_token() == 'SYNTHETIC_NEWER_AUTHORIZATION'


def test_invalid_reference_never_becomes_a_token_or_resets_valid_json(store):
    oauth.save_server_record('example', record())
    saved = json.loads(store.read_text())
    saved['example']['access_token'] = REFERENCE + 'invalid'
    store.write_text(json.dumps(saved))
    before = store.read_bytes()
    assert handler().get_valid_access_token() is None
    assert store.read_bytes() == before
    assert 'SYNTHETIC_' not in str(oauth.credential_statuses())


def test_metadata_edit_cannot_transfer_existing_auth_secrets_to_another_endpoint(store):
    oauth.save_server_record('example', record())
    client = handler()
    before = store.read_bytes()
    client.metadata = {'token_endpoint': 'https://other.example.invalid/token'}
    with pytest.raises((PermissionError, ValueError), match='endpoint|receiver|authorization'):
        client._persist()
    assert store.read_bytes() == before


def test_a_different_resource_does_not_decrypt_the_original_services_secrets(store, monkeypatch):
    oauth.save_server_record('example', record())
    def forbidden(path):
        raise AssertionError('A different resource must not decrypt the old service credentials')
    monkeypatch.setattr('core.security.env_store.keychain_for', forbidden)
    changed = oauth.OAuthHandler('example', 'https://other.example.invalid/api', 'http://127.0.0.1/callback')
    assert changed.get_valid_access_token() is None

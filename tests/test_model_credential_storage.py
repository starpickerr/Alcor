"""Original model settings must persist verified references and survive restart."""
import ctypes as c
import json
import os
import sys

import pytest

import config as config_module
from config import Config

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Real macOS Keychain integration')


@pytest.fixture
def model_owner(tmp_path, monkeypatch):
    from channel.desktop import desktop_channel as channel
    values = {'agent': False, 'agent_workspace': str(tmp_path / 'workspace'), 'appdata_dir': 'data',
              'security_policy': {}, 'open_ai_api_key': 'SYNTHETIC_OLD_MODEL_KEY',
              'open_ai_api_base': 'https://model.example.invalid/v1', 'custom_providers': []}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(values))
    live = Config(values)
    monkeypatch.setattr(config_module, 'config', live)
    monkeypatch.setattr(config_module, 'get_data_root', lambda: str(tmp_path))
    monkeypatch.setattr(channel, 'get_data_root', lambda: str(tmp_path))
    monkeypatch.setattr(channel.ModelsHandler, '_reset_bridge', staticmethod(lambda: None))
    monkeypatch.setenv('STELLA_DESKTOP', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'SYNTHETIC_OLD_MODEL_KEY')
    monkeypatch.setenv('CUSTOM_PROVIDERS', '')
    for name in config_module.MODEL_CONFIG_TO_ENV.values():
        if name != 'OPENAI_API_KEY':
            monkeypatch.setenv(name, '')
    monkeypatch.setenv('SKILL_IMAGE_GENERATION_CUSTOM_PROVIDER', '')
    return channel.ModelsHandler(), path, live


def test_original_model_api_persists_no_plaintext_and_reloads_the_same_key(model_owner):
    handler, path, live = model_owner
    response = json.loads(handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'SYNTHETIC_NEW_MODEL_KEY'}))
    assert response['status'] == 'success'
    assert 'SYNTHETIC_NEW_MODEL_KEY' not in path.read_text()
    assert live['open_ai_api_key'] == 'SYNTHETIC_NEW_MODEL_KEY'
    config_module.load_config()
    assert config_module.conf()['open_ai_api_key'] == 'SYNTHETIC_NEW_MODEL_KEY'
    assert os.environ['OPENAI_API_KEY'] == 'SYNTHETIC_NEW_MODEL_KEY'
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_secure_save_keeps_disk_memory_and_environment(model_owner, monkeypatch):
    handler, path, live = model_owner
    before = path.read_bytes()
    def unavailable(path):
        raise PermissionError('Keychain unavailable')
    monkeypatch.setattr('core.security.env_store.keychain_for', unavailable)
    with pytest.raises(PermissionError):
        handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'SYNTHETIC_NEW_MODEL_KEY'})
    assert path.read_bytes() == before
    assert live['open_ai_api_key'] == 'SYNTHETIC_OLD_MODEL_KEY'
    assert os.environ['OPENAI_API_KEY'] == 'SYNTHETIC_OLD_MODEL_KEY'


def test_custom_provider_api_keeps_identity_and_credentials_across_restart(model_owner):
    handler, path, _ = model_owner
    for name, key in [('Alpha', 'SYNTHETIC_ALPHA_KEY'), ('Beta', 'SYNTHETIC_BETA_KEY')]:
        result = json.loads(handler._handle_set_custom_provider({'name': name, 'api_base': 'https://model.example.invalid/v1', 'api_key': key, 'model': 'test-model'}))
        assert result['status'] == 'success'
    assert 'SYNTHETIC_ALPHA_KEY' not in path.read_text()
    assert 'SYNTHETIC_BETA_KEY' not in path.read_text()
    ids = [item['id'] for item in config_module.conf()['custom_providers']]
    config_module.load_config()
    rows = config_module.conf()['custom_providers']
    assert [item['id'] for item in rows] == ids
    assert [item['api_key'] for item in rows] == ['SYNTHETIC_ALPHA_KEY', 'SYNTHETIC_BETA_KEY']


def test_locked_keychain_is_not_corrupt_json_and_cannot_restore_a_stale_key(model_owner, isolated_keychain):
    handler, path, _ = model_owner
    handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'SYNTHETIC_NEW_MODEL_KEY'})
    before = path.read_bytes()
    backend = isolated_keychain(path)
    backend.security.SecKeychainLock.argtypes = [c.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    config_module.load_config()
    assert path.read_bytes() == before
    assert not list(path.parent.glob('config.json.corrupted-*'))
    assert config_module.conf()['open_ai_api_key'] == ''
    assert 'OPENAI_API_KEY' not in os.environ
    from core.security.config_credentials import public_states
    assert public_states(path)['open_ai_api_key'] == 'unavailable'


def test_repair_changes_only_the_selected_reference_and_uses_security_page_config(model_owner, isolated_keychain, monkeypatch):
    from agent.permission import center
    from core.security.credential_values import account
    handler, path, live = model_owner
    monkeypatch.setattr(center, '_env_path', lambda: path.parent / '.env')
    monkeypatch.setattr(center, '_refresh_model_clients', lambda: None)
    handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'SYNTHETIC_FIRST'})
    handler._handle_set_provider({'provider_id': 'claudeAPI', 'api_key': 'SYNTHETIC_SECOND'})
    before = json.loads(path.read_text())
    backend = isolated_keychain(path)
    backend.delete(account('open_ai_api_key', before['open_ai_api_key']))
    backend.delete(account('claude_api_key', before['claude_api_key']))
    config_module.load_config()
    def cannot_decrypt_other(self, name):
        if name == account('claude_api_key', before['claude_api_key']):
            raise AssertionError('Unchanged credential must not be decrypted')
        return original(self, name)
    from core.security.keychain import MacOSKeychain
    original = MacOSKeychain.get
    monkeypatch.setattr(MacOSKeychain, 'get', cannot_decrypt_other)
    center.set_secret('OPENAI_API_KEY', 'SYNTHETIC_REPAIRED')
    after = json.loads(path.read_text())
    assert after['claude_api_key'] == before['claude_api_key']
    assert after['open_ai_api_key'] != before['open_ai_api_key']
    assert 'SYNTHETIC_REPAIRED' not in path.read_text()
    assert config_module.conf()['open_ai_api_key'] == 'SYNTHETIC_REPAIRED'
    assert config_module.conf()['claude_api_key'] == ''
    rows = {row['name']: row for row in center.secrets()}
    assert rows['OPENAI_API_KEY']['configured'] is True
    assert rows['CLAUDE_API_KEY']['configured'] is False


def test_custom_provider_edit_failure_keeps_original_runtime_and_references(model_owner, monkeypatch):
    from copy import deepcopy
    handler, path, live = model_owner
    handler._handle_set_custom_provider({'name': 'Alpha', 'api_base': 'https://model.example.invalid/v1', 'api_key': 'SYNTHETIC_ALPHA'})
    before, runtime = path.read_bytes(), deepcopy(live)
    def failed(*args):
        raise OSError('Synthetic publication failure')
    monkeypatch.setattr('core.security.storage.private_atomic_write', failed)
    with pytest.raises(OSError):
        handler._handle_set_custom_provider({'id': live['custom_providers'][0]['id'], 'name': 'Renamed', 'api_key': 'SYNTHETIC_REPLACEMENT'})
    assert path.read_bytes() == before
    assert live == runtime


def test_custom_provider_reorder_and_edit_preserve_other_unavailable_key(model_owner, isolated_keychain):
    from core.security.credential_values import account
    handler, path, live = model_owner
    for name in ('Alpha', 'Beta'):
        handler._handle_set_custom_provider({'name': name, 'api_base': 'https://model.example.invalid/v1', 'api_key': 'SYNTHETIC_' + name})
    stored = json.loads(path.read_text())
    alpha, beta = stored['custom_providers']
    isolated_keychain(path).delete(account('CUSTOM:' + alpha['id'], alpha['api_key']))
    stored['custom_providers'] = [beta, alpha]
    path.write_text(json.dumps(stored))
    config_module.load_config()
    handler._handle_set_custom_provider({'id': beta['id'], 'name': 'Beta edited', 'api_key': 'SYNTHETIC_NEW_BETA'})
    rows = json.loads(path.read_text())['custom_providers']
    assert rows[1]['api_key'] == alpha['api_key']
    assert rows[0]['id'] == beta['id']
    config_module.load_config()
    assert [p['api_key'] for p in config_module.conf()['custom_providers']] == ['SYNTHETIC_NEW_BETA', '']


def test_fresh_model_settings_can_save_from_template(model_owner):
    handler, path, live = model_owner
    path.unlink()
    result = json.loads(handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'SYNTHETIC_FRESH'}))
    assert result['status'] == 'success'
    assert live['open_ai_api_key'] == 'SYNTHETIC_FRESH'
    assert 'SYNTHETIC_FRESH' not in path.read_text()


def test_source_restart_cannot_override_a_missing_keychain_reference(model_owner, isolated_keychain, monkeypatch):
    from core.security.credential_values import account
    handler, path, _ = model_owner
    handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'SYNTHETIC_SAVED'})
    isolated_keychain(path).delete(account('open_ai_api_key', json.loads(path.read_text())['open_ai_api_key']))
    monkeypatch.delenv('STELLA_DESKTOP')
    monkeypatch.setenv('OPENAI_API_KEY', 'SYNTHETIC_EXPIRED')
    config_module.load_config()
    assert config_module.conf()['open_ai_api_key'] == ''
    assert 'OPENAI_API_KEY' not in os.environ


def test_a_copied_reference_is_rejected_before_publication(model_owner):
    handler, path, _ = model_owner
    handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'SYNTHETIC_SAVED'})
    before = path.read_bytes()
    with pytest.raises(PermissionError):
        handler._handle_set_provider({'provider_id': 'claudeAPI', 'api_key': json.loads(before)['open_ai_api_key']})
    assert path.read_bytes() == before


def test_model_cards_report_missing_saved_credentials_without_exposing_short_keys(model_owner, isolated_keychain):
    from core.security.credential_values import account
    handler, path, _ = model_owner
    handler._handle_set_provider({'provider_id': 'openai', 'api_key': 'tiny'})
    card = next(row for row in handler._provider_overview() if row['id'] == 'openai')
    assert card['api_key_masked'] != 'tiny'
    before = json.loads(path.read_text())
    isolated_keychain(path).delete(account('open_ai_api_key', before['open_ai_api_key']))
    config_module.load_config()
    card = next(row for row in handler._provider_overview() if row['id'] == 'openai')
    assert card['configured'] is False
    assert card['credential_status'] == 'repair-required'
    assert before['open_ai_api_key'] not in json.dumps(card)


def test_legacy_duplicate_cleanup_failure_cannot_leave_a_stale_runtime_key(model_owner, monkeypatch):
    from agent.permission import center
    from core.security.env_store import EnvironmentFile
    handler, path, live = model_owner
    legacy = path.parent / '.env'
    legacy.write_text('OPENAI_API_KEY=SYNTHETIC_EXPIRED\n')
    monkeypatch.setattr(center, '_env_path', lambda: legacy)
    monkeypatch.setattr(center, '_refresh_model_clients', lambda: None)
    def failed(*args):
        raise OSError('Synthetic legacy cleanup failure')
    monkeypatch.setattr(EnvironmentFile, 'update_secret', failed)
    center.set_secret('OPENAI_API_KEY', 'SYNTHETIC_CURRENT')
    assert live['open_ai_api_key'] == os.environ['OPENAI_API_KEY'] == 'SYNTHETIC_CURRENT'
    assert 'SYNTHETIC_CURRENT' not in path.read_text()
    assert legacy.read_text() == 'OPENAI_API_KEY=SYNTHETIC_EXPIRED\n'


@pytest.mark.parametrize('provider,field,env', [('bocha', 'BOCHA_API_KEY', 'BOCHA_API_KEY'),
                                             ('zhipu', 'zhipu_ai_api_key', 'ZHIPUAI_API_KEY')])
def test_missing_search_credentials_cannot_restore_environment_aliases(model_owner, isolated_keychain, monkeypatch, provider, field, env):
    from agent.tools.web_search.web_search import _get_api_key
    from core.security.credential_values import account
    handler, path, _ = model_owner
    monkeypatch.setenv(env, 'SYNTHETIC_EXPIRED_SEARCH')
    if provider == 'bocha':
        handler._handle_set_search_credential({'api_key': 'SYNTHETIC_CURRENT_SEARCH'})
    else:
        handler._handle_set_provider({'provider_id': provider, 'api_key': 'SYNTHETIC_CURRENT_SEARCH'})
    stored = json.loads(path.read_text())
    reference = stored['tools']['web_search']['bocha_api_key'] if provider == 'bocha' else stored[field]
    isolated_keychain(path).delete(account(field, reference))
    config_module.load_config()
    assert _get_api_key(provider) == ''
    assert env not in os.environ


def test_search_credential_deletion_clears_the_previous_environment_alias(model_owner, monkeypatch):
    from agent.tools.web_search.web_search import _get_api_key
    handler, path, _ = model_owner
    monkeypatch.setenv('BOCHA_API_KEY', 'SYNTHETIC_EXPIRED_SEARCH')
    handler._handle_set_search_credential({'api_key': ''})
    assert _get_api_key('bocha') == ''
    assert 'BOCHA_API_KEY' not in os.environ


@pytest.mark.parametrize('name', ['GITHUB_TOKEN', 'OPENAI_API_BASE', '', None])
def test_model_credential_action_rejects_non_model_keys(model_owner, monkeypatch, name):
    handler, path, _ = model_owner
    import channel.desktop.desktop_channel as channel
    monkeypatch.setattr(channel, '_require_auth', lambda: None)
    monkeypatch.setattr(channel.web, 'header', lambda *a, **k: None)
    monkeypatch.setattr(channel.web, 'data', lambda: json.dumps({'action': 'credential', 'name': name, 'value': 'SYNTHETIC'}).encode())
    before = path.read_bytes()
    result = json.loads(handler.POST())
    assert result['status'] == 'error'
    assert path.read_bytes() == before


def test_model_credential_action_uses_original_secure_store(model_owner, monkeypatch):
    handler, path, _ = model_owner
    import channel.desktop.desktop_channel as channel
    monkeypatch.setattr(channel, '_require_auth', lambda: None)
    monkeypatch.setattr(channel.web, 'header', lambda *a, **k: None)
    for value in ('SYNTHETIC_EDITOR_KEY', None):
        monkeypatch.setattr(channel.web, 'data', lambda: json.dumps({'action': 'credential', 'name': 'OPENAI_API_KEY', 'value': value}).encode())
        assert json.loads(handler.POST())['status'] == 'success'
        assert 'SYNTHETIC_EDITOR_KEY' not in path.read_text()
        from core.security.config_credentials import model_key
        if value:
            assert model_key('open_ai_api_key') == value
        else:
            from core.security.keychain import KeychainUnavailable
            with pytest.raises(KeychainUnavailable):
                model_key('open_ai_api_key')
            assert json.loads(path.read_text())['open_ai_api_key'] == ''

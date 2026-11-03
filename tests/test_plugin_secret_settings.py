"""Declared plugin credentials stay in the existing settings store, never Worker data."""
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest

from common.runtime_identity import identity_scope
from core.plugins.management import ManagementStore, RevisionConflict
from core.plugins.package_ops import PackageStore
from core.plugins.settings import PluginSettingsService
from core.security.credential_values import REFERENCE, account

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Real macOS Keychain integration')
KEY = ('stella.sample.alpha', 'alice', 'preferences', 'general')


@pytest.fixture
def settings(tmp_path):
    source = tmp_path / 'source'
    shutil.copytree('samples/plugins/alpha', source)
    manifest = json.loads((source / 'plugin.json').read_text())
    manifest['contributes']['settings'] = ['preferences']
    (source / 'plugin.json').write_text(json.dumps(manifest))
    (source / 'settings').mkdir()
    descriptor = {'id': 'preferences', 'pluginId': KEY[0], 'scope': 'user',
                  'categories': [{'id': 'general', 'title': 'General'}], 'fields': [
                      {'key': 'notes', 'title': 'Notes', 'type': 'string', 'category': 'general', 'default': ''},
                      *[{'key': key, 'title': key, 'type': 'string', 'category': 'general', 'secret': True,
                         'validation': {'minLength': 5}} for key in ('password', 'token')]]}
    (source / 'settings/preferences.json').write_text(json.dumps(descriptor))
    entry = source / manifest['runtime']['entry']
    entry.write_text('def handle_invoke(params):\n'
                     '    if params["input"].get("readpath"):\n'
                     '        try:\n'
                     '            with open(params["input"]["readpath"], "rb") as stream: stream.read()\n'
                     '            value = {"readable":True}\n'
                     '        except PermissionError:\n'
                     '            value = {"readable":False}\n'
                     '        return {"status":"succeeded", "sideEffects":"none", "value":value}\n'
                     '    return {"status":"succeeded", "sideEffects":"none",\n'
                     '            "value":params.get("_hostDataHandle", {}).get("settings", {})}\n')
    store = ManagementStore(tmp_path / 'packages')
    packages = PackageStore(store.root)
    op = packages.stage(source)
    packages.inspect(op.operation_id)
    packages.prepare(op.operation_id)
    packages.commit(op.operation_id)
    return PluginSettingsService(store)


def save(service, changes, revision=1):
    return service.save(*KEY, changes, expected_revision=revision)


def test_original_settings_save_encrypts_credentials_and_runtime_read_never_decrypts(settings, isolated_keychain, monkeypatch):
    snapshot = save(settings, {'notes': 'ordinary', 'password': 'SYNTHETIC_PLUGIN_PASSWORD'})
    raw = settings.management.settings(*KEY)
    reference = raw['values']['password']
    assert reference.startswith(REFERENCE)
    assert b'SYNTHETIC_PLUGIN_PASSWORD' not in settings.management.path.read_bytes()
    assert snapshot['values'] == {'notes': 'ordinary'}
    assert snapshot['secretStates'] == {'password': 'configured', 'token': 'not_configured'}
    from core.security.keychain import MacOSKeychain
    def forbidden(*args):
        raise AssertionError('Runtime settings must not inspect or decrypt credentials')
    monkeypatch.setattr(MacOSKeychain, 'get', forbidden)
    monkeypatch.setattr(MacOSKeychain, 'contains', forbidden)
    runtime = settings.read(*KEY)
    assert runtime['values'] == {'notes': 'ordinary'}
    assert 'secretStates' not in runtime
    assert 'SYNTHETIC' not in json.dumps(runtime)
    assert REFERENCE not in json.dumps(runtime)


def test_failed_verification_preserves_revision_and_prior_keychain_entry(settings, isolated_keychain, monkeypatch):
    save(settings, {'password': 'SYNTHETIC_ORIGINAL'})
    before = settings.management.settings(*KEY)
    from core.security.keychain import MacOSKeychain
    original_get = MacOSKeychain.get
    def missing_new_value(self, name):
        value = original_get(self, name)
        return None if value == 'SYNTHETIC_REPLACEMENT' else value
    monkeypatch.setattr(MacOSKeychain, 'get', missing_new_value)
    with pytest.raises(OSError):
        save(settings, {'password': 'SYNTHETIC_REPLACEMENT', 'notes': 'must not publish'}, revision=2)
    assert settings.management.settings(*KEY) == before
    assert settings.read(*KEY, credential_status=True)['secretStates']['password'] == 'configured'


def test_one_field_repair_preserves_other_missing_reference_without_decryption(settings, isolated_keychain, monkeypatch):
    save(settings, {'password': 'SYNTHETIC_FIRST', 'token': 'SYNTHETIC_SECOND'})
    before = settings.management.settings(*KEY)['values']
    backend = isolated_keychain(settings.management.path)
    name = json.dumps([*KEY, 'token'], separators=(',', ':'))
    backend.delete(account(name, before['token']))
    from core.security.keychain import MacOSKeychain
    original_get = MacOSKeychain.get
    def forbid_unchanged(self, identifier):
        if identifier == account(name, before['token']):
            raise AssertionError('Unchanged credential was decrypted')
        return original_get(self, identifier)
    monkeypatch.setattr(MacOSKeychain, 'get', forbid_unchanged)
    snapshot = save(settings, {'password': 'SYNTHETIC_REPAIRED'}, revision=2)
    after = settings.management.settings(*KEY)['values']
    assert after['token'] == before['token']
    assert after['password'] != before['password']
    assert snapshot['secretStates'] == {'password': 'configured', 'token': 'repair_required'}
    deleted = save(settings, {'token': ''}, revision=3)
    assert deleted['secretStates']['token'] == 'not_configured'
    assert settings.management.settings(*KEY)['values']['password'] == after['password']


def test_owner_read_migrates_legacy_plaintext_but_never_exposes_it_before_migration(settings):
    settings.management.save_settings(*KEY, {'password': 'SYNTHETIC_LEGACY_PLUGIN_PASSWORD'}, expected_revision=1)
    assert settings.read(*KEY)['values'] == {'notes': ''}
    public = settings.read(*KEY, credential_status=True)
    assert public['secretStates']['password'] == 'configured'
    assert public['revision'] == 3
    assert b'SYNTHETIC_LEGACY_PLUGIN_PASSWORD' not in settings.management.path.read_bytes()
    assert settings.management.settings(*KEY)['values']['password'].startswith(REFERENCE)


def test_failed_migration_keeps_valid_store_and_allows_selected_removal(settings, monkeypatch):
    settings.management.save_settings(*KEY, {'password': 'SYNTHETIC_LEGACY'}, expected_revision=1)
    before = settings.management.settings(*KEY)
    def unavailable(*args):
        raise PermissionError('Synthetic locked Keychain')
    monkeypatch.setattr('core.security.env_store.keychain_for', unavailable)
    public = settings.read(*KEY, credential_status=True)
    assert public['secretStates']['password'] == 'repair_required'
    assert settings.management.settings(*KEY) == before
    assert public['values'] == {'notes': ''}
    save(settings, {'password': ''}, revision=2)
    assert 'password' not in settings.management.settings(*KEY)['values']


def test_removed_declarations_cannot_release_saved_secrets_and_owner_can_remove_orphans(settings):
    save(settings, {'password': 'SYNTHETIC_OLD_DECLARATION'})
    package = settings.packages.current_package(KEY[0])
    path = Path(package['path']) / 'settings/preferences.json'
    descriptor = json.loads(path.read_text())
    descriptor['fields'] = [field for field in descriptor['fields'] if not field.get('secret')]
    path.write_text(json.dumps(descriptor))
    assert settings.read(*KEY)['values'] == {'notes': ''}
    inventory = settings.credentials(KEY[0], KEY[1])
    assert len(inventory) == 1
    assert inventory[0]['field'] == 'password'
    assert inventory[0]['retired'] is True
    assert inventory[0]['state'] == 'configured'
    assert 'SYNTHETIC' not in json.dumps(inventory) and REFERENCE not in json.dumps(inventory)
    # Even removing the entire package leaves an Owner cleanup entry.
    (settings.packages.packages / KEY[0] / 'current.json').unlink()
    with pytest.raises(RevisionConflict):
        settings.remove_secret(*KEY, 'password', expected_revision=1)
    settings.remove_secret(*KEY, 'password', expected_revision=2)
    assert settings.credentials(KEY[0], KEY[1]) == []
    assert settings.management.settings(*KEY)['values'] == {}


def test_agent_cannot_manage_or_inspect_credential_metadata(settings):
    with identity_scope(session_id='agent-session', agent_id='default'):
        with pytest.raises(PermissionError):
            save(settings, {'password': 'SYNTHETIC_AGENT_WRITE'})
        with pytest.raises(PermissionError):
            settings.read(*KEY, credential_status=True)
        with pytest.raises(PermissionError):
            settings.credentials(KEY[0], KEY[1])
    assert settings.management.settings(*KEY)['revision'] == 1


def test_sqlite_publication_failure_does_not_retire_the_previous_native_item(settings, isolated_keychain):
    save(settings, {'password': 'SYNTHETIC_BEFORE_DB_FAILURE'})
    before = settings.management.settings(*KEY)
    with settings.management._db() as db:
        db.execute("CREATE TRIGGER reject_settings BEFORE UPDATE ON settings BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        save(settings, {'password': 'SYNTHETIC_UNPUBLISHED'}, revision=2)
    assert settings.management.settings(*KEY) == before
    name = json.dumps([*KEY, 'password'], separators=(',', ':'))
    assert isolated_keychain(settings.management.path).get(account(name, before['values']['password'])) == 'SYNTHETIC_BEFORE_DB_FAILURE'


def test_locked_and_invalid_references_offer_metadata_without_changing_saved_json(settings, isolated_keychain):
    import ctypes as c
    save(settings, {'password': 'SYNTHETIC_LOCKED'})
    backend = isolated_keychain(settings.management.path)
    backend.security.SecKeychainLock.argtypes = [c.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    before = settings.management.settings(*KEY)
    assert settings.read(*KEY, credential_status=True)['secretStates']['password'] == 'unavailable'
    assert settings.management.settings(*KEY) == before
    # A normal settings edit must not attempt to decrypt the locked credential.
    assert save(settings, {'notes': 'ordinary edit'}, revision=2)['values']['notes'] == 'ordinary edit'
    with settings.management._db() as db:
        db.execute('UPDATE settings SET value=?', (json.dumps({'password': REFERENCE + 'bad'}),))
    assert settings.read(*KEY, credential_status=True)['secretStates']['password'] == 'repair_required'
    snapshot = save(settings, {'password': ''}, revision=3)
    assert snapshot['secretStates']['password'] == 'not_configured'


def test_native_original_settings_api_reports_status_and_cleans_removed_credentials(settings, monkeypatch):
    from channel.desktop import desktop_channel as channel
    monkeypatch.setattr(channel, '_require_auth', lambda: None)
    monkeypatch.setattr(channel.web, 'header', lambda *args: None)
    monkeypatch.setattr(channel, '_plugin_settings_service', lambda: settings)
    monkeypatch.setattr(channel, '_plugin_user', lambda: KEY[1])
    save(settings, {'password': 'SYNTHETIC_NATIVE_API'})
    def request(action, **extra):
        body = dict(action=action, pluginId=KEY[0], **extra)
        monkeypatch.setattr(channel.web, 'data', lambda: json.dumps(body).encode())
        return json.loads(channel.PluginSettingsHandler().POST())
    response = request('read', settingsId=KEY[2], category=KEY[3])
    assert response['result']['secretStates']['password'] == 'configured'
    assert response['result']['values'] == {'notes': ''}
    assert 'SYNTHETIC' not in json.dumps(response) and REFERENCE not in json.dumps(response)
    (settings.packages.packages / KEY[0] / 'current.json').unlink()
    response = request('credentials')
    assert response['status'] == 'ok'
    assert response['result'][0]['retired'] is True
    response = request('remove-secret', settingsId=KEY[2], category=KEY[3], field='password', expectedRevision=2)
    assert response == {'status': 'ok', 'result': []}


def test_credential_directory_keeps_removed_package_entries_and_the_original_config_link(settings, monkeypatch):
    from core.plugins import management
    from core.security.credentials import plugin_credentials
    save(settings, {'password': 'SYNTHETIC_DIRECTORY'})
    (settings.packages.packages / KEY[0] / 'current.json').unlink()
    monkeypatch.setattr(management, 'default_store', lambda: settings.management)
    with identity_scope(user_id=KEY[1]):
        rows = list(plugin_credentials())
    assert len(rows) == 1
    assert rows[0]['configured'] is True
    assert rows[0]['account'] == 'alice'
    assert rows[0]['configure_url'] == '/skills/plugins/stella.sample.alpha?section=configuration'
    assert 'SYNTHETIC' not in json.dumps(rows) and REFERENCE not in json.dumps(rows)


def test_removed_package_configuration_remains_reachable_from_the_capability_directory(settings, monkeypatch):
    from core.capabilities.catalog import CapabilityCatalog
    from core.plugins import management
    from core.plugins.lifecycle import PluginManager
    save(settings, {'password': 'SYNTHETIC_RETAINED'})
    (settings.packages.packages / KEY[0] / 'current.json').unlink()
    monkeypatch.setattr(management, 'default_store', lambda: settings.management)
    manager = PluginManager(settings.management, catalog_path=settings.management.root / 'absent.json')
    catalog = CapabilityCatalog(settings.management.root, plugin_manager=manager, tool_rows=lambda: [])
    with identity_scope(user_id=KEY[1]):
        detail = catalog.detail('plugins', KEY[0])
    assert detail['retained'] is True
    assert detail['enabled'] is False
    assert 'configuration' in detail['tabs']


def test_valid_json_with_invalid_secret_value_is_repairable_without_reset(settings):
    settings.management.save_settings(*KEY, {'password': 123, 'notes': 'keep'}, expected_revision=1)
    assert settings.read(*KEY, credential_status=True)['secretStates']['password'] == 'repair_required'
    snapshot = save(settings, {'password': ''}, revision=2)
    assert snapshot['values'] == {'notes': 'keep'}
    assert snapshot['secretStates']['password'] == 'not_configured'


def test_repairing_one_credential_preserves_another_invalid_saved_field(settings):
    settings.management.save_settings(*KEY, {'password': 0, 'token': ['invalid', 'preserve']}, expected_revision=1)
    before = settings.read(*KEY, credential_status=True)
    assert before['secretStates'] == {'password': 'repair_required', 'token': 'repair_required'}
    snapshot = save(settings, {'password': 'SYNTHETIC_REPAIR_ONLY'}, revision=2)
    assert snapshot['secretStates'] == {'password': 'configured', 'token': 'repair_required'}
    assert settings.management.settings(*KEY)['values']['token'] == ['invalid', 'preserve']
    assert settings.read(*KEY)['values'] == {'notes': ''}


def test_real_managed_worker_receives_only_ordinary_settings(settings, monkeypatch, offline_runtime_artifacts):
    from core.plugins import management
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    from core.plugins.storage import private_data_handle
    save(settings, {'notes': 'normal worker value', 'password': 'SYNTHETIC_WORKER_FORBIDDEN'})
    monkeypatch.setattr(management, 'default_store', lambda: settings.management)
    settings.management.set_intent(KEY[0], KEY[1], 'enabled')
    package = settings.packages.current_package(KEY[0])
    handle = private_data_handle(KEY[0], KEY[1], ensure=True)
    worker = PluginSession(Path(package['path']), source_label='third-party-sample', registry=PluginRegistry(),
                           user_id=KEY[1], python_executable=sys.executable, data_handle=handle)
    try:
        worker.start()
        result = worker.invoke_contract(contract='stella.sample.alpha@1', method='ping', input_payload={},
            origin='ui', grant_ref='owner-settings-test', data_handle=handle)
        assert result['status'] == 'succeeded'
        assert result['value'] == {'notes': 'normal worker value'}
        rejected = worker.invoke_contract(contract='stella.sample.alpha@1', method='ping',
            input_payload={'readpath': str(settings.management.path)}, origin='ui',
            grant_ref='owner-settings-test', data_handle=handle)
        assert rejected['status'] == 'succeeded'
        assert rejected['value'] == {'readable': False}
    finally:
        worker.dispose()

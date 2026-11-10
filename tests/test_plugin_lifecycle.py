from pathlib import Path
import json

import pytest

from core.plugins.management import ManagementStore
from core.plugins.package_ops import _sha256_tree
from core.plugins.lifecycle import PluginManager


def _catalog(tmp_path, root=Path('stella-plugins-workspace/plugins/bills')):
    manifest = json.loads((root / 'plugin.json').read_text())
    file = tmp_path / 'catalog.json'
    file.write_text(json.dumps({'coreApi': '1.0.0', 'plugins': [{
        'pluginId': manifest['id'], 'version': manifest['version'], 'hostApi': manifest['hostApi'],
        'path': str(root.resolve()), 'sha256': _sha256_tree(root),
    }]}))
    return file


def test_install_uses_real_migration_then_selects_package_and_keeps_disabled_intent(tmp_path, monkeypatch):
    from core.plugins import management
    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    store.set_intent('stella.bills', 'local-default', 'disabled')
    data = tmp_path / 'domain' / 'bills.sqlite'
    manager = PluginManager(store, catalog_path=_catalog(tmp_path))
    result = manager.install('stella.bills', user_id='local-default',
                             data_handle={'dbPath': str(data), 'namespace': 'stella.bills'})
    assert result['stage'] == 'completed'
    assert data.exists()
    assert manager.packages.current_package('stella.bills')
    assert store.instance('stella.bills', 'local-default')['intent'] == 'disabled'
    assert manager.inventory('local-default')[0]['state'] == 'stopped'


def test_inventory_and_disable_are_scoped_to_plugin_user(tmp_path, monkeypatch):
    from core.plugins import lifecycle, management
    from core.plugins.registry import ContributionSnapshot, PluginRegistry

    store = ManagementStore(tmp_path / 'packages')
    registry = PluginRegistry()
    monkeypatch.setattr(management, 'default_store', lambda: store)
    monkeypatch.setattr(lifecycle, 'DEFAULT_REGISTRY', registry)
    plugin = 'stella.sample.alpha'
    manager = PluginManager(store, catalog_path=_catalog(tmp_path, Path('samples/plugins/alpha')))
    manager.install(plugin, user_id='alice')
    for user in ('alice', 'bob'):
        manager.set_intent(plugin, user, 'enabled')
        registry.publish(ContributionSnapshot(plugin, registry.next_generation(plugin, user_id=user),
            user, 'digest', {}, (plugin + '@1',), user))
    for user in ('alice', 'bob'):
        row = manager.inventory(user)[0]
        assert row['state'] == 'active' and row['sourceLabel'] == user
    pending = ContributionSnapshot(plugin, registry.next_generation(plugin, user_id='alice'),
                                   'alice', 'digest', {}, (), 'alice')
    manager.set_intent(plugin, 'alice', 'disabled')
    with pytest.raises(PermissionError):
        registry.publish(pending)
    assert manager.inventory('alice')[0]['services'] == []
    assert manager.inventory('bob')[0]['state'] == 'active'


def test_remove_preserves_data_and_prevents_bootstrap_resurrection(tmp_path, monkeypatch):
    from core.plugins import management
    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    data = tmp_path / 'domain' / 'bills.sqlite'
    manager = PluginManager(store, catalog_path=_catalog(tmp_path))
    manager.install('stella.bills', user_id='local-default',
                    data_handle={'dbPath': str(data), 'namespace': 'stella.bills'})
    original = data.read_bytes()
    manager.set_intent('stella.bills', 'local-default', 'removed')
    assert data.read_bytes() == original
    assert manager.inventory('local-default')[0]['intent'] == 'removed'
    assert not manager.inventory('local-default')[0]['services']


@pytest.mark.parametrize('stage', ['prepared', 'draining', 'migrated', 'selected'])
def test_resume_lifecycle_selection_after_crash(tmp_path, monkeypatch, stage):
    from core.plugins import management
    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    manager = PluginManager(store, catalog_path=_catalog(tmp_path))
    data = tmp_path / 'bills.sqlite'

    class Crash(BaseException):
        pass

    def interrupt(current, op):
        if current == stage:
            raise Crash()

    with pytest.raises(Crash):
        manager.install('stella.bills', user_id='local-default',
                        data_handle={'dbPath': str(data), 'namespace': 'stella.bills'}, checkpoint=interrupt)
    operation = [op for op in store.unfinished() if op['kind'] == 'install'][0]
    restarted = PluginManager(ManagementStore(tmp_path / 'packages'), catalog_path=tmp_path / 'offline-catalog')
    result = restarted.resume(operation['operationId'])
    assert result['stage'] == 'completed'
    assert restarted.packages.current_package('stella.bills')
    assert store.instance('stella.bills', 'local-default')['state'] == 'stopped'


def test_one_corrupt_pointer_keeps_other_installed_inventory_visible(tmp_path, monkeypatch):
    from core.plugins import management
    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    manager = PluginManager(store, catalog_path=_catalog(tmp_path))
    manager.install('stella.bills', user_id='local-default',
                    data_handle={'dbPath': str(tmp_path / 'bills.sqlite'), 'namespace': 'stella.bills'})
    bad = manager.packages.packages / 'stella.tasks' / 'current.json'
    bad.parent.mkdir()
    bad.write_text('{broken')
    rows = {row['pluginId']: row for row in manager.inventory('local-default')}
    assert rows['stella.bills']['version']
    assert rows['stella.tasks']['state'] == 'failed'
    assert rows['stella.tasks']['error']


def test_failed_candidate_preflight_preserves_running_version_and_user_intents(tmp_path, monkeypatch):
    import shutil
    from core.plugins import management
    from core.plugins.host import PluginSession, PluginHostError
    from core.plugins.registry import PluginRegistry
    from core.runtime.environments import EnvironmentStore

    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    package = tmp_path / 'original'
    shutil.copytree('samples/plugins/alpha', package)
    manager = PluginManager(store, catalog_path=_catalog(tmp_path, package))
    manager.install('stella.sample.alpha', user_id='alice')
    selected = manager.packages.current_package('stella.sample.alpha')
    session = PluginSession(Path(selected['path']), source_label='installed', user_id='alice',
                            registry=PluginRegistry())
    session.start()
    store.set_runtime('stella.sample.alpha', 'alice', 'active')
    store.set_intent('stella.sample.alpha', 'bob', 'disabled')
    environment = EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice')
    revision = store.source_revision('stella.sample.alpha', 'alice')
    candidate = tmp_path / 'candidate'
    shutil.copytree(package, candidate)
    manifest = json.loads((candidate / 'plugin.json').read_text())
    manifest['version'] = '1.0.1'
    (candidate / 'plugin.json').write_text(json.dumps(manifest))
    (candidate / 'backend/main.py').write_text('raise RuntimeError("candidate cannot initialize")\n')
    _catalog(tmp_path, candidate)
    try:
        with pytest.raises(PluginHostError):
            manager.install('stella.sample.alpha', user_id='alice')
        assert store.runtime_state('stella.sample.alpha', 'alice') == 'active'
        assert session.is_running
        assert manager.packages.current_package('stella.sample.alpha') == selected
        assert EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice') == environment
        assert store.source_revision('stella.sample.alpha', 'alice') == revision
        assert store.instance('stella.sample.alpha', 'bob')['intent'] == 'disabled'
        with store._db() as db:
            failed_id = db.execute("SELECT id FROM operations WHERE kind='install' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        operation = store.operation(failed_id)
        assert operation['stage'] == 'failed' and operation['error']
        assert store.unfinished('stella.sample.alpha') == []
        with pytest.raises(ValueError, match='preflight'):
            manager.resume(failed_id)
    finally:
        session.dispose()


@pytest.mark.parametrize('operation', ['update', 'disable', 'remove'])
def test_lifecycle_reaps_non_domain_plugin_workers_and_preserves_private_data(tmp_path, monkeypatch, operation):
    import shutil
    from core.plugins import management
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    from core.plugins.storage import private_data_handle
    from core.runtime.environments import EnvironmentStore

    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    package = tmp_path / 'package'
    shutil.copytree('samples/plugins/alpha', package)
    manager = PluginManager(store, catalog_path=_catalog(tmp_path, package))
    manager.install('stella.sample.alpha', user_id='alice')
    selected = manager.packages.current_package('stella.sample.alpha')
    registry = PluginRegistry()
    session = PluginSession(Path(selected['path']), source_label='installed', user_id='alice', registry=registry)
    session.start()
    process = session._proc
    private = Path(private_data_handle('stella.sample.alpha', 'alice', ensure=True)['dataDir'])
    (private / 'credentials').write_text('KEEP_ACCOUNT')
    old_environment = EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice')
    try:
        if operation == 'update':
            manifest = json.loads((package / 'plugin.json').read_text())
            manifest['version'] = '1.0.1'
            (package / 'plugin.json').write_text(json.dumps(manifest))
            _catalog(tmp_path, package)

            def prepared(stage, op):
                if stage == 'prepared':
                    assert session.is_running, 'Old worker stopped before candidate validation finished'
                    assert EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice') == old_environment

            manager.install('stella.sample.alpha', user_id='alice', checkpoint=prepared)
            assert manager.packages.current_package('stella.sample.alpha')['version'] == '1.0.1'
        else:
            manager.set_intent('stella.sample.alpha', 'alice', 'disabled' if operation == 'disable' else 'removed')
        assert process.poll() is not None, 'Lifecycle left an old plugin process alive'
        assert registry.get('stella.sample.alpha') is None
        assert (private / 'credentials').read_text() == 'KEEP_ACCOUNT'
    finally:
        session.dispose()


@pytest.mark.parametrize('state', ['disabled', 'removed', 'maintenance', 'old-version'])
def test_managed_worker_start_respects_admission_and_current_package(tmp_path, monkeypatch, state):
    import shutil
    from core.plugins import management
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry

    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    package = tmp_path / 'package'
    shutil.copytree('samples/plugins/alpha', package)
    manager = PluginManager(store, catalog_path=_catalog(tmp_path, package))
    manager.install('stella.sample.alpha', user_id='alice')
    selected = manager.packages.current_package('stella.sample.alpha')
    if state == 'old-version':
        manifest = json.loads((package / 'plugin.json').read_text())
        manifest['version'] = '1.0.1'
        (package / 'plugin.json').write_text(json.dumps(manifest))
        _catalog(tmp_path, package)
        manager.install('stella.sample.alpha', user_id='alice')
    elif state == 'maintenance':
        store.set_runtime('stella.sample.alpha', 'alice', 'maintenance')
    else:
        manager.set_intent('stella.sample.alpha', 'alice', state)
    session = PluginSession(Path(selected['path']), source_label='installed', user_id='alice', registry=PluginRegistry())
    try:
        with pytest.raises(PermissionError):
            session.start()
        assert session._proc is None
        assert session.registry.get('stella.sample.alpha') is None
    finally:
        session.dispose()


def test_disabling_one_user_does_not_stop_another_users_worker(tmp_path, monkeypatch):
    import shutil
    from core.plugins import management
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry

    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    package = tmp_path / 'package'
    shutil.copytree('samples/plugins/alpha', package)
    manager = PluginManager(store, catalog_path=_catalog(tmp_path, package))
    manager.install('stella.sample.alpha', user_id='alice')
    manager.adopt_current('stella.sample.alpha', user_id='bob')
    selected = manager.packages.current_package('stella.sample.alpha')
    sessions = [PluginSession(Path(selected['path']), source_label='installed', user_id=user,
                              registry=PluginRegistry()) for user in ['alice', 'bob']]
    try:
        for session in sessions:
            session.start()
        manager.set_intent('stella.sample.alpha', 'alice', 'disabled')
        assert sessions[0]._proc is None
        assert sessions[1].is_running, 'Disabling one owner stopped an unrelated owner'
        assert store.instance('stella.sample.alpha', 'bob')['intent'] == 'enabled'
    finally:
        for session in sessions:
            session.dispose()

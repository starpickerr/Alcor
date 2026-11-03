"""Owner rebuilds validate real isolated workers without replacing business data."""
import json
from pathlib import Path
import shutil

import pytest

from core.plugins.host import PluginSession, PluginHostError
from core.plugins.lifecycle import PluginManager
from core.plugins.management import ManagementStore
from core.plugins.package_ops import _sha256_tree
from core.plugins.registry import PluginRegistry
from core.plugins.storage import private_data_handle
from core.runtime.environments import EnvironmentStore


@pytest.fixture
def manager(tmp_path, monkeypatch):
    from core.plugins import management
    monkeypatch.setattr('common.state_dir.shared_root', lambda: tmp_path / 'state')
    package = tmp_path / 'source'
    shutil.copytree('samples/plugins/alpha', package)
    (package / 'backend/main.py').write_text('''import sys
def handle(method, params):
    return {'status': 'succeeded', 'sideEffects': 'none', 'value': sys.prefix}
''')
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'coreApi': '1.0.0', 'plugins': [{
        'pluginId': 'stella.sample.alpha', 'hostApi': '^1.0.0', 'version': '1.0.0',
        'path': str(package), 'sha256': _sha256_tree(package),
    }]}))
    store = ManagementStore(tmp_path / 'packages')
    monkeypatch.setattr(management, 'default_store', lambda: store)
    service = PluginManager(store, catalog_path=catalog)
    service.install('stella.sample.alpha', user_id='alice')
    return service


def start(manager, user='alice'):
    current = manager.packages.current_package('stella.sample.alpha')
    session = PluginSession(Path(current['path']), source_label='installed', user_id=user, registry=PluginRegistry())
    session.start()
    return session


def test_rebuild_selects_new_environment_for_next_real_worker_and_preserves_owner_state(manager):
    from common.runtime_identity import identity_scope
    first = start(manager)
    manager.adopt_current('stella.sample.alpha', user_id='bob')
    other = start(manager, 'bob')
    private = Path(private_data_handle('stella.sample.alpha', 'alice', ensure=True)['dataDir'])
    (private / 'kept').write_text('ACCOUNT_AND_BUSINESS_DATA')
    manager.management.save_settings('stella.sample.alpha', 'alice', 'preferences', 'general',
                                     {'notes': 'KEEP_CONFIG'}, expected_revision=1)
    old = first.environment
    current = manager.packages.current_package('stella.sample.alpha')
    fresh = None
    try:
        result = manager.rebuild('stella.sample.alpha', user_id='alice')
        assert result['kind'] == 'rebuild' and result['stage'] == 'completed'
        assert first._proc is None and other.is_running
        selected = EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice')
        assert selected['id'] != old['id']
        assert Path(old['path']).is_dir()
        fresh = start(manager)
        assert fresh.environment['id'] == selected['id']
        with identity_scope(user_id='alice'):
            actual = fresh.invoke('ping', {})
        assert actual['status'] == 'succeeded', actual
        assert actual['value'] == str(Path(selected['path']) / 'python')
        assert (private / 'kept').read_text() == 'ACCOUNT_AND_BUSINESS_DATA'
        assert manager.management.settings('stella.sample.alpha', 'alice', 'preferences', 'general')['values'] == {'notes': 'KEEP_CONFIG'}
        assert manager.packages.current_package('stella.sample.alpha') == current
        assert manager.management.instance('stella.sample.alpha', 'alice')['intent'] == 'enabled'
    finally:
        first.dispose()
        other.dispose()
        if fresh:
            fresh.dispose()


@pytest.mark.parametrize('corrupt_pointer', [False, True])
def test_rebuild_failed_worker_validation_keeps_old_running_selection(manager, monkeypatch, corrupt_pointer):
    old = start(manager)
    pointer = Path(old.environment['path']).parent / 'current.json'
    if corrupt_pointer:
        previous = json.loads(pointer.read_text())
        previous.pop('id')
        pointer.write_text(json.dumps(previous))
    retained = pointer.read_bytes()
    prepare = EnvironmentStore.prepare_python
    candidates = []
    def damaged_candidate(store, **values):
        record = prepare(store, **values)
        if values.get('rebuild'):
            python = Path(record['python'])
            python.unlink()
            python.symlink_to('/usr/bin/false')
            candidates.append(record)
        return record
    monkeypatch.setattr(EnvironmentStore, 'prepare_python', damaged_candidate)
    try:
        with pytest.raises(PluginHostError):
            manager.rebuild('stella.sample.alpha', user_id='alice')
        assert old.is_running
        assert pointer.read_bytes() == retained
        if not corrupt_pointer:
            assert EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice') == old.environment
        assert manager.management.unfinished() == []
        assert candidates
        marker = Path(candidates[0]['path']) / 'environment.json'
        assert json.loads(marker.read_text())['state'] == 'failed'
    finally:
        old.dispose()


def test_rebuild_disabled_plugin_does_not_enable_or_start_it(manager):
    manager.set_intent('stella.sample.alpha', 'alice', 'disabled')
    operation = manager.rebuild('stella.sample.alpha', user_id='alice')
    assert operation['stage'] == 'completed'
    assert EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice') is not None
    state = manager.management.instance('stella.sample.alpha', 'alice')
    assert state['intent'] == 'disabled' and state['state'] == 'stopped'


def test_rebuild_can_repair_invalid_selected_metadata(manager):
    from common.runtime_identity import identity_scope
    from core.security.control import RuntimeControl
    old = start(manager)
    environment = old.environment
    old.dispose()
    pointer = Path(environment['path']).parent / 'current.json'
    corrupt = json.loads(pointer.read_text())
    corrupt.pop('id')
    pointer.write_text(json.dumps(corrupt))
    with identity_scope(user_id='alice'):
        inventory = RuntimeControl().inventory()
    row = next(row for row in inventory['runtimes'] if row['id'] == 'stella.sample.alpha')
    assert row['environment_error'] and row['can_rebuild']
    result = manager.rebuild('stella.sample.alpha', user_id='alice')
    assert result['stage'] == 'completed'
    assert EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice')['id'] != environment['id']
    assert Path(environment['path']).exists()


@pytest.mark.parametrize('stage', ['prepared', 'draining', 'selected'])
def test_rebuild_resumes_fixed_candidate_after_interruption(manager, stage):
    class PowerLoss(BaseException):
        pass
    def interrupt(current, operation):
        if current == stage:
            raise PowerLoss()
    with pytest.raises(PowerLoss):
        manager.rebuild('stella.sample.alpha', user_id='alice', checkpoint=interrupt)
    operation = next(op for op in manager.management.unfinished() if op['kind'] == 'rebuild')
    candidate = operation['facts']['environment']
    resumed = PluginManager(ManagementStore(manager.management.root), catalog_path=Path('offline-catalog'))
    result = resumed.resume(operation['operationId'])
    assert result['stage'] == 'completed'
    assert EnvironmentStore().current('plugin', 'stella.sample.alpha', 'alice')['id'] == candidate['id']
    assert resumed.management.runtime_state('stella.sample.alpha', 'alice') == 'stopped'


@pytest.mark.parametrize('resume', [False, True])
def test_rebuild_cannot_reopen_admission_while_another_install_needs_recovery(manager, resume):
    class PowerLoss(BaseException):
        pass
    def interrupt_at(stage):
        def interrupt(current, operation):
            if current == stage:
                raise PowerLoss()
        return interrupt
    original = None
    if resume:
        with pytest.raises(PowerLoss):
            manager.rebuild('stella.sample.alpha', user_id='alice', checkpoint=interrupt_at('prepared'))
        original = next(op for op in manager.management.unfinished() if op['kind'] == 'rebuild')
    with pytest.raises(PowerLoss):
        manager.install('stella.sample.alpha', user_id='alice', checkpoint=interrupt_at('selected' if resume else 'prepared'))
    before = manager.management.runtime_state('stella.sample.alpha', 'alice')
    with pytest.raises(PermissionError, match='pending|recovery'):
        if resume:
            manager.resume(original['operationId'])
        else:
            manager.rebuild('stella.sample.alpha', user_id='alice')
    assert manager.management.runtime_state('stella.sample.alpha', 'alice') == before

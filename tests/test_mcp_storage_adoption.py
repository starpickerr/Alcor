"""Owner recovery copies retained MCP private data and preserves its source."""
import json
from pathlib import Path

import pytest

from common.runtime_identity import identity_scope
from core.mcp.lifecycle import McpLifecycle
from core.security.mcp_runtime import McpStorageMigrationRequired, private_storage
from tests.test_mcp_runtime_storage import setup as runtime_setup, legacy_root, info

setup = runtime_setup


@pytest.fixture
def owner(setup, monkeypatch):
    from agent.tools.tool_manager import ToolManager
    config, start = setup
    with identity_scope(user_id='alice'):
        values = config('https://service.invalid')
        manager = ToolManager()
        monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [values])
        yield McpLifecycle(manager), values, start
        manager.shutdown_mcp()


def legacy_data():
    home = legacy_root() / '.home'
    home.mkdir(parents=True)
    marker = home / 'private.txt'
    marker.write_text('RETAINED_OWNER_DATA')
    return marker


def adopt(lifecycle, values, preview=None):
    preview = preview or lifecycle.storage_preview(values['name'])
    lifecycle.adopt_storage(values['name'], preview['token'], preview['sources'][0]['id'])
    return preview


def test_owner_confirms_original_data_then_real_worker_restarts_with_it(owner):
    lifecycle, values, start = owner
    marker = legacy_data()
    with pytest.raises(McpStorageMigrationRequired):
        private_storage(values, 'alice')
    preview = lifecycle.storage_preview(values['name'])
    assert preview['sources'][0]['path'] == str(legacy_root())
    assert marker.read_text() == 'RETAINED_OWNER_DATA'
    # Cancelling the confirmation performs no write and remains retryable.
    assert lifecycle.storage_preview(values['name']) == preview
    adopt(lifecycle, values, preview)
    first = start(values)
    assert info(first)['value'] == 'RETAINED_OWNER_DATA'
    first.shutdown()
    assert info(start(values))['value'] == 'RETAINED_OWNER_DATA'
    assert marker.read_text() == 'RETAINED_OWNER_DATA'


def test_source_update_requires_new_confirmation_and_stops_old_process(owner):
    lifecycle, values, start = owner
    old = start(values)
    original = info(old)
    marker = Path(original['home']) / 'private.txt'
    marker.write_text('KEEP_AFTER_UPDATE')
    script = Path(values['args'][0])
    script.write_text(script.read_text() + '\n# changed implementation\n')
    preview = adopt(lifecycle, values)
    assert preview['sources']
    assert old._proc is None
    assert info(start(values))['value'] == 'KEEP_AFTER_UPDATE'
    assert marker.read_text() == 'KEEP_AFTER_UPDATE'


@pytest.mark.parametrize('change', ['source', 'root', 'configuration', 'user', 'agent'])
def test_confirmation_is_bound_to_source_root_current_configuration_and_owner(owner, change):
    lifecycle, values, _ = owner
    marker = legacy_data()
    preview = lifecycle.storage_preview(values['name'])
    if change == 'source':
        script = Path(values['args'][0])
        script.write_text(script.read_text() + '\n# new source\n')
    elif change == 'root':
        root = legacy_root()
        root.rename(root.with_name(root.name + '.retained'))
        marker = legacy_data()
    elif change == 'configuration':
        values['env']['SERVICE_ORIGIN'] = 'https://another.invalid'
    with identity_scope(user_id='bob' if change == 'user' else 'alice',
                        session_id='agent-session' if change == 'agent' else None):
        with pytest.raises((PermissionError, ValueError)):
            adopt(lifecycle, values, preview)
    assert marker.read_text() == 'RETAINED_OWNER_DATA'


def test_failed_publication_retains_data_and_can_retry(owner, monkeypatch):
    lifecycle, values, start = owner
    marker = legacy_data()
    preview = lifecycle.storage_preview(values['name'])
    original = lifecycle.store.save_settings
    def fail(*args, **kwargs):
        raise OSError('synthetic publication failure')
    monkeypatch.setattr(lifecycle.store, 'save_settings', fail)
    with pytest.raises(OSError):
        adopt(lifecycle, values, preview)
    assert marker.read_text() == 'RETAINED_OWNER_DATA'
    monkeypatch.setattr(lifecycle.store, 'save_settings', original)
    adopt(lifecycle, values, preview)
    assert info(start(values))['value'] == 'RETAINED_OWNER_DATA'


def test_original_runtime_api_shows_source_and_accepts_only_owner_confirmation(owner, monkeypatch):
    import channel.desktop.desktop_channel as desktop
    from core.security.control import RuntimeControl
    lifecycle, values, start = owner
    legacy_data()
    row = next(row for row in RuntimeControl().inventory()['runtimes'] if row['id'] == values['name'])
    preview = row['storage_migration']
    body = {'action': 'adopt-storage', 'kind': 'mcp', 'id': values['name'],
            'token': preview['token'], 'sourceId': preview['sources'][0]['id']}
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(body).encode())
    with identity_scope(user_id='alice', session_id='agent-session'):
        assert json.loads(desktop.SecurityRuntimeTrustHandler().POST())['status'] == 'error'
    assert json.loads(desktop.SecurityRuntimeTrustHandler().POST())['status'] == 'success'
    assert info(start(values))['value'] == 'RETAINED_OWNER_DATA'


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo'])
def test_retained_links_and_special_files_are_rejected(owner, kind):
    import os
    lifecycle, values, _ = owner
    marker = legacy_data()
    other = marker.with_name('unsafe')
    if kind == 'symlink':
        other.symlink_to(marker)
    elif kind == 'hardlink':
        os.link(marker, other)
    else:
        os.mkfifo(other)
    with pytest.raises(OSError):
        lifecycle.storage_preview(values['name'])
    assert marker.read_text() == 'RETAINED_OWNER_DATA'


def test_multiple_sources_only_copy_the_selected_source(owner):
    from core.security.mcp_runtime import storage_layout
    lifecycle, values, start = owner
    marker = legacy_data()
    _, directory, _, _ = storage_layout(values, 'alice')
    other = directory / ('0' * 64) / '.home'
    other.mkdir(parents=True)
    (other / 'private.txt').write_text('SELECTED_HISTORY')
    preview = lifecycle.storage_preview(values['name'])
    assert len(preview['sources']) == 2
    selected = next(row for row in preview['sources'] if row['path'] == str(other.parent))
    lifecycle.adopt_storage(values['name'], preview['token'], selected['id'])
    assert info(start(values))['value'] == 'SELECTED_HISTORY'
    assert marker.read_text() == 'RETAINED_OWNER_DATA'
    assert (other / 'private.txt').read_text() == 'SELECTED_HISTORY'


def test_source_changes_during_copy_are_rejected_and_retryable(owner, monkeypatch):
    from core.mcp import storage
    lifecycle, values, start = owner
    marker = legacy_data()
    preview = lifecycle.storage_preview(values['name'])
    original = storage._walk
    def changed(path, target=None):
        result = original(path, target)
        if target:
            marker.write_text('NEW_RETAINED_DATA')
        return result
    monkeypatch.setattr(storage, '_walk', changed)
    with pytest.raises(ValueError, match='changed'):
        adopt(lifecycle, values, preview)
    monkeypatch.setattr(storage, '_walk', original)
    adopt(lifecycle, values)
    assert info(start(values))['value'] == 'NEW_RETAINED_DATA'


def test_publication_never_overwrites_a_concurrently_created_target(owner, monkeypatch):
    from core.mcp import storage
    from core.security.mcp_runtime import storage_layout
    lifecycle, values, _ = owner
    marker = legacy_data()
    _, _, target, _ = storage_layout(values, 'alice')
    original = storage._walk
    def occupied(path, destination=None):
        result = original(path, destination)
        if destination:
            target.mkdir()
        return result
    monkeypatch.setattr(storage, '_walk', occupied)
    with pytest.raises(FileExistsError):
        adopt(lifecycle, values)
    assert target.is_dir() and list(target.iterdir()) == []
    assert marker.read_text() == 'RETAINED_OWNER_DATA'


def test_adoption_keeps_another_service_with_the_same_alias_running(owner):
    lifecycle, values, start = owner
    other = start({**values, 'env': {'SERVICE_ORIGIN': 'https://other.invalid'}})
    marker = legacy_data()
    adopt(lifecycle, values)
    assert other._proc is not None and other._proc.poll() is None
    assert marker.read_text() == 'RETAINED_OWNER_DATA'


def test_interruption_after_atomic_publication_is_already_recoverable(owner, monkeypatch):
    from core.mcp import storage
    lifecycle, values, start = owner
    marker = legacy_data()
    original = storage._publish
    def interrupted(source, target):
        original(source, target)
        raise RuntimeError('synthetic interruption after publish')
    monkeypatch.setattr(storage, '_publish', interrupted)
    with pytest.raises(RuntimeError, match='interruption'):
        adopt(lifecycle, values)
    assert lifecycle.storage_preview(values['name']) is None
    assert info(start(values))['value'] == 'RETAINED_OWNER_DATA'
    assert marker.read_text() == 'RETAINED_OWNER_DATA'


def test_unlisted_source_cannot_select_an_arbitrary_directory(owner):
    lifecycle, values, _ = owner
    marker = legacy_data()
    preview = lifecycle.storage_preview(values['name'])
    with pytest.raises(ValueError, match='source'):
        lifecycle.adopt_storage(values['name'], preview['token'], str(marker.parent))
    assert marker.read_text() == 'RETAINED_OWNER_DATA'


def test_replacing_the_approved_target_revokes_the_confirmation(owner):
    lifecycle, values, _ = owner
    marker = legacy_data()
    adopt(lifecycle, values)
    target = private_storage(values, 'alice')
    target.rename(target.with_name('.retained-copy'))
    target.mkdir()
    with pytest.raises(McpStorageMigrationRequired):
        private_storage(values, 'alice')
    assert marker.read_text() == 'RETAINED_OWNER_DATA'

"""Actual trusted UI and OS boundaries raised by the final branch review."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


def probe_storage(handle, runtime, package, code, *, legacy=False):
    from core.plugins.storage import storage_paths
    from core.security.extension_sandbox import prepare_extension
    domain, reads = storage_paths(handle, legacy=legacy)
    plan = prepare_extension([sys.executable, '-c', code], package, runtime, {}, trusted=False,
        read_roots=[Path(sys.executable).resolve().parent, *reads], write_roots=[domain])
    return subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, capture_output=True, text=True, timeout=10)


def read_code(path):
    return f'''from pathlib import Path
try: print(Path({str(path)!r}).read_text())
except OSError: print('denied')
'''


def test_normal_memory_worker_cannot_open_core_conversation_index(tmp_path, monkeypatch):
    from common import state_dir
    from core.plugins.lifecycle import domain_data_handle
    root = tmp_path / 'state'
    index = root / 'memory/long-term/index.db'
    index.parent.mkdir(parents=True)
    index.write_text('CORE_CONVERSATIONS')
    monkeypatch.setattr(state_dir, 'user_root', lambda *args, **kwargs: root)
    monkeypatch.setattr(state_dir, 'memory_dir', lambda **kwargs: root / 'memory')
    monkeypatch.setattr(state_dir, 'memory_file', lambda: root / 'MEMORY.md')
    result = probe_storage(domain_data_handle('stella.memory', 'local-default'), tmp_path / 'runtime',
        Path('stella-plugins-workspace/plugins/memory'), read_code(index))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'denied'


def test_memory_migration_import_never_exposes_original_core_index(tmp_path, monkeypatch):
    from core.plugins.host import PluginSession
    from core.plugins.management import ManagementStore
    from core.plugins.migration import MigrationCoordinator
    legacy = tmp_path / 'legacy'
    index = legacy / 'long-term/index.db'
    index.parent.mkdir(parents=True)
    index.write_text('CORE_CONVERSATIONS')
    (legacy / 'day.md').write_text('USER_MARKDOWN')
    main = tmp_path / 'MEMORY.md'
    main.write_text('USER_MAIN')
    seen = []
    original = PluginSession.migrate
    def inspected(self, **kwargs):
        result = probe_storage(self._bound_data_handle, tmp_path / 'probe-runtime', self.plugin_root,
            read_code(index), legacy=True)
        seen.append(result.stdout.strip())
        return original(self, **kwargs)
    monkeypatch.setattr(PluginSession, 'migrate', inspected)
    target = tmp_path / 'content'
    target.mkdir()
    result = MigrationCoordinator(ManagementStore(tmp_path / 'packages')).run(
        Path('stella-plugins-workspace/plugins/memory'), user_id='local-default',
        data_handle={'namespace': 'stella.memory', 'dataDir': str(target),
                     'legacyMemoryDir': str(legacy), 'legacyMainFile': str(main)})
    assert result['stage'] == 'data_selected'
    assert seen == ['denied', 'denied']
    assert (target / 'memory/day.md').read_text() == 'USER_MARKDOWN'
    assert index.read_text() == 'CORE_CONVERSATIONS'


def test_automation_worker_has_only_current_owner_records_and_no_core_tables(tmp_path, monkeypatch, registered_agent):
    import sqlite3
    from common.runtime_identity import identity_scope
    from agent.scheduler.automation_store import AutomationStore
    from core.plugins.package_ops import _sha256_tree
    from core.security.runtime_trust import RuntimeTrustStore
    from core.services import automation_gateway as gateway
    agent = registered_agent('review-agent')
    db = Path(agent.workspace) / 'scheduler/automation.db'
    store = AutomationStore(db.with_name('tasks.json'), agent.id)
    for user in ('alice', 'bob'):
        with identity_scope(user_id=user, agent_id=agent.id):
            store.add_task({'id': user + '-task', 'name': user, 'enabled': True,
                'schedule': {'type': 'interval', 'seconds': 3600},
                'action': {'type': 'send_message', 'content': 'synthetic'}})
    with sqlite3.connect(db) as connection:
        connection.execute("INSERT INTO metadata VALUES ('core_secret','CORE_ONLY')")
    root = Path('stella-plugins-workspace/plugins/automation')
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN', '1')
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN_ROOT', str(root.resolve()))
    RuntimeTrustStore().set(user_id='alice', kind='plugin', identifier='stella.automation',
        digest=_sha256_tree(root), trusted=False)
    try:
        with identity_scope(user_id='alice', agent_id=agent.id):
            result = gateway.invoke_automation('list_tasks', {}, db_path=db, agent_id=agent.id, owner_user_id='alice')
            assert [row['id'] for row in result['value']] == ['alice-task']
            worker = gateway._SESSION
            script = f'''import sqlite3,json
result={{}}
try:
    with sqlite3.connect({str(db)!r}) as db:
        result['bob'] = db.execute("SELECT owner_user_id FROM tasks WHERE task_id='bob-task'").fetchone()
        db.execute("DELETE FROM tasks WHERE task_id='bob-task'")
except sqlite3.Error: result['original'] = 'denied'
with sqlite3.connect({str(worker._bound_data_handle['dbPath'])!r}) as db:
    result['visible_users'] = [r[0] for r in db.execute('SELECT DISTINCT owner_user_id FROM tasks')]
    result['tables'] = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print(json.dumps(result))
'''
            probe = probe_storage(worker._bound_data_handle, Path(worker._storage_directory), root, script)
            assert probe.returncode == 0, probe.stderr
            observed = json.loads(probe.stdout)
            assert observed.get('original') == 'denied', observed
            assert observed['visible_users'] == ['alice']
            assert not {'job_runs', 'job_deliveries', 'metadata'} & set(observed['tables'])
            connect = sqlite3.connect
            def portable_connect(database, *args, **kwargs):
                if '/dev/fd/' in str(database):
                    raise sqlite3.OperationalError('Unix descriptor paths are unavailable')
                return connect(database, *args, **kwargs)
            monkeypatch.setattr(sqlite3, 'connect', portable_connect)
            changed = gateway.invoke_automation('update_task', {'task_id': 'alice-task', 'patch': {'name': 'updated'}},
                db_path=db, agent_id=agent.id, owner_user_id='alice')
            assert changed['status'] == 'succeeded', changed
        with identity_scope(user_id='bob', agent_id=agent.id):
            assert store.get_task('bob-task')['name'] == 'bob'
        with identity_scope(user_id='alice', agent_id=agent.id):
            assert store.get_task('alice-task')['name'] == 'updated'
        with sqlite3.connect(db) as connection:
            assert connection.execute("SELECT value FROM metadata WHERE key='core_secret'").fetchone()[0] == 'CORE_ONLY'
    finally:
        gateway.shutdown_automation_session()


@pytest.mark.parametrize('action', ['source', 'sync', 'cleanup'])
def test_authenticated_native_data_operation_is_not_limited_by_agent_readonly(tmp_path, monkeypatch, action):
    from tests.test_data_api import call
    from unittest.mock import patch
    from channel.desktop import desktop_channel as desktop
    from core.data.api import DataController
    from core.data.service import DataService, ProviderRegistry
    from tests.test_data_service import Provider
    from core.security.run_policy import save_run_policy
    registry = ProviderRegistry()
    class GuardedProvider(Provider):
        def authorize(self, source, operation):
            from agent.permission.data_access import authorize
            authorize(operation, source['id'])
            super().authorize(source, operation)
    provider = GuardedProvider()
    registry.register('messages', provider)
    service = DataService(tmp_path / 'data.db', registry)
    service.discover()
    monkeypatch.setattr('core.data.api.current_controller', lambda: DataController(service))
    save_run_policy('template', 'default', {'filesystem_mode': 'read-only'})
    bodies = {'source': {'action': 'source', 'source_id': 'messages:a', 'enabled': False},
              'sync': {'action': 'sync', 'source_id': 'messages:a', 'options': {}},
              'cleanup': {'action': 'cleanup', 'source_id': 'messages:a', 'all_records': True}}
    with patch.object(desktop, '_require_auth') as auth, patch.object(desktop.web, 'header'):
        result = call(desktop.DataCommandHandler, body=bodies[action])
    assert auth.called
    assert result['status'] == 'success', result


@pytest.mark.parametrize('scope', [{'session_id': 'readonly-data'}, {'task_id': 'readonly-data'}, {'run_id': 'child-data'}])
def test_agent_context_cannot_borrow_native_data_bypass(scope):
    from common.runtime_identity import identity_scope
    from core.data.native import native_data_access
    from core.security.run_policy import save_run_policy
    from agent.permission.data_access import authorize
    save_run_policy('template', 'default', {'filesystem_mode': 'read-only'})
    save_run_policy('session', 'readonly-data', {'filesystem_mode': 'read-only'})
    with identity_scope(**scope), native_data_access(), pytest.raises(PermissionError):
        authorize('configure', 'synthetic-source')


def test_native_readonly_still_obeys_resource_revocation(tmp_path, monkeypatch):
    from tests.test_data_api import call
    from unittest.mock import patch
    from channel.desktop import desktop_channel as desktop
    from core.data.api import DataController
    from core.data.service import DataService, ProviderRegistry
    from tests.test_data_service import Provider
    from core.security.run_policy import save_run_policy
    class GuardedProvider(Provider):
        def authorize(self, source, operation):
            from agent.permission.data_access import authorize
            authorize(operation, source['id'])
            super().authorize(source, operation)
    provider = GuardedProvider()
    registry = ProviderRegistry()
    registry.register('messages', provider)
    service = DataService(tmp_path / 'data.db', registry)
    service.discover()
    provider.allowed = False
    monkeypatch.setattr('core.data.api.current_controller', lambda: DataController(service))
    save_run_policy('template', 'default', {'filesystem_mode': 'read-only'})
    with patch.object(desktop, '_require_auth'), patch.object(desktop.web, 'header'):
        result = call(desktop.DataCommandHandler, body={'action': 'sync', 'source_id': 'messages:a', 'options': {}})
    assert result['status'] == 'error'
    assert 'revoked' in result['message']


def test_cancel_between_worker_result_and_commit_keeps_core_task_unchanged(tmp_path, monkeypatch):
    import threading
    from core.services import automation_gateway as gateway
    from core.plugins.cancellation import cancellation_scope
    from agent.protocol.cancel import AgentCancelledError
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN', '1')
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN_ROOT', str(Path('stella-plugins-workspace/plugins/automation').resolve()))
    db = tmp_path / 'core' / 'automation.db'
    task = {'id': 'one', 'name': 'before', 'enabled': True,
        'schedule': {'type': 'interval', 'seconds': 3600}, 'action': {'type': 'send_message', 'content': 'test'}}
    try:
        assert gateway.invoke_automation('create_task', {'task': task}, db_path=db, agent_id='default')['status'] == 'succeeded'
        session = gateway._SESSION
        original = session._rpc
        cancelled = threading.Event()
        def cancel_after_write(method, params, **kwargs):
            result = original(method, params, **kwargs)
            if method == 'plugin.invoke' and params.get('method') == 'update_task':
                cancelled.set()
            return result
        monkeypatch.setattr(session, '_rpc', cancel_after_write)
        try:
            with cancellation_scope(cancelled):
                gateway.invoke_automation('update_task', {'task_id': 'one', 'patch': {'name': 'after'}}, db_path=db, agent_id='default')
        except AgentCancelledError:
            pass
        assert gateway.invoke_automation('get_task', {'task_id': 'one'}, db_path=db, agent_id='default')['value']['name'] == 'before'
    finally:
        gateway.shutdown_automation_session()


@pytest.mark.parametrize('tampering', ['owner', 'unrelated', 'delete', 'symlink'])
def test_worker_cannot_publish_foreign_or_unrequested_task_changes(tmp_path, monkeypatch, tampering):
    import sqlite3
    from core.services import automation_gateway as gateway
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN', '1')
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN_ROOT', str(Path('stella-plugins-workspace/plugins/automation').resolve()))
    db = tmp_path / 'core' / 'automation.db'
    try:
        for task_id in ('one', 'two'):
            task = {'id': task_id, 'name': 'before', 'enabled': True,
                'schedule': {'type': 'interval', 'seconds': 3600}, 'action': {'type': 'send_message', 'content': 'test'}}
            assert gateway.invoke_automation('create_task', {'task': task}, db_path=db, agent_id='default')['status'] == 'succeeded'
        session = gateway._SESSION
        original = session._rpc
        def tamper_after_write(method, params, **kwargs):
            result = original(method, params, **kwargs)
            if method == 'plugin.invoke' and params.get('method') == 'update_task':
                with sqlite3.connect(session._bound_data_handle['dbPath']) as copy:
                    if tampering == 'owner':
                        copy.execute("UPDATE tasks SET owner_user_id='bob' WHERE task_id='one'")
                    elif tampering == 'unrelated':
                        copy.execute("DELETE FROM tasks WHERE task_id='two'")
                    elif tampering == 'delete':
                        copy.execute("DELETE FROM tasks WHERE task_id='one'")
                if tampering == 'symlink':
                    private = Path(session._bound_data_handle['dbPath'])
                    private.unlink()
                    private.symlink_to(db)
            return result
        monkeypatch.setattr(session, '_rpc', tamper_after_write)
        result = gateway.invoke_automation('update_task', {'task_id': 'one', 'patch': {'name': 'after'}}, db_path=db, agent_id='default')
        assert result['status'] == 'failed' and result['sideEffects'] == 'none'
        with sqlite3.connect(db) as connection:
            rows = [json.loads(row[0]) for row in connection.execute('SELECT data FROM tasks ORDER BY task_id')]
        assert [row['name'] for row in rows] == ['before', 'before']
    finally:
        gateway.shutdown_automation_session()


@pytest.mark.parametrize('legacy_port', [False, True])
def test_ui_rooted_mcp_writes_skip_agent_mode_but_preserve_connection_deny(legacy_port):
    from core.data.native import native_data_access
    from core.plugins.caller import caller_origin_scope, consumer_plugin_scope
    from core.security.service import SecurityService
    from core.security.capabilities import CapabilityDescriptor
    from core.security.run_policy import save_run_policy
    from agent.permission.gateway import Capability, register_tool
    save_run_policy('template', 'default', {'filesystem_mode': 'read-only',
        'connections': {'mcp:blocked-native': {'allowed': False, 'scopes': []}}})
    descriptor = CapabilityDescriptor('mcp:native-fixture/save', 'mcp', 'write')
    register_tool('native_mcp_fixture', Capability.EXTERNAL, descriptor=descriptor)
    blocked = CapabilityDescriptor('mcp:blocked-native/save', 'mcp', 'write')
    register_tool('blocked_native_fixture', Capability.EXTERNAL, descriptor=blocked, connection='mcp:blocked-native')
    with (caller_origin_scope('desktop') if legacy_port else native_data_access()), consumer_plugin_scope('stella.briefing'):
        assert SecurityService.authorize_tool('native_mcp_fixture', {}, capability_descriptor=descriptor, noninteractive=True) is None
        assert SecurityService.authorize_tool('blocked_native_fixture', {}, capability_descriptor=blocked, noninteractive=True)


@pytest.mark.parametrize('scope', [{'session_id': 's'}, {'task_id': 't'}, {'run_id': 'r'}, {'execution_session_id': 'e'}])
def test_legacy_ui_origin_cannot_override_agent_execution(scope):
    from common.runtime_identity import identity_scope
    from core.plugins.caller import caller_origin_scope
    from core.security.service import SecurityService
    with identity_scope(**scope), caller_origin_scope('desktop'):
        assert not SecurityService.native_user_operation()


@pytest.mark.parametrize('source', ['agent', 'scheduler', 'system'])
def test_non_ui_legacy_origin_cannot_skip_agent_mode(source):
    from core.plugins.caller import caller_origin_scope
    from core.security.service import SecurityService
    with caller_origin_scope(source):
        assert not SecurityService.native_user_operation()


def test_automation_migration_rejects_raw_core_database(tmp_path):
    from core.plugins.migration import MigrationCoordinator
    from core.plugins.management import ManagementStore
    from agent.scheduler.automation_store import AutomationStore
    source = tmp_path / 'scheduler' / 'automation.db'
    AutomationStore(source.with_name('tasks.json'), 'default')
    before = source.read_bytes()
    with pytest.raises(PermissionError, match='broker'):
        MigrationCoordinator(ManagementStore(tmp_path / 'packages')).run(
            Path('stella-plugins-workspace/plugins/automation'), user_id='local-default',
            data_handle={'namespace': 'stella.automation', 'dbPath': str(source)})
    assert source.read_bytes() == before


def test_broker_commit_preserves_concurrent_core_updates(tmp_path, monkeypatch, registered_agent):
    import threading
    from common.runtime_identity import identity_scope
    from agent.scheduler.automation_store import AutomationStore
    from core.services import automation_gateway as gateway
    agent = registered_agent('concurrent-agent')
    store = AutomationStore(Path(agent.workspace) / 'scheduler/tasks.json', agent.id)
    db = Path(store.store_path)
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN', '1')
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN_ROOT', str(Path('stella-plugins-workspace/plugins/automation').resolve()))
    entered, release, core_done = threading.Event(), threading.Event(), threading.Event()
    errors = []
    for user in ('alice', 'bob'):
        with identity_scope(user_id=user, agent_id=agent.id):
            store.add_task({'id': user, 'name': 'before', 'enabled': True,
                'schedule': {'type': 'interval', 'seconds': 3600},
                'action': {'type': 'send_message', 'content': 'synthetic'}})
    with identity_scope(user_id='alice', agent_id=agent.id):
        gateway.invoke_automation('list_tasks', {}, db_path=db, agent_id=agent.id, owner_user_id='alice')
    session = gateway._SESSION
    original = session._rpc
    def pause(method, params, **kwargs):
        result = original(method, params, **kwargs)
        if method == 'plugin.invoke' and params.get('method') == 'update_task':
            entered.set()
            assert release.wait(5)
        return result
    monkeypatch.setattr(session, '_rpc', pause)
    def alice():
        try:
            with identity_scope(user_id='alice', agent_id=agent.id):
                result = gateway.invoke_automation('update_task', {'task_id': 'alice', 'patch': {'name': 'plugin updated'}},
                    db_path=db, agent_id=agent.id, owner_user_id='alice')
                assert result['status'] == 'succeeded', result
        except BaseException as exc:
            errors.append(exc)
    def bob():
        try:
            with identity_scope(user_id='bob', agent_id=agent.id):
                store.update_task('bob', {'name': 'core updated'})
        except BaseException as exc:
            errors.append(exc)
        finally:
            core_done.set()
    threads = [threading.Thread(target=alice), threading.Thread(target=bob)]
    try:
        threads[0].start()
        assert entered.wait(5)
        threads[1].start()
        assert not core_done.wait(.05)
        release.set()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        assert not errors, errors
        with identity_scope(user_id='alice', agent_id=agent.id):
            assert store.get_task('alice')['name'] == 'plugin updated'
        with identity_scope(user_id='bob', agent_id=agent.id):
            assert store.get_task('bob')['name'] == 'core updated'
    finally:
        release.set()
        for thread in threads:
            if thread.ident:
                thread.join(5)
        gateway.shutdown_automation_session()

"""Scheduled runs own policy snapshots and cannot borrow notifier privileges."""
import pytest

from common.runtime_identity import identity_scope
from core.security.run_policy import resolve_run_policy, save_run_policy


def task(task_id='daily', **extra):
    return {'id': task_id, 'name': 'daily', 'enabled': True,
            'schedule': {'type': 'interval', 'seconds': 3600},
            'action': {'type': 'tool_call', 'tool_name': 'todos', 'tool_params': {'action': 'create', 'title': 'daily'}}, **extra}


@pytest.fixture
def scheduler(tmp_path):
    from agent.scheduler.store import TaskStore
    from agent.scheduler.mutations import SchedulerMutations
    return SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))


def test_new_task_has_read_only_independent_policy(scheduler):
    save_run_policy('template', 'default', {'filesystem_mode': 'full-access', 'capabilities': {}})
    created = scheduler.create(task())
    assert created['run_policy']['filesystem_mode'] == 'read-only'
    save_run_policy('template', 'default', {'filesystem_mode': 'workspace-write'})
    assert scheduler.get('daily')['run_policy']['filesystem_mode'] == 'read-only'


def test_trusted_task_business_write_is_independent_of_filesystem(scheduler):
    from agent.runtime.tool_invocation import permission_denial, scheduled_permission_scope
    created = scheduler.create(task(run_policy={'filesystem_mode': 'workspace-write',
        'capabilities': {}}))
    save_run_policy('session', 'notification', {'filesystem_mode': 'full-access', 'capabilities': {}})
    with identity_scope(task_id='daily', session_id='notification'), scheduled_permission_scope(created):
        assert permission_denial('todos', {'action': 'create'}, noninteractive=True) is None
        assert permission_denial('write', {'path': '/outside/blocked.txt', 'content': 'blocked'}, noninteractive=True)
        assert resolve_run_policy()['filesystem_mode'] == 'workspace-write'


def test_agent_cannot_escalate_task_even_with_forged_user_origin(scheduler):
    save_run_policy('session', 'parent', {'filesystem_mode': 'read-only',
        'capabilities': {'plugin:stella.tasks': {'read': 'allow'}}})
    with identity_scope(session_id='parent'):
        with pytest.raises((ValueError, PermissionError)):
            scheduler.create(task(run_policy={'filesystem_mode': 'full-access',
                'capabilities': {'plugin:stella.tasks': {'write': 'allow'}}}), origin='user')
        assert scheduler.store.get_task('daily') is None


def test_agent_task_creation_cannot_delegate_a_wider_mode(scheduler):
    save_run_policy('session', 'parent', {'filesystem_mode': 'workspace-write'})
    with identity_scope(session_id='parent'):
        created = scheduler.create(task(run_policy={'filesystem_mode': 'workspace-write'}), origin='agent')
        assert created['run_policy']['filesystem_mode'] == 'workspace-write'
        with pytest.raises((ValueError, PermissionError)):
            scheduler.update('daily', {'run_policy': {'filesystem_mode': 'full-access'}}, origin='agent')


def test_background_missing_sensitive_preauthorization_fails_without_pending(scheduler):
    from agent.permission.gateway import SecurityGateway
    from agent.runtime.tool_invocation import permission_denial, scheduled_permission_scope
    created = scheduler.create(task(run_policy={'filesystem_mode': 'read-only',
        'capabilities': {'plugin:stella.tasks': {'read': 'allow', 'write': 'allow'}}}))
    with identity_scope(task_id='daily', session_id='notification'), scheduled_permission_scope(created):
        before = len(SecurityGateway.pending())
        denial = permission_denial('todos', {'action': 'delete', 'todo_id': 'one'}, noninteractive=True)
        assert denial and 'denied' in denial.lower()
        assert len(SecurityGateway.pending()) == before


def test_child_permission_ceiling_includes_mode():
    from agent.runtime.tool_invocation import permission_denial, permission_limit_scope, _current_authorization
    save_run_policy('session', 'child', {'filesystem_mode': 'full-access', 'capabilities': {}})
    with identity_scope(session_id='child'):
        _, context, _ = _current_authorization(None, None, 'child', None, False)
        ceiling = {**context['policy'], 'filesystem_mode': 'read-only'}
        with permission_limit_scope(ceiling, context['scope'].write_roots):
            assert permission_denial('todos', {'action': 'list'}) is None
            assert permission_denial('todos', {'action': 'create'}, noninteractive=True)


def test_failed_task_insert_cannot_change_existing_task_permissions(scheduler):
    first = scheduler.create(task(run_policy={'filesystem_mode': 'read-only', 'capabilities': {}}))
    with pytest.raises(ValueError):
        scheduler.create(task(run_policy={'filesystem_mode': 'full-access', 'capabilities': {}}))
    assert scheduler.get('daily')['run_policy'] == first['run_policy']


def test_real_automation_worker_uses_the_same_policy_boundary(tmp_path, monkeypatch):
    from agent.scheduler.automation_store import AutomationStore
    from core.services.automation_facade import AutomationPluginFacade
    from core.services.automation_gateway import shutdown_automation_session
    from pathlib import Path
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN', '1')
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN_ROOT', str(Path('stella-plugins-workspace/plugins/automation').resolve()))
    facade = AutomationPluginFacade(AutomationStore(str(tmp_path / 'tasks.json'), 'default'))
    try:
        created = facade.create(task(run_policy={'filesystem_mode': 'read-only',
            'capabilities': {'plugin:stella.tasks': {'write': 'allow'}}}))
        assert created['run_policy']['filesystem_mode'] == 'read-only'
        assert facade.get('daily')['run_policy']['capabilities']['plugin:stella.tasks']['write'] == 'allow'
        changed = facade.update('daily', {'run_policy': {'filesystem_mode': 'workspace-write',
            'policy_revision': created['run_policy']['policy_revision']}})
        assert changed['run_policy']['filesystem_mode'] == 'workspace-write'
        assert changed['run_policy']['policy_revision'] > created['run_policy']['policy_revision']
        save_run_policy('session', 'worker-parent', {'filesystem_mode': 'read-only',
            'capabilities': {'plugin:stella.automation': {'read': 'allow', 'write': 'allow'}}})
        with identity_scope(session_id='worker-parent'), pytest.raises(PermissionError):
            facade.create(task('forged', run_policy={'filesystem_mode': 'full-access', 'capabilities': {}}), origin='user')
        assert facade.store.get_task('forged') is None
    finally:
        shutdown_automation_session()


def test_policy_commit_interruption_is_fail_closed(scheduler, monkeypatch):
    from core.security.task_policy import TaskPolicyChange
    monkeypatch.setattr(TaskPolicyChange, 'apply', lambda self: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError):
        scheduler.create(task(run_policy={'filesystem_mode': 'full-access', 'capabilities': {}}))
    stored = scheduler.get('daily')
    assert stored['run_policy']['migration_status'] == 'needs_confirmation'
    assert stored['run_policy']['filesystem_mode'] == 'read-only'


def test_child_task_creation_keeps_captured_ceiling_through_real_worker(tmp_path, monkeypatch):
    from pathlib import Path
    from agent.scheduler.automation_store import AutomationStore
    from core.services.automation_facade import AutomationPluginFacade
    from core.services.automation_gateway import shutdown_automation_session
    from agent.runtime.tool_invocation import permission_limit_scope, _current_authorization
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN', '1')
    monkeypatch.setenv('STELLA_AUTOMATION_PLUGIN_ROOT', str(Path('stella-plugins-workspace/plugins/automation').resolve()))
    facade = AutomationPluginFacade(AutomationStore(str(tmp_path / 'tasks.json'), 'default'))
    save_run_policy('session', 'captured-parent', {'filesystem_mode': 'workspace-write',
        'capabilities': {'plugin:stella.automation': {'read': 'allow', 'write': 'allow'}}})
    try:
        with identity_scope(session_id='captured-parent'):
            _, context, _ = _current_authorization(None, None, 'captured-parent', None, False)
            with permission_limit_scope(context['policy'], context['scope'].write_roots):
                save_run_policy('session', 'captured-parent', {'filesystem_mode': 'full-access'})
                with pytest.raises(PermissionError, match='exceeds'):
                    facade.create(task('child-task', run_policy={'filesystem_mode': 'full-access', 'capabilities': {}}), origin='agent')
                assert facade.store.get_task('child-task') is None
    finally:
        shutdown_automation_session()


def test_legacy_capability_field_cannot_override_delegating_mode(scheduler):
    save_run_policy('session', 'denied-file-parent', {'filesystem_mode': 'workspace-write',
        'capabilities': {'builtin:write': {'write': 'deny'}}})
    with identity_scope(session_id='denied-file-parent'):
        created = scheduler.create(task(run_policy={'filesystem_mode': 'workspace-write'}), origin='agent')
        assert created['run_policy']['filesystem_mode'] == 'workspace-write'


def test_delegated_task_preserves_legacy_restrictions(scheduler):
    save_run_policy('session', 'legacy-parent', {'filesystem_mode': 'workspace-write', 'capabilities': {}},
        legacy_rules={'network': 'deny'})
    with identity_scope(session_id='legacy-parent'):
        created = scheduler.create(task(run_policy={'filesystem_mode': 'workspace-write', 'capabilities': {}}), origin='agent')
    assert created['run_policy']['legacy_restrictions']['network'] == 'deny'


def test_owner_can_repair_missing_task_policy_with_cas(scheduler, monkeypatch):
    from core.security.task_policy import TaskPolicyChange, initialize_scheduled_policy
    from core.security.control import PermissionControl
    from core.security.migration import ensure_migrated
    ensure_migrated()
    with monkeypatch.context() as fault:
        fault.setattr(TaskPolicyChange, 'apply', lambda self: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError):
            scheduler.create(task(run_policy={'filesystem_mode': 'full-access', 'capabilities': {}}))
    control = PermissionControl(task_service=lambda agent: scheduler)
    revision = control.read('task', 'daily')['policy']['policy_revision']
    assert revision == 0
    patch = {'policy_revision': revision, 'filesystem_mode': 'read-only'}
    with identity_scope(session_id='model'), pytest.raises(PermissionError):
        control.save('task', 'daily', patch)
    saved = control.save('task', 'daily', patch)
    assert saved['migration_status'] == 'complete'
    with pytest.raises(ValueError, match='revision'):
        control.save('task', 'daily', patch)
    with identity_scope(task_id='daily'):
        assert initialize_scheduled_policy(scheduler.get('daily'))['migration_status'] == 'complete'


def test_owner_can_confirm_untranslatable_task_policy(scheduler):
    from core.security.control import PermissionControl
    from core.security.policy_store import RunPolicyStore
    from core.security.run_policy import subject_key
    from core.security.migration import ensure_migrated
    ensure_migrated()
    scheduler.create(task())
    RunPolicyStore().update(subject_key('task', 'daily'), lambda current: {**current,
        'migration_status': 'needs_confirmation', 'legacy_restrictions': {'network': 'deny'}})
    control = PermissionControl(task_service=lambda agent: scheduler)
    policy = control.read('task', 'daily')['policy']
    saved = control.save('task', 'daily', {**policy, 'filesystem_mode': 'read-only'})
    assert saved['migration_status'] == 'complete'
    assert scheduler.get('daily')['run_policy']['legacy_restrictions'] == {}


def test_delegated_path_grant_survives_captured_ceiling(scheduler, tmp_path):
    from agent.runtime.tool_invocation import permission_limit_scope, _current_authorization
    root = str(tmp_path / 'granted')
    save_run_policy('session', 'path-parent', {'filesystem_mode': 'workspace-write', 'capabilities': {}, 'extra_roots': [root]})
    with identity_scope(session_id='path-parent'):
        _, context, _ = _current_authorization(None, None, 'path-parent', None, False)
        with permission_limit_scope(context['policy'], context['scope'].write_roots):
            created = scheduler.create(task(run_policy={'filesystem_mode': 'workspace-write', 'capabilities': {}, 'extra_roots': [root]}), origin='agent')
            assert created['run_policy']['extra_roots'] == [root]
            with pytest.raises(PermissionError):
                scheduler.create(task('outside', run_policy={'filesystem_mode': 'workspace-write', 'capabilities': {},
                    'extra_roots': [str(tmp_path / 'outside')]}), origin='agent')


def test_model_cannot_repair_missing_policy_through_task_mutation(scheduler, monkeypatch):
    from core.security.task_policy import TaskPolicyChange
    with monkeypatch.context() as fault:
        fault.setattr(TaskPolicyChange, 'apply', lambda self: (_ for _ in ()).throw(OSError('disk full')))
        with pytest.raises(OSError):
            scheduler.create(task())
    save_run_policy('session', 'model-repair', {'filesystem_mode': 'read-only', 'capabilities': {}})
    with identity_scope(session_id='model-repair'), pytest.raises(PermissionError):
        scheduler.update('daily', {'run_policy': {'filesystem_mode': 'read-only', 'capabilities': {}, 'policy_revision': 0}}, origin='agent')
    assert scheduler.get('daily')['run_policy']['policy_revision'] == 0


def test_task_policy_edits_only_apply_to_next_execution(scheduler):
    from agent.runtime.tool_invocation import scheduled_permission_scope
    created = scheduler.create(task(run_policy={'filesystem_mode': 'read-only'}))
    with identity_scope(task_id='daily'), scheduled_permission_scope(created):
        save_run_policy('task', 'daily', {'filesystem_mode': 'full-access'})
        assert resolve_run_policy()['filesystem_mode'] == 'read-only'
    with identity_scope(task_id='daily'), scheduled_permission_scope(created):
        assert resolve_run_policy()['filesystem_mode'] == 'full-access'


def test_revoking_task_still_stops_an_active_execution(scheduler):
    from agent.runtime.tool_invocation import scheduled_permission_scope
    from core.security.task_policy import revoke_task_policy
    created = scheduler.create(task(run_policy={'filesystem_mode': 'full-access'}))
    with identity_scope(task_id='daily'), scheduled_permission_scope(created):
        revoke_task_policy('daily')
        with pytest.raises(PermissionError, match='revoked'):
            resolve_run_policy()

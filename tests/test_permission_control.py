"""Native controls share subject policies, revisions and owner-only authorization."""
import pytest

from common.runtime_identity import identity_scope


def test_native_control_updates_one_subject_and_rejects_stale_revision():
    from core.security.control import PermissionControl
    control = PermissionControl()
    one = control.read('session', 'control-one')
    policy = one['policy']
    saved = control.save('session', 'control-one', {'filesystem_mode': 'read-only',
        'capabilities': {'plugin:stella.tasks': {'read': 'allow', 'write': 'allow'}},
        'policy_revision': policy['policy_revision']})
    assert saved['policy']['filesystem_mode'] == 'read-only'
    assert 'capabilities' not in saved['policy']
    assert control.read('session', 'control-two')['policy'] == control.read('template', 'default')['policy']
    with pytest.raises(ValueError, match='revision'):
        control.save('session', 'control-one', policy)


@pytest.mark.parametrize('scope', [{'session_id': 'model'}, {'task_id': 'background'}, {'run_id': 'agent-run'}])
def test_agent_or_job_cannot_use_native_owner_controls(scope):
    from core.security.control import PermissionControl
    with identity_scope(**scope), pytest.raises(PermissionError):
        PermissionControl().save('template', 'default', {'filesystem_mode': 'full-access', 'policy_revision': 1})


def test_task_control_uses_business_mutation_and_updates_expected_policy_reference(tmp_path):
    from core.security.control import PermissionControl
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    scheduler = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    scheduler.create({'id': 'daily', 'name': 'Daily', 'enabled': True,
        'schedule': {'type': 'interval', 'seconds': 3600},
        'action': {'type': 'tool_call', 'tool_name': 'todos', 'tool_params': {}}})
    control = PermissionControl(task_service=lambda agent: scheduler)
    policy = control.read('task', 'daily')['policy']
    updated = control.save('task', 'daily', {**policy, 'capabilities': {'plugin:stella.tasks': {'write': 'allow'}}})
    assert scheduler.store.get_task('daily')['run_policy_revision'] == updated['policy']['policy_revision']


def test_run_policy_http_requires_auth_and_returns_authoritative_policy(monkeypatch):
    import json
    from types import SimpleNamespace
    from channel.desktop import desktop_channel as desktop
    calls = []
    monkeypatch.setattr(desktop, '_require_auth', lambda: calls.append('auth'))
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'input', lambda **kwargs: SimpleNamespace(kind='session', subject='api-one', agent=''))
    reply = json.loads(desktop.SecurityRunPolicyHandler().GET())
    assert calls == ['auth'] and reply['status'] == 'success'
    assert reply['policy']['policy_revision'] >= 1
    def reject():
        raise PermissionError('unauthenticated')
    monkeypatch.setattr(desktop, '_require_auth', reject)
    with pytest.raises(PermissionError):
        desktop.SecurityRunPolicyHandler().GET()


def test_resource_and_runtime_http_routes_require_auth(monkeypatch):
    from channel.desktop import desktop_channel as desktop
    def reject():
        raise PermissionError('unauthenticated')
    monkeypatch.setattr(desktop, '_require_auth', reject)
    for handler in (desktop.DataAccessSettingsHandler, desktop.CapabilityAccessSettingsHandler, desktop.SecurityRuntimeTrustHandler, desktop.SecurityMigrationHandler):
        with pytest.raises(PermissionError):
            handler().GET()
        with pytest.raises(PermissionError):
            handler().POST()


def test_desktop_task_creation_persists_the_selected_business_capabilities(tmp_path):
    from tests.test_scheduler_web_update import _post_create
    result = _post_create(tmp_path, {'name': 'business task', 'schedule': {'type': 'interval', 'seconds': 3600},
        'action': {'type': 'agent_task', 'task_description': 'synthetic task',
            'conversation_mode': 'reuse', 'conversation_space': 'personal'}, 'session_id': 'source-chat',
        'run_policy': {'filesystem_mode': 'read-only', 'capabilities': {'plugin:stella.tasks': {'read': 'allow', 'write': 'allow'}}}})
    assert result['status'] == 'success', result
    assert result['task']['run_policy']['capabilities']['plugin:stella.tasks']['write'] == 'allow'


def test_linked_reminder_permission_edit_does_not_change_managed_schedule(tmp_path):
    from core.security.control import PermissionControl
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    store = TaskStore(str(tmp_path / 'tasks.json'))
    store.add_task({'id': 'reminder', 'linked_todo_id': 'todo-1', 'name': 'Reminder', 'enabled': True,
        'schedule': {'type': 'interval', 'seconds': 3600}, 'action': {'type': 'send_message', 'content': 'reminder'}})
    scheduler = SchedulerMutations(store)
    control = PermissionControl(task_service=lambda agent: scheduler)
    policy = control.read('task', 'reminder')['policy']
    before = store.get_task('reminder')['schedule']
    control.save('task', 'reminder', {**policy, 'filesystem_mode': 'read-only'})
    assert store.get_task('reminder')['schedule'] == before
    assert 'next_run_at' not in store.get_task('reminder')
    with pytest.raises(ValueError):
        scheduler.update('reminder', {'schedule': {'type': 'interval', 'seconds': 7200}})


def test_connection_owner_edit_uses_revision_and_does_not_change_template_or_legacy_input(monkeypatch):
    from config import conf
    from copy import deepcopy
    from core.security.control import PermissionControl
    from agent.tools.tool_manager import ToolManager
    monkeypatch.setitem(ToolManager().__dict__, '_load_mcp_configs', lambda: [{'name': 'email', 'type': 'stdio'}])
    from core.security.resource_grants import ResourceGrantStore
    from core.security.service import SecurityService
    from agent.permission.gateway import Capability, SecurityGateway, register_tool
    from core.security.capabilities import CapabilityDescriptor
    from core.security.run_policy import gateway_policy, resolve_run_policy
    legacy = {'preset': 'full-access', 'connections': {'mcp:email': {'allowed': False, 'scopes': []}}}
    monkeypatch.setitem(conf(), 'security_policy', deepcopy(legacy))
    before = PermissionControl().read('template', 'default')['policy']
    store = ResourceGrantStore()
    revision = store.connection_revision(user_id='local-default', connection='mcp:email')
    ResourceGrantStore().update_owned_connection(user_id='local-default', connection='mcp:email', policy=dict(allowed=True, scopes=['read']), expected_revision=revision)
    assert PermissionControl().read('template', 'default')['policy'] == before
    assert conf()['security_policy'] == legacy
    with pytest.raises(ValueError, match='revision'):
        ResourceGrantStore().update_owned_connection(user_id='local-default', connection='mcp:email', policy=dict(allowed=False, scopes=[]), expected_revision=revision)
    descriptor = CapabilityDescriptor('mcp:email/fetch_messages', 'mcp', 'read')
    register_tool('mcp_resource_read', Capability.EXTERNAL, connection='mcp:email', descriptor=descriptor)
    with identity_scope(session_id='owner-restored'):
        policy = gateway_policy(resolve_run_policy('owner-restored'))
        assert 'mcp:email' not in policy['connections']  # New subjects do not inherit legacy global defaults.
        assert SecurityService.legacy_connection_restrictions('mcp:email') == [{'allowed': True, 'scopes': ['read']}]
        assert SecurityGateway.check('full-access', 'mcp_resource_read', {}, policy=policy,
            capability_descriptor=descriptor, create_pending=False).allowed


def test_permission_control_has_no_capability_catalog():
    from core.security.control import PermissionControl
    result = PermissionControl().read('template', 'default')
    assert result['capabilities'] == []
    assert 'capabilities' not in result['policy']


def test_explicit_mode_replaces_hidden_legacy_file_denial():
    from core.security.run_policy import resolve_run_policy
    from core.security.control import PermissionControl
    from agent.runtime.tool_invocation import permission_denial
    resolve_run_policy('custom-business', seed={'preset': 'custom', 'base_preset': 'full-access',
        'rules': {'workspace_write': 'deny'}})
    control = PermissionControl()
    state = control.read('session', 'custom-business')
    control.save('session', 'custom-business', {**state['policy'], 'filesystem_mode': 'workspace-write'})
    with identity_scope(session_id='custom-business'):
        assert permission_denial('todos', {'action': 'create', 'title': 'authorized'}) is None
        assert permission_denial('write', {'path': '/outside/blocked.txt', 'content': 'blocked'})


def test_session_settings_report_independent_three_mode_policy(tmp_path, monkeypatch):
    from channel.desktop import desktop_channel as desktop
    from agent.workspace import project_store
    project = tmp_path / 'project'
    project.mkdir()
    project_store.set_project_dir('report-one', str(project))
    project_store.set_security_policy(str(project), {'preset': 'custom', 'base_preset': 'workspace-write',
        'rules': {'network': 'deny'}})
    monkeypatch.setattr(desktop, '_session_model_catalog', lambda: [])
    state = desktop._session_settings_state('report-one', None)['permission']
    assert state['source'] == 'independent'
    assert state['mode'] == 'workspace-write'
    assert state['presets'] == ['read-only', 'workspace-write', 'full-access']


def test_legacy_http_cannot_change_project_permission_authority(tmp_path, monkeypatch):
    import json
    from channel.desktop import desktop_channel as desktop
    from agent.workspace import project_store
    project = tmp_path / 'project'
    project.mkdir()
    original = project_store.get_security_policy(str(project))
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps({'target': 'project', 'project': str(project),
        'policy': {'preset': 'full-access'}}).encode())
    result = json.loads(desktop.SecurityPolicyHandler().POST())
    assert result['status'] != 'success'
    assert project_store.get_security_policy(str(project)) == original


def test_background_summary_uses_authoritative_task_snapshot():
    from core.security.run_policy import resolve_run_policy
    from agent.permission.center import describe_task_permission
    policy = resolve_run_policy(task_id='display-task', seed={'filesystem_mode': 'full-access', 'capabilities': {}})
    row = describe_task_permission({'id': 'display-task', 'run_permission_mode': 'read-only',
        'run_policy_version': 1, 'run_policy_revision': policy['policy_revision'], 'enabled': True})
    assert row['effective_mode'] == 'full-access'
    assert row['permission_source'] == 'independent'
    row = describe_task_permission({'id': 'display-task', 'run_policy_version': 1,
        'run_policy_revision': policy['policy_revision'] + 1})
    assert row['unknown'] and row['policy_error']


def test_resource_catalog_uses_exact_folder_and_account_qualified_course_ids(monkeypatch):
    from core.security.access_settings import FeatureAccessControl
    from types import SimpleNamespace
    monkeypatch.setattr('core.data.runtime.current_service', lambda: SimpleNamespace(list_sources=lambda: [
        {'id': 'email-source', 'provider': 'email', 'name': 'Mailbox', 'account_id': 'school'},
        {'id': 'learning-source', 'provider': 'elearning', 'name': 'Courses',
         'config': {'account_id': 'student-account', 'course_ids': ['42', '43']}}]))
    resources = [row for source in FeatureAccessControl('data', 'unused')._sources() for row in FeatureAccessControl._source_resources(source)]
    assert {'kind': 'course', 'id': 'student-account:42', 'name': 'Courses · 42', 'operations': ['read']} in resources
    assert {'kind': 'folder', 'id': 'school', 'name': 'Mailbox', 'operations': ['read']} in resources


def test_background_summary_preserves_owner_for_mode_editor(registered_agent):
    from agent.permission.center import describe_task_permission
    from core.security.run_policy import save_run_policy
    registered_agent('another-agent')
    policy = save_run_policy('task', 'shared-id', {'filesystem_mode': 'workspace-write'}, agent_id='another-agent')
    row = describe_task_permission({'id': 'shared-id', 'owner_agent_id': 'another-agent',
        'run_policy_version': 1, 'run_policy_revision': policy['policy_revision']})
    assert row['owner_agent_id'] == 'another-agent'
    assert row['effective_mode'] == 'workspace-write'

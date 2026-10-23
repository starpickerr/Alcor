"""All capability types share the Agent's three modes, not plugin grants."""
from pathlib import Path

import pytest

from common.runtime_identity import identity_scope
from core.security.capabilities import CapabilityDescriptor, authorization
from core.security.run_policy import gateway_policy, save_run_policy


@pytest.mark.parametrize('source,identifier', [('builtin', 'browser'), ('plugin', 'stella.tasks'), ('mcp', 'mail')])
@pytest.mark.parametrize('mode,operation,expected', [
    ('read-only', 'read', 'allow'), ('read-only', 'write', 'deny'),
    ('read-only', 'system_execute', 'deny'), ('read-only', 'unknown', 'deny'),
    ('workspace-write', 'read', 'allow'), ('workspace-write', 'write', 'allow'),
    ('workspace-write', 'delete', 'confirm'), ('workspace-write', 'external_send', 'confirm'),
    ('workspace-write', 'system_execute', 'confirm'), ('workspace-write', 'unknown', 'confirm'),
    ('full-access', 'read', 'allow'), ('full-access', 'write', 'allow'),
    ('full-access', 'delete', 'allow'), ('full-access', 'external_send', 'allow'),
    ('full-access', 'unknown', 'allow'),
])
def test_operation_decision_uses_mode_for_every_capability(source, identifier, mode, operation, expected):
    descriptor = CapabilityDescriptor(f'{source}:{identifier}', source, operation)
    policy = {'filesystem_mode': mode, 'capabilities': {descriptor.capability_id: {operation: 'deny'}}}
    assert authorization(policy, descriptor) == expected


def test_service_write_works_with_workspace_mode_without_capability_grant(todos_plugin):
    save_run_policy('session', 'mode-writer', {'filesystem_mode': 'workspace-write', 'capabilities': {}})
    with identity_scope(session_id='mode-writer'):
        created = todos_plugin.create_todo({'title': 'Unified mode'})
        assert todos_plugin.get_todo(created['id'])['title'] == 'Unified mode'
    save_run_policy('session', 'mode-reader', {'filesystem_mode': 'read-only', 'capabilities': {}})
    with identity_scope(session_id='mode-reader'), pytest.raises(PermissionError):
        todos_plugin.create_todo({'title': 'denied'})


def test_plugin_native_owner_does_not_need_independent_grant():
    from core.plugins.authz import PluginAuthorizationStore
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    authz = PluginAuthorizationStore()
    session = PluginSession(Path('samples/plugins/alpha'), source_label='test', registry=PluginRegistry(), authz=authz)
    try:
        session.start()
        result = session.invoke_contract(contract='stella.sample.alpha@1', method='ping', input_payload={},
            origin='ui', grant_ref='not-an-authorization')
        assert result['status'] == 'succeeded'
        assert authz.get('not-an-authorization') is None
    finally:
        session.dispose()


def test_background_sensitive_mode_requires_explicit_full_access():
    from agent.runtime.tool_invocation import permission_denial
    from agent.permission.gateway import SecurityGateway
    save_run_policy('session', 'background-mode', {'filesystem_mode': 'workspace-write', 'capabilities': {}})
    with identity_scope(session_id='background-mode'):
        before = len(SecurityGateway.pending())
        assert permission_denial('todos', {'action': 'delete', 'id': 'one'}, noninteractive=True)
        assert len(SecurityGateway.pending()) == before
    save_run_policy('session', 'background-mode', {'filesystem_mode': 'full-access'})
    with identity_scope(session_id='background-mode'):
        assert permission_denial('todos', {'action': 'delete', 'id': 'one'}, noninteractive=True) is None


def test_current_mode_still_cannot_write_direct_files_in_read_only(tmp_path):
    from agent.runtime.tool_invocation import permission_denial
    save_run_policy('session', 'read-files', {'filesystem_mode': 'read-only', 'capabilities': {}})
    with identity_scope(session_id='read-files'):
        assert permission_denial('write', {'path': str(tmp_path / 'blocked'), 'content': 'blocked'}, noninteractive=True)


def test_explicit_connection_deny_survives_full_access():
    from agent.permission.gateway import SecurityGateway, Capability, register_tool
    descriptor = CapabilityDescriptor('mcp:blocked', 'mcp', 'read')
    register_tool('blocked_connection_test', Capability.EXTERNAL, connection='mcp:blocked', descriptor=descriptor)
    policy = gateway_policy({'filesystem_mode': 'full-access', 'capabilities': {},
        'connections': {'mcp:blocked': {'allowed': False, 'scopes': []}}})
    assert not SecurityGateway.check('full-access', 'blocked_connection_test', {}, policy=policy,
        capability_descriptor=descriptor).allowed


def test_mode_only_owner_save_repairs_missing_task_policy(tmp_path, monkeypatch):
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    from core.security.control import PermissionControl
    from core.security.migration import ensure_migrated
    from core.security.task_policy import TaskPolicyChange
    ensure_migrated()
    service = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    with monkeypatch.context() as fault:
        fault.setattr(TaskPolicyChange, 'apply', lambda self: (_ for _ in ()).throw(OSError('interrupted')))
        with pytest.raises(OSError):
            service.create({'id': 'one', 'name': 'one', 'enabled': True, 'schedule': {'type': 'interval', 'seconds': 60},
                'action': {'type': 'tool_call', 'tool_name': 'todos', 'tool_params': {'action': 'list'}}})
    control = PermissionControl(task_service=lambda agent: service)
    assert control.read('task', 'one')['policy']['policy_revision'] == 0
    saved = control.save('task', 'one', {'filesystem_mode': 'workspace-write', 'policy_revision': 0})
    assert saved['migration_status'] == 'complete'
    assert 'capabilities' not in saved['policy']


def test_mode_control_no_longer_exposes_per_capability_settings():
    from core.security.control import PermissionControl
    state = PermissionControl().read('template', 'default')
    assert 'capabilities' not in state['policy']
    assert not state.get('capabilities')

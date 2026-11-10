"""Capability operations and file execution are independent conjunctive gates."""
import pytest

from common.runtime_identity import identity_scope


def set_policy(session, mode, capabilities):
    from core.security.run_policy import save_run_policy
    return save_run_policy('session', session, {'filesystem_mode': mode, 'capabilities': capabilities})


def test_workspace_mode_creates_real_todo_without_independent_permission(todos_plugin):
    set_policy('tasks-writer', 'workspace-write', {'plugin:stella.tasks': {'read': 'allow', 'write': 'allow'}})
    with identity_scope(session_id='tasks-writer'):
        created = todos_plugin.create_todo({'title': '独立业务权限'})
        assert created['title'] == '独立业务权限'
        assert todos_plugin.get_todo(created['id'])['id'] == created['id']


def test_full_access_replaces_obsolete_per_plugin_read_only_setting(todos_plugin):
    set_policy('tasks-reader', 'full-access', {'plugin:stella.tasks': {'read': 'allow'}})
    with identity_scope(session_id='tasks-reader'):
        assert todos_plugin.create_todo({'title': 'mode permits write'})['title'] == 'mode permits write'


def test_business_write_does_not_grant_direct_file_write(tmp_path):
    from agent.runtime.tool_invocation import permission_denial
    set_policy('read-files', 'read-only', {'plugin:stella.tasks': {'write': 'allow'},
        'builtin:write': {'write': 'allow'}})
    with identity_scope(session_id='read-files'):
        assert permission_denial('write', {'path': str(tmp_path / 'blocked.txt'), 'content': 'blocked'})
    assert not (tmp_path / 'blocked.txt').exists()


def test_manage_does_not_include_delete(todos_plugin):
    set_policy('manage', 'workspace-write', {'plugin:stella.tasks': {'read': 'allow', 'write': 'allow'}})
    with identity_scope(session_id='manage'):
        created = todos_plugin.create_todo({'title': 'retained'})
        with pytest.raises(PermissionError):
            todos_plugin.delete_todo(created['id'])
        assert todos_plugin.get_todo(created['id'])['title'] == 'retained'


def test_full_access_does_not_grant_actual_mcp_resource():
    from core.security.service import SecurityService
    set_policy('no-mail', 'full-access', {})
    with identity_scope(session_id='no-mail'), pytest.raises(PermissionError, match='resource'):
        SecurityService.authorize_connection('mail', 'external_send')


def test_unknown_mcp_requires_exact_approval_and_background_preauthorization():
    from agent.permission.gateway import Capability, register_tool, SecurityGateway
    from agent.runtime.tool_invocation import permission_denial
    register_tool('opaque_mcp_test', Capability.EXTERNAL, connection='mcp:opaque', read_only=True)
    set_policy('unknown', 'workspace-write', {})
    with identity_scope(session_id='unknown'):
        assert permission_denial('opaque_mcp_test', {'value': 1})
        pending = [row for row in SecurityGateway.pending() if row['session'] == 'unknown']
        assert len(pending) == 1
        assert SecurityGateway.approve(pending[0]['id'], session_id='unknown')
        assert permission_denial('opaque_mcp_test', {'value': 1}) is None
        assert permission_denial('opaque_mcp_test', {'value': 2}, noninteractive=True)


def test_policy_revision_invalidates_unused_approval():
    from agent.permission.gateway import Capability, register_tool, SecurityGateway
    from agent.runtime.tool_invocation import permission_denial
    register_tool('revision_mcp_test', Capability.EXTERNAL, connection='mcp:revision')
    policy = set_policy('revision', 'workspace-write', {})
    with identity_scope(session_id='revision'):
        assert permission_denial('revision_mcp_test', {})
        pending = next(row for row in SecurityGateway.pending() if row['session'] == 'revision')
        assert SecurityGateway.approve(pending['id'])
        from core.security.run_policy import save_run_policy
        save_run_policy('session', 'revision', {'filesystem_mode': 'read-only'}, expected_revision=policy['policy_revision'])
        assert permission_denial('revision_mcp_test', {}, noninteractive=True)


def test_approved_business_operation_reaches_service_once_without_approving_other_resources(todos_plugin):
    from agent.permission.gateway import SecurityGateway
    from agent.runtime.tool_invocation import permission_denial, invocation_scope
    first = todos_plugin.create_todo({'title': 'approved target'})
    second = todos_plugin.create_todo({'title': 'unapproved target'})
    set_policy('approved-service', 'workspace-write', {'plugin:stella.tasks': {
        'read': 'allow', 'write': 'allow', 'delete': 'confirm'}})
    with identity_scope(session_id='approved-service'):
        arguments = {'action': 'delete', 'id': first['id']}
        assert permission_denial('todos', arguments)
        request = next(row for row in SecurityGateway.pending() if row['session'] == 'approved-service')
        assert SecurityGateway.approve(request['id'])
        assert permission_denial('todos', arguments) is None
        with invocation_scope('approved-call'):
            todos_plugin.delete_todo(first['id'])
            with pytest.raises(PermissionError):
                todos_plugin.delete_todo(second['id'])
        assert todos_plugin.get_todo(second['id'])['title'] == 'unapproved target'

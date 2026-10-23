"""Actual resource grants are distinct from callable capability authorization."""
from datetime import datetime, timedelta, timezone

import pytest


def test_resource_scope_is_subject_operation_purpose_and_user_bound(tmp_path):
    from core.security.resource_grants import ResourceGrantStore
    store = ResourceGrantStore(tmp_path / 'authorization.sqlite')
    store.issue(user_id='alice', subject='session:agent:one', resource={'kind': 'account', 'id': 'school'},
                operations=['read'], purposes=['interactive'])
    store.check(user_id='alice', subject='session:agent:one', resource={'kind': 'account', 'id': 'school'}, operation='read')
    for change in ({'user_id': 'bob'}, {'subject': 'session:agent:two'},
                   {'resource': {'kind': 'account', 'id': 'personal'}}, {'operation': 'write'}, {'purpose': 'background'}):
        args = dict(user_id='alice', subject='session:agent:one', resource={'kind': 'account', 'id': 'school'}, operation='read')
        with pytest.raises(PermissionError):
            store.check(**{**args, **change})


def test_delegation_cannot_expand_and_parent_revoke_is_live_after_restart(tmp_path):
    from core.security.resource_grants import ResourceGrantStore
    path = tmp_path / 'authorization.sqlite'
    store = ResourceGrantStore(path)
    parent = store.issue(user_id='alice', subject='session:agent:one', resource={'kind': 'account', 'id': 'school'},
                         operations=['read'], purposes=['interactive', 'background'])
    child = store.delegate(parent['grant_id'], user_id='alice', subject='task:agent:daily',
                           operations=['read'], purposes=['background'])
    with pytest.raises(PermissionError):
        store.delegate(parent['grant_id'], user_id='alice', subject='task:agent:bad', operations=['write'], purposes=['background'])
    restarted = ResourceGrantStore(path)
    args = dict(user_id='alice', subject='task:agent:daily', resource={'kind': 'account', 'id': 'school'}, operation='read', purpose='background')
    restarted.check(**args)
    store.revoke(parent['grant_id'], user_id='alice', expected_revision=parent['revision'])
    with pytest.raises(PermissionError):
        restarted.check(**args)
    with pytest.raises(ValueError):
        store.revoke(child['grant_id'], user_id='alice', expected_revision=99)


def test_expired_resource_grant_cannot_authorize(tmp_path):
    from core.security.resource_grants import ResourceGrantStore
    store = ResourceGrantStore(tmp_path / 'authorization.sqlite')
    with pytest.raises(ValueError):
        store.issue(user_id='alice', subject='user:alice', resource={'kind': 'account', 'id': 'school'},
                    operations=['read'], valid_until=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())


def test_resource_rejection_is_audited_without_business_values():
    from agent.permission.audit import recent
    from common.runtime_identity import identity_scope
    from core.security.service import SecurityService
    with identity_scope(user_id='audit-owner', session_id='one'):
        with pytest.raises(PermissionError):
            SecurityService.authorize_resource({'kind': 'folder', 'id': 'sensitive-resource-name'}, 'read')
    row = next(row for row in recent() if row['event'] == 'resource_access')
    assert row['decision'] == 'deny' and row['operation'] == 'read'
    assert 'sensitive-resource-name' not in str(row)


def test_existing_plugin_parent_revoke_invalidates_scoped_children():
    from core.plugins.authz import PluginAuthorizationStore
    store = PluginAuthorizationStore()
    store.issue(grant_ref='parent', plugin_id='stella.tasks', user_id='alice', capabilities=['tasks.read'])
    store.issue_scoped(grant_ref='child', parent_ref='parent', plugin_id='stella.tasks', user_id='alice',
        consumer_plugin_id='stella.tasks', capability='tasks.read', methods=['get_todo'],
        resources=[{'kind': 'user', 'id': 'alice'}], purpose='interactive')
    store.revoke('parent')
    with pytest.raises(PermissionError):
        store.check_scoped(grant_ref='parent', plugin_id='stella.tasks', user_id='alice',
            consumer_plugin_id='stella.tasks', method='get_todo', required_capabilities=['tasks.read'],
            resources=[{'kind': 'user', 'id': 'alice'}], purpose='interactive')


def test_mcp_capability_does_not_grant_account_and_subject_grant_is_rechecked(monkeypatch):
    from common.runtime_identity import identity_scope
    from core.mcp import authorization
    from core.plugins.authz import PluginAuthorizationStore
    from core.security.resource_grants import ResourceGrantStore
    monkeypatch.setattr(authorization, 'DEFAULT_AUTHZ', PluginAuthorizationStore())
    def metadata(method, values):
        return {'source_account_id': values.get('account_id')}
    with identity_scope(user_id='alice', agent_id='assistant', session_id='one'):
        args = ('email', 'fetch_messages', {'account_id': 'school'}, 'alice', metadata)
        with pytest.raises(PermissionError):
            authorization.check(*args)

        store = ResourceGrantStore()
        grant = store.issue(user_id='alice', subject='session:assistant:one',
            resource={'kind': 'folder', 'id': 'school'}, operations=['read'])
        assert authorization.check(*args)['resourceScope']['resources'] == [{'kind': 'folder', 'id': 'school'}]
        with pytest.raises(PermissionError):
            authorization.check('email', 'fetch_messages', {'account_id': 'personal'}, 'alice', metadata)
        store.revoke(grant['grant_id'], user_id='alice', expected_revision=1)
        with pytest.raises(PermissionError):
            authorization.check(*args)


def test_generic_mcp_connection_requires_actual_resource_grant(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from common.runtime_identity import identity_scope
    from agent.tools.tool_manager import ToolManager
    from core.security.run_policy import save_run_policy
    from core.security.resource_grants import ResourceGrantStore
    package = tmp_path / 'package'
    package.mkdir()
    script = package / 'server.py'
    script.write_text(Path('tests/fixtures/mcp_echo_server.py').read_text().replace(
        'text = args.get("text", "")', 'text = json.dumps({"accounts": ["school"]})').replace('f"echo:{text}"', 'text'))
    config = {'name': 'scoped-generic', 'command': sys.executable,
        'args': [str(script)], 'toolOperations': {'echo': 'read'}, 'timeout': 10}
    with identity_scope(user_id='resource-test', session_id='chat'):
        save_run_policy('session', 'chat', {'filesystem_mode': 'full-access',
            'capabilities': {'mcp:scoped-generic': {'read': 'allow'}}})
        manager = ToolManager()
        monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [config])
        try:
            manager.set_mcp_enabled('scoped-generic', True)
            with pytest.raises(PermissionError, match='resource'):
                manager.call_mcp_tool('scoped-generic', 'echo', {})
            from agent.registry import get_agent_registry
            agent = get_agent_registry().default_agent_id
            ResourceGrantStore().issue(user_id='resource-test', subject=f'session:{agent}:chat',
                resource={'kind': 'connection', 'id': 'mcp:scoped-generic'}, operations=['read'])
            assert manager.call_mcp_tool('scoped-generic', 'echo', {})['accounts']
        finally:
            manager._teardown_mcp_server('scoped-generic')


@pytest.mark.parametrize('preauthorized', [True, False])
def test_unknown_mcp_real_call_uses_explicit_system_execute_resource(tmp_path, monkeypatch, preauthorized):
    import sys
    from pathlib import Path
    from common.runtime_identity import identity_scope
    from agent.tools.tool_manager import ToolManager
    from core.security.run_policy import save_run_policy
    from core.security.resource_grants import ResourceGrantStore
    from agent.permission.gateway import SecurityGateway
    from agent.runtime.tool_invocation import permission_denial, invocation_scope
    from agent.registry import get_agent_registry
    script = tmp_path / 'server.py'
    script.write_text(Path('tests/fixtures/mcp_echo_server.py').read_text().replace('f"echo:{text}"', 'json.dumps(f"echo:{text}")'))
    config = {'name': 'opaque-real', 'command': sys.executable, 'args': [str(script)], 'timeout': 10}
    with identity_scope(user_id='opaque-owner', session_id='opaque-chat'):
        save_run_policy('session', 'opaque-chat', {'filesystem_mode': 'full-access' if preauthorized else 'workspace-write',
            'capabilities': {'mcp:opaque-real': {'system_execute': 'allow' if preauthorized else 'confirm'}}})
        manager = ToolManager()
        monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [config])
        try:
            manager.set_mcp_enabled('opaque-real', True)
            if preauthorized:
                with pytest.raises(PermissionError, match='resource'):
                    manager.call_mcp_tool('opaque-real', 'echo', {'text': 'ok'})
            else:
                import time
                deadline = time.monotonic() + 5
                while not manager._mcp_tool_instances and time.monotonic() < deadline:
                    time.sleep(.01)
                tool = next(iter(manager._mcp_tool_instances.values()))
                assert permission_denial(tool.name, {'text': 'ok'}, tool=tool)
                request = next(row for row in SecurityGateway.pending() if row['session'] == 'opaque-chat')
                assert SecurityGateway.approve(request['id'])
                assert permission_denial(tool.name, {'text': 'ok'}, tool=tool) is None
            store = ResourceGrantStore()
            grant = store.issue(user_id='opaque-owner', subject=f'session:{get_agent_registry().default_agent_id}:opaque-chat',
                resource={'kind': 'connection', 'id': 'mcp:opaque-real'}, operations=['system_execute'],
                purposes=['interactive', 'background'])
            # Exact once receipt is retained across the trusted manager adapter.
            from core.plugins.caller import invocation_purpose_scope
            with invocation_scope('opaque-once'), invocation_purpose_scope('background' if preauthorized else 'interactive'):
                assert manager.call_mcp_tool('opaque-real', 'echo', {'text': 'ok'}) == 'echo:ok'
                if not preauthorized:
                    with pytest.raises(PermissionError):
                        manager.call_mcp_tool('opaque-real', 'echo', {'text': 'changed'})
            store.revoke(grant['grant_id'], user_id='opaque-owner', expected_revision=1)
            with pytest.raises(PermissionError):
                manager.call_mcp_tool('opaque-real', 'echo', {'text': 'ok'})
        finally:
            manager._teardown_mcp_server('opaque-real')

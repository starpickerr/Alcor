"""Run permissions belong to a subject, never to its workspace or notifier."""
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture(autouse=True)
def isolated_policy_state(tmp_path, monkeypatch):
    monkeypatch.setattr('common.state_dir.shared_root', lambda: tmp_path / 'state')


def test_retired_template_cannot_widen_new_conversations():
    from core.security.policy_store import RunPolicyStore
    from core.security.run_policy import resolve_run_policy, save_run_policy, subject_key
    RunPolicyStore().ensure(subject_key('template', 'default'),
                          {'filesystem_mode': 'full-access', 'capabilities': {}})
    save_run_policy('session', 'existing', {'filesystem_mode': 'workspace-write'})
    assert resolve_run_policy('fresh')['filesystem_mode'] == 'read-only'
    assert resolve_run_policy('existing')['filesystem_mode'] == 'workspace-write'


def test_current_default_is_owner_editable_but_retired_template_is_not():
    from core.security.control import PermissionControl
    control = PermissionControl()
    current = control.read('template', 'default')['policy']
    saved = control.save('template', 'default', {'filesystem_mode': 'full-access',
                         'policy_revision': current['policy_revision']})
    assert saved['policy']['filesystem_mode'] == 'full-access'
    with pytest.raises(ValueError, match='default'):
        control.save('template', 'retired', {'filesystem_mode': 'full-access', 'policy_revision': 1})


def test_project_conversations_have_independent_policies(tmp_path):
    from agent.permission.center import save_conversation_policy
    from agent.permission.security_policy import resolve, rule
    from agent.workspace import project_store
    project = tmp_path / 'project'
    project.mkdir()
    project_store.set_security_policy(str(project), {'preset': 'read-only'})
    project_store.set_project_dir('first', str(project))
    project_store.set_project_dir('second', str(project))
    resolve('workspace-write', project_dir=str(project), session_id='second')
    save_conversation_policy('first', {'preset': 'full-access'})
    first = resolve('workspace-write', project_dir=str(project), session_id='first')
    second = resolve('workspace-write', project_dir=str(project), session_id='second')
    assert rule(first, 'workspace_write') == 'allow'
    assert rule(second, 'workspace_write') == 'deny'
    assert project_store.get_security_policy(str(project))['preset'] == 'read-only'


def test_custom_migration_preserves_explicit_business_and_network_denials():
    from core.security.run_policy import resolve_run_policy, gateway_policy
    legacy = {'preset': 'custom', 'base_preset': 'full-access',
              'rules': {'workspace_write': 'deny', 'network': 'deny'},
              'connections': {'email': {'allowed': False}}}
    policy = resolve_run_policy('legacy', seed=legacy)
    assert policy['capabilities']['plugin:stella.tasks']['write'] == 'deny'
    assert gateway_policy(policy)['rules']['network'] == 'deny'
    assert gateway_policy(policy)['connections']['email']['allowed'] is False
    assert resolve_run_policy('legacy', seed={'preset': 'full-access'}) == policy


def test_policy_revision_conflicts_and_subjects_are_isolated():
    from common.runtime_identity import identity_scope
    from core.security.run_policy import resolve_run_policy, save_run_policy
    initial = resolve_run_policy('one', agent_id='first', seed={'preset': 'read-only'})
    updated = save_run_policy('session', 'one', {'filesystem_mode': 'workspace-write'},
                             agent_id='first', expected_revision=initial['policy_revision'])
    assert updated['policy_revision'] == initial['policy_revision'] + 1
    with pytest.raises(ValueError, match='revision|changed'):
        save_run_policy('session', 'one', {'filesystem_mode': 'full-access'},
                        agent_id='first', expected_revision=initial['policy_revision'])
    assert resolve_run_policy('one', agent_id='second', seed={'preset': 'read-only'})['filesystem_mode'] == 'read-only'
    with identity_scope(user_id='another-user'):
        assert resolve_run_policy('one', agent_id='first', seed={'preset': 'read-only'})['filesystem_mode'] == 'read-only'


def test_concurrent_first_snapshot_is_stable():
    from core.security.run_policy import resolve_run_policy
    with ThreadPoolExecutor(max_workers=4) as executor:
        policies = list(executor.map(lambda mode: resolve_run_policy('same', seed={'preset': mode}),
                                    ['read-only', 'full-access', 'workspace-write', 'read-only']))
    assert all(policy == policies[0] for policy in policies)


def test_task_workspace_cannot_be_replaced_by_source_project(tmp_path):
    from agent.workspace import project_store
    from agent.workspace.context import resolve_context
    project = tmp_path / 'project'
    project.mkdir()
    project_store.set_project_dir('source', str(project))
    workspace = resolve_context('source', 'default', task_id='daily', ensure=True)
    assert workspace.kind == 'automation'
    assert workspace.root != project
    assert workspace.root.is_dir()


def test_invalid_legacy_policy_fails_closed_and_remains_pending_confirmation():
    from core.security.run_policy import resolve_run_policy
    policy = resolve_run_policy('broken', seed={'preset': 'custom', 'rules': {'network': 'oops'}})
    assert policy['filesystem_mode'] == 'read-only'
    assert policy['capabilities'] == {}
    assert policy['migration_status'] == 'needs_confirmation'

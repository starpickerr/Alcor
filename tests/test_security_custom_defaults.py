"""Owner-managed default snapshots and custom policies share the existing authority."""
from copy import deepcopy

import pytest

from common.runtime_identity import identity_scope
from core.plugins.caller import caller_origin_scope
from core.security.control import PermissionControl
from core.security.run_policy import resolve_run_policy, gateway_policy, save_run_policy


@pytest.fixture(autouse=True)
def policy_state(tmp_path, monkeypatch):
    monkeypatch.setattr('common.state_dir.shared_root', lambda: tmp_path / 'state')
    monkeypatch.setattr('core.security.migration.ensure_migrated', lambda: None)


def owner_default(mode='full-access', **values):
    control = PermissionControl()
    with caller_origin_scope('ui'):
        current = control.read('template', 'default')['policy']
        return control.save('template', 'default', dict(filesystem_mode=mode,
            permission_mode=values.pop('permission_mode', mode), policy_revision=current['policy_revision'], **values))['policy']


def test_default_changes_only_new_conversations_and_independent_tasks(tmp_path):
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    first = resolve_run_policy('before')
    assert first['filesystem_mode'] == 'read-only'
    saved = owner_default()
    assert saved['filesystem_mode'] == 'full-access'
    assert resolve_run_policy('before') == first
    assert resolve_run_policy('after')['filesystem_mode'] == 'full-access'
    scheduler = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    task = scheduler.create(dict(id='independent', name='Independent', enabled=True,
        schedule=dict(type='interval', seconds=60), action=dict(type='agent_task', task_description='test')))
    assert task['run_policy']['filesystem_mode'] == 'full-access'
    owner_default('read-only')
    assert scheduler.get('independent')['run_policy']['filesystem_mode'] == 'full-access'


def test_custom_rules_enforce_business_operations_and_do_not_grant_resources():
    from core.security.capabilities import authorization, CapabilityDescriptor
    custom = owner_default(permission_mode='custom', rules={'workspace_write': 'deny', 'network': 'deny'})
    assert custom['permission_mode'] == 'custom'
    policy = gateway_policy(resolve_run_policy('custom-chat'))
    assert policy['preset'] == 'custom'
    assert authorization(policy, CapabilityDescriptor('plugin:example', 'plugin', 'write')) == 'deny'
    assert authorization(policy, CapabilityDescriptor('builtin:send', 'builtin', 'external_send')) == 'deny'
    assert policy['connections'] == {}
    assert policy['extra_roots'] == []


def test_old_client_cannot_erase_custom_rules_and_revision_conflicts_reject():
    control = PermissionControl()
    owner_default(permission_mode='custom', rules={'workspace_write': 'deny'})
    custom = resolve_run_policy('custom-chat')
    with caller_origin_scope('ui'):
        with pytest.raises(ValueError, match='custom'):
            control.save('session', 'custom-chat', dict(filesystem_mode='full-access', policy_revision=custom['policy_revision']))
        patch = dict(filesystem_mode='read-only', permission_mode='read-only', policy_revision=custom['policy_revision'])
        control.save('session', 'custom-chat', patch)
        with pytest.raises(ValueError, match='revision'):
            control.save('session', 'custom-chat', patch)


def test_default_is_user_scoped_and_not_agent_editable():
    owner_default()
    with identity_scope(user_id='other'):
        assert resolve_run_policy('new')['filesystem_mode'] == 'read-only'
    with identity_scope(session_id='agent-chat'), caller_origin_scope('tool'):
        with pytest.raises(PermissionError):
            owner_default('read-only')


def test_agent_implicit_task_intersects_default_but_explicit_escalation_rejects(tmp_path):
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    owner_default()
    save_run_policy('session', 'parent', {'filesystem_mode': 'read-only'})
    scheduler = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    task = dict(id='implicit', name='Implicit', enabled=True, schedule=dict(type='interval', seconds=60),
                action=dict(type='agent_task', task_description='test'))
    with identity_scope(session_id='parent'), caller_origin_scope('tool'):
        saved = scheduler.create(task, origin='agent')
        assert saved['run_policy']['filesystem_mode'] == 'read-only'
        explicit = deepcopy(task)
        explicit.update(id='explicit', run_policy=dict(filesystem_mode='full-access'))
        with pytest.raises(PermissionError):
            scheduler.create(explicit, origin='agent')


def test_same_named_mode_cannot_delegate_wider_custom_rules(tmp_path):
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    save_run_policy('session', 'parent', {'filesystem_mode': 'full-access', 'permission_mode': 'custom',
                                        'rules': {'workspace_write': 'deny'}})
    scheduler = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    with identity_scope(session_id='parent'), caller_origin_scope('tool'):
        with pytest.raises(PermissionError):
            scheduler.create(dict(id='wider', name='Wider', enabled=True, schedule=dict(type='interval', seconds=60),
                action=dict(type='agent_task', task_description='test'), run_policy=dict(filesystem_mode='full-access')), origin='agent')


def test_session_settings_report_custom_mode_and_editable_default(monkeypatch):
    from channel.desktop import desktop_channel as desktop
    monkeypatch.setattr(desktop, '_session_model_catalog', lambda: [])
    owner_default(permission_mode='custom', rules={'network': 'deny'})
    state = desktop._session_settings_state('visible-custom', None)['permission']
    assert state['mode'] == 'custom'
    assert state['global_preset'] == 'custom'
    assert state['policy']['rules']['network'] == 'deny'
    assert state['presets'] == ['read-only', 'workspace-write', 'full-access', 'custom']


def test_linked_user_task_does_not_silently_take_system_default(tmp_path):
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    owner_default()
    scheduler = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    saved = scheduler.create(dict(id='linked', name='Linked', enabled=True,
        schedule=dict(type='interval', seconds=60), action=dict(type='agent_task', task_description='test',
        conversation_mode='source', source_session_id='chat')))
    assert saved['run_policy']['filesystem_mode'] == 'read-only'


def test_default_mode_helpers_and_corrupt_default_recovery_use_authoritative_revision():
    from agent.permission.policy import global_mode
    from core.security.policy_store import RunPolicyStore
    from core.security.run_policy import default_template, subject_key
    owner_default()
    assert global_mode() == 'full-access'
    key = subject_key('template', 'owner-default-v2')
    broken = RunPolicyStore().update(key, lambda current: {**current, 'filesystem_mode': 'invalid'})
    restricted = default_template()
    assert restricted['filesystem_mode'] == 'read-only'
    assert restricted['migration_status'] == 'needs_confirmation'
    assert restricted['policy_revision'] == broken['policy_revision']
    control = PermissionControl()
    repaired = control.save('template', 'default', {'filesystem_mode': 'read-only',
        'permission_mode': 'read-only', 'policy_revision': restricted['policy_revision']})
    assert repaired['migration_status'] == 'complete'


def test_owner_policy_inventory_is_scoped_and_cannot_be_listed_by_agent():
    owner_default(permission_mode='custom', rules={'network': 'deny'})
    resolve_run_policy('owned')
    with identity_scope(user_id='another'):
        resolve_run_policy('other-owner')
    control = PermissionControl()
    rows = control.inventory()['subjects']
    assert [(row['kind'], row['subject']) for row in rows] == [('session', 'owned')]
    assert rows[0]['policy']['permission_mode'] == 'custom'
    assert rows[0]['source'] == 'independent'
    assert rows[0]['original_url'] == '/'
    with identity_scope(session_id='owned'), pytest.raises(PermissionError):
        control.inventory()


def test_custom_base_is_a_consistent_ceiling_for_file_and_business_operations():
    from agent.permission.security_policy import rule
    from core.security.capabilities import authorization, CapabilityDescriptor
    owner_default('read-only', permission_mode='custom',
        rules={'workspace_write': 'allow', 'workspace_outside': 'allow', 'shell': 'allow'})
    policy = gateway_policy(resolve_run_policy('consistent-ceiling'))
    assert rule(policy, 'workspace_write') == 'deny'
    assert rule(policy, 'workspace_outside') == 'deny'
    assert rule(policy, 'shell') == 'deny'
    assert authorization(policy, CapabilityDescriptor('plugin:example', 'plugin', 'write')) == 'deny'


def test_custom_policy_allows_reads_and_blocks_writes_through_real_gateway():
    from agent.permission.gateway import SecurityGateway, DecisionKind
    policy = gateway_policy(resolve_run_policy('gateway-custom', seed={
        'filesystem_mode': 'full-access', 'permission_mode': 'custom',
        'rules': {'workspace_write': 'deny'}, 'capabilities': {}}))
    assert SecurityGateway.check('full-access', 'todos', {'action': 'list'}, policy=policy).kind is DecisionKind.ALLOW
    assert SecurityGateway.check('full-access', 'todos', {'action': 'create', 'title': 'blocked'}, policy=policy).kind is DecisionKind.DENY


def test_custom_rules_survive_gateway_merge_and_child_intersection():
    from agent.permission.gateway import SecurityGateway, DecisionKind
    from agent.permission.security_policy import intersect, merge, default_policy, validate_patch
    policy = gateway_policy(resolve_run_policy('parent-custom', seed={
        'filesystem_mode': 'full-access', 'permission_mode': 'custom',
        'rules': {'workspace_write': 'deny'}, 'capabilities': {}}))
    merged = merge(default_policy(), policy)
    child = intersect(policy, policy)
    for effective in (merged, child):
        assert SecurityGateway.check('full-access', 'todos', {'action': 'create', 'title': 'blocked'}, policy=effective).kind is DecisionKind.DENY
        assert validate_patch(effective)['permission_mode'] == 'custom'


def test_legacy_conversation_client_cannot_remove_custom_via_empty_rules():
    from agent.permission.center import save_conversation_policy
    save_run_policy('session', 'legacy-ui', {'filesystem_mode': 'full-access',
        'permission_mode': 'custom', 'rules': {'workspace_write': 'deny'}})
    with pytest.raises(ValueError, match='custom'):
        save_conversation_policy('legacy-ui', {'preset': 'full-access', 'rules': {}})
    assert resolve_run_policy('legacy-ui')['rules']['workspace_write'] == 'deny'


def test_nested_child_run_keeps_custom_business_limits_in_permission_gate():
    from agent.runtime.tool_invocation import permission_limit_scope, permission_denial
    parent = gateway_policy(resolve_run_policy('restricted-parent', seed={
        'filesystem_mode': 'full-access', 'permission_mode': 'custom',
        'rules': {'workspace_write': 'deny'}, 'capabilities': {}}))
    child = gateway_policy(resolve_run_policy('unrestricted-child', seed={
        'filesystem_mode': 'full-access', 'capabilities': {}}))
    with identity_scope(session_id='unrestricted-child'), permission_limit_scope(parent, ()), permission_limit_scope(child, ()):
        assert permission_denial('todos', {'action': 'list'}, noninteractive=True) is None
        assert permission_denial('todos', {'action': 'create', 'title': 'blocked'}, noninteractive=True)


def test_legacy_session_http_cannot_replace_new_custom(monkeypatch):
    import json
    from channel.desktop import desktop_channel as desktop
    save_run_policy('session', 'old-http', {'filesystem_mode': 'full-access',
        'permission_mode': 'custom', 'rules': {'workspace_write': 'deny'}})
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: b'{"security_policy":{"preset":"full-access","rules":{}}}')
    reply = json.loads(desktop.SessionSettingsHandler().POST('old-http'))
    assert reply['status'] == 'error'
    assert 'custom' in reply['message']
    assert resolve_run_policy('old-http')['rules']['workspace_write'] == 'deny'

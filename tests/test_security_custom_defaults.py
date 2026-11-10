"""Three public modes, fixed defaults and conservative legacy compatibility."""
import pytest

from common.runtime_identity import identity_scope
from core.plugins.caller import caller_origin_scope
from core.security.control import PermissionControl
from core.security.policy_store import RunPolicyStore
from core.security.run_policy import resolve_run_policy, gateway_policy, subject_key


@pytest.fixture(autouse=True)
def policy_state(tmp_path, monkeypatch):
    monkeypatch.setattr('common.state_dir.shared_root', lambda: tmp_path / 'state')
    monkeypatch.setattr('core.security.migration.ensure_migrated', lambda: None)


def test_defaults_ignore_retired_wide_template_for_sessions_and_tasks(tmp_path):
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    RunPolicyStore().ensure(subject_key('template', 'owner-default-v2'),
                           {'filesystem_mode': 'full-access', 'capabilities': {}})
    assert resolve_run_policy('new')['filesystem_mode'] == 'read-only'
    scheduler = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    task = scheduler.create(dict(id='new-task', name='Test', enabled=True,
        schedule=dict(type='interval', seconds=60), action=dict(type='agent_task', task_description='test')))
    assert task['run_policy']['filesystem_mode'] == 'read-only'
    with pytest.raises(ValueError):
        PermissionControl().save('template', 'default', {'filesystem_mode': 'full-access', 'policy_revision': 1})


@pytest.mark.parametrize('extra', [dict(permission_mode='custom', rules={}), dict(rules={}),
    dict(capabilities={}), dict(connections={}), dict(extra_roots=[])])
def test_public_control_rejects_advanced_policy_fields(extra):
    control = PermissionControl()
    policy = control.read('session', 'chat')['policy']
    with pytest.raises(ValueError):
        control.save('session', 'chat', {**policy, **extra})


def test_legacy_custom_is_read_only_and_keeps_denies_and_revocation():
    from agent.permission.gateway import SecurityGateway, DecisionKind
    store = RunPolicyStore()
    key = subject_key('session', 'old')
    store.ensure(key, dict(filesystem_mode='full-access', permission_mode='custom',
        rules={'workspace_read': 'deny', 'network': 'deny'}, capabilities={}, revoked=True,
        legacy_restrictions={'external': 'deny'}, migration_status='complete'))
    policy = resolve_run_policy('old')
    assert policy['filesystem_mode'] == 'read-only'
    assert policy.get('permission_mode', 'read-only') == 'read-only'
    assert policy['revoked']
    effective = gateway_policy(policy)
    assert effective['rules']['workspace_write'] == effective['rules']['shell'] == 'deny'
    assert effective['rules']['network'] == effective['rules']['external'] == 'deny'
    assert SecurityGateway.check('read-only', 'todos', {'action': 'list'}, policy=effective).kind is DecisionKind.DENY
    assert SecurityGateway.check('read-only', 'todos', {'action': 'create'}, policy=effective).kind is DecisionKind.DENY
    public = PermissionControl().read('session', 'old')['policy']
    assert set(public) == {'filesystem_mode', 'policy_revision'}


def test_modes_are_subject_scoped_and_revision_checked():
    control = PermissionControl()
    first = control.read('session', 'first')['policy']
    control.save('session', 'first', {**first, 'filesystem_mode': 'workspace-write'})
    assert control.read('session', 'second')['policy']['filesystem_mode'] == 'read-only'
    with pytest.raises(ValueError, match='revision'):
        control.save('session', 'first', first)
    with identity_scope(session_id='first'), caller_origin_scope('tool'), pytest.raises(PermissionError):
        control.save('session', 'first', first)


def test_agent_cannot_delegate_more_than_read_only(tmp_path):
    from agent.scheduler.mutations import SchedulerMutations
    from agent.scheduler.store import TaskStore
    scheduler = SchedulerMutations(TaskStore(str(tmp_path / 'tasks.json')))
    task = dict(id='implicit', name='Test', enabled=True, schedule=dict(type='interval', seconds=60),
                action=dict(type='agent_task', task_description='test'))
    with identity_scope(session_id='parent'), caller_origin_scope('tool'):
        saved = scheduler.create(task, origin='agent')
        assert saved['run_policy']['filesystem_mode'] == 'read-only'
        assert saved['run_policy'].get('permission_mode') != 'custom'
        with pytest.raises(PermissionError):
            scheduler.create({**task, 'id': 'wider', 'run_policy': {'filesystem_mode': 'full-access'}}, origin='agent')


def test_session_state_has_only_three_modes(monkeypatch):
    from channel.desktop import desktop_channel as desktop
    monkeypatch.setattr(desktop, '_session_model_catalog', lambda: [])
    state = desktop._session_settings_state('chat', None)['permission']
    assert state['mode'] == 'read-only'
    assert state['presets'] == ['read-only', 'workspace-write', 'full-access']
    assert 'rules' not in state and 'process_isolation' not in state


def test_legacy_session_http_cannot_write_policy(monkeypatch):
    import json
    from channel.desktop import desktop_channel as desktop
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: b'{"security_policy":{"preset":"full-access"}}')
    reply = json.loads(desktop.SessionSettingsHandler().POST('old-http'))
    assert reply['status'] == 'error'
    assert resolve_run_policy('old-http')['filesystem_mode'] == 'read-only'

"""Permission migration uses only metadata and never edits business/credential stores."""
import pytest

from common.runtime_identity import identity_scope


@pytest.mark.parametrize('legacy', [{'preset': 'full-access', 'connections': {'mcp:email': 'broken'}}, 'broken'])
def test_invalid_legacy_connection_scope_is_pending_and_can_be_repaired(monkeypatch, legacy):
    from config import conf
    from core.security.control import PermissionControl, ResourceControl
    from core.security.resource_grants import ResourceGrantStore
    monkeypatch.setitem(conf(), 'security_policy', legacy)
    control = PermissionControl()
    state = control.read('template', 'default')
    assert state['migration_status'] == 'needs_confirmation'
    assert state['policy']['filesystem_mode'] == 'read-only'
    revision = ResourceGrantStore().connection_revision(user_id='local-default', connection='mcp:email')
    ResourceControl().set_connection('mcp:email', True, ['read'], revision)
    from core.security.service import SecurityService
    assert SecurityService.legacy_connection_restrictions('mcp:email') == [{'allowed': True, 'scopes': ['read']}]
    saved = control.save('template', 'default', {**state['policy'], 'filesystem_mode': 'read-only',
        'capabilities': {'plugin:stella.tasks': {'read': 'allow', 'write': 'allow'}}})
    assert saved['migration_status'] == 'complete'
    assert saved['compatibility_restrictions'] == {}


def subjects():
    return [dict(kind='session', subject='one', agent='default', legacy={'preset': 'custom',
        'base_preset': 'workspace-write', 'rules': {'workspace_write': 'deny'}}),
        dict(kind='session', subject='two', agent='default', legacy={'preset': 'full-access'}),
        dict(kind='task', subject='daily', agent='default', legacy={'preset': 'read-only'})]


def test_preview_has_no_policy_mutations_and_apply_preserves_deny_idempotently():
    from core.security.migration import PermissionMigration
    from core.security.policy_store import RunPolicyStore
    migration = PermissionMigration(sources=subjects)
    before = migration.preview()
    assert before['pending'] == 3
    assert RunPolicyStore().get(('local-default', 'default', 'session', 'one')) is None
    applied = migration.apply(before['fingerprint'])
    one = RunPolicyStore().get(('local-default', 'default', 'session', 'one'))
    assert one['capabilities']['plugin:stella.tasks']['write'] == 'deny'
    assert applied['status'] == 'applied'
    assert migration.apply(migration.preview()['fingerprint'])['migration_id'] == applied['migration_id']
    assert migration.verify()['valid']


def test_mid_transaction_failure_keeps_all_subjects_unmigrated(monkeypatch):
    from core.security.migration import PermissionMigration
    from core.security.policy_store import RunPolicyStore
    migration = PermissionMigration(sources=subjects)
    preview = migration.preview()
    original = migration._apply_subject
    def fail_second(db, entry):
        if entry['subject'] == 'two':
            raise RuntimeError('injected write failure')
        return original(db, entry)
    monkeypatch.setattr(migration, '_apply_subject', fail_second)
    with pytest.raises(RuntimeError):
        migration.apply(preview['fingerprint'])
    assert RunPolicyStore().get(('local-default', 'default', 'session', 'one')) is None
    monkeypatch.setattr(migration, '_apply_subject', original)
    assert migration.apply(migration.preview()['fingerprint'])['status'] == 'applied'


def test_changed_preview_is_rejected_and_recovery_never_erases_a_later_owner_edit():
    from core.security.migration import PermissionMigration
    from core.security.policy_store import RunPolicyStore
    rows = subjects()
    migration = PermissionMigration(sources=lambda: rows)
    old = migration.preview()
    rows[1]['legacy'] = {'preset': 'read-only'}
    with pytest.raises(ValueError, match='changed'):
        migration.apply(old['fingerprint'])
    migration.apply(migration.preview()['fingerprint'])
    key = ('local-default', 'default', 'session', 'one')
    store = RunPolicyStore()
    store.update(key, lambda current: {**current, 'filesystem_mode': 'read-only'})
    with pytest.raises(ValueError, match='changed'):
        migration.recover()
    assert store.get(key)['policy_revision'] == 2


def test_recovery_is_atomic_and_user_scoped_and_retains_old_grant_revocation():
    from core.security.migration import PermissionMigration
    from core.security.policy_store import RunPolicyStore
    from core.plugins.authz import PluginAuthorizationStore
    from core.security.policy_store import policy_path
    authz = PluginAuthorizationStore(path=policy_path())
    authz.issue(grant_ref='revoked', plugin_id='stella.tasks', user_id='local-default', capabilities=['tasks.read'])
    authz.revoke('revoked')
    migration = PermissionMigration(sources=subjects)
    migration.apply(migration.preview()['fingerprint'])
    with identity_scope(user_id='another-user'):
        assert PermissionMigration(sources=subjects).verify()['status'] == 'not_applied'
    assert migration.recover()['status'] == 'recovered'
    assert authz.get('revoked').revoked
    assert RunPolicyStore().get(('local-default', 'default', 'session', 'one')) is None
    assert migration.apply(migration.preview()['fingerprint'])['status'] == 'applied'


def test_template_edit_freezes_existing_conversations_before_changing_defaults(monkeypatch):
    from config import conf
    from agent.memory import get_conversation_store
    from agent.permission.center import save_global_policy
    from core.security.run_policy import resolve_run_policy
    monkeypatch.setitem(conf(), 'security_policy', {'preset': 'workspace-write'})
    get_conversation_store().append_messages('existing-before-migration', [{'role': 'user', 'content': 'SYNTHETIC_BODY'}])
    save_global_policy({'filesystem_mode': 'full-access'})
    assert resolve_run_policy('existing-before-migration')['filesystem_mode'] == 'workspace-write'
    assert resolve_run_policy('created-after-migration')['filesystem_mode'] == 'full-access'


def test_preview_retains_original_legacy_preferences_bytes(tmp_path, monkeypatch):
    import json
    from agent.workspace import session_prefs
    from agent.registry import get_agent_registry
    from core.security.migration import PermissionMigration
    agent = get_agent_registry().default_agent_id
    path = tmp_path / 'prefs.json'
    path.write_text(json.dumps({'sessions': {f'{agent}::legacy-one': {'permission': 'read-only'}}}))
    monkeypatch.setattr(session_prefs, '_store_file', lambda: str(path))
    before = path.read_bytes()
    preview = PermissionMigration().preview()
    assert any(row['subject'] == 'legacy-one' for row in preview['subjects'])
    assert path.read_bytes() == before


def test_new_project_conversation_uses_template_after_legacy_subjects_are_frozen(tmp_path):
    from agent.workspace import project_store
    from core.security.migration import PermissionMigration
    from core.security.run_policy import resolve_run_policy, save_run_policy
    project = tmp_path / 'project'
    project.mkdir()
    project_store.set_security_policy(str(project), {'preset': 'read-only'})
    project_store.set_project_dir('legacy-in-project', str(project))
    migration = PermissionMigration()
    migration.apply(migration.preview()['fingerprint'])
    save_run_policy('template', 'default', {'filesystem_mode': 'full-access'})
    project_store.set_project_dir('new-in-project', str(project))
    assert resolve_run_policy('legacy-in-project')['filesystem_mode'] == 'read-only'
    assert resolve_run_policy('new-in-project')['filesystem_mode'] == 'full-access'


def test_task_metadata_discovery_never_imports_another_users_legacy_task(tmp_path, monkeypatch):
    import json
    import sqlite3
    from agent.registry import get_agent_registry
    from core.security.migration import PermissionMigration
    path = tmp_path / 'tasks.json'
    path.write_text(json.dumps({'tasks': {'alice': {'id': 'alice-task', 'owner_user_id': 'alice',
        'run_permission_mode': 'read-only'}, 'legacy': {'id': 'legacy-task', 'owner_user_id': ''},
        'other-agent': {'id': 'other-agent-task', 'owner_user_id': 'alice', 'owner_agent_id': 'another-agent'}}}))
    monkeypatch.setattr('common.state_dir.scheduler_file', lambda **kwargs: path)
    with identity_scope(user_id='alice'):
        preview = PermissionMigration().preview()
        tasks = [row for row in preview['subjects'] if row['kind'] == 'task']
        assert [row['subject'] for row in tasks] == ['alice-task']
    path.unlink()
    agent = get_agent_registry().default_agent_id
    with sqlite3.connect(path.with_name('automation.db')) as db:
        db.execute('CREATE TABLE tasks(task_id TEXT, owner_agent_id TEXT, owner_user_id TEXT, data TEXT)')
        db.execute('INSERT INTO tasks VALUES (?,?,?,?)', ('canonical', agent, 'local-default', '{}'))
        db.execute('INSERT INTO tasks VALUES (?,?,?,?)', ('alice-sql', agent, 'alice', '{}'))
    with identity_scope(user_id='local-default'):
        tasks = [row for row in PermissionMigration().preview()['subjects'] if row['kind'] == 'task']
        assert [row['subject'] for row in tasks] == ['canonical']
    with identity_scope(user_id='alice'):
        tasks = [row for row in PermissionMigration().preview()['subjects'] if row['kind'] == 'task']
        assert [row['subject'] for row in tasks] == ['alice-sql']


def test_connection_restrictions_have_one_authority_after_migration(monkeypatch):
    from config import conf
    from core.security.migration import PermissionMigration
    from core.security.service import SecurityService
    from core.security.resource_grants import ResourceGrantStore
    monkeypatch.setitem(conf(), 'security_policy', {'preset': 'full-access',
        'connections': {'mcp:email': {'allowed': False, 'scopes': []}}})
    migration = PermissionMigration(sources=subjects)
    migration.apply(migration.preview()['fingerprint'])
    monkeypatch.setitem(conf(), 'security_policy', {'preset': 'full-access', 'connections': {}})
    assert any(row['allowed'] is False for row in SecurityService.legacy_connection_restrictions('mcp:email'))
    ResourceGrantStore().set_connection_restriction(user_id='local-default', subject='user:local-default',
        connection='mcp:email', policy={'allowed': True, 'scopes': ['read']})
    assert SecurityService.legacy_connection_restrictions('mcp:email') == [{'allowed': True, 'scopes': ['read']}]


def test_migration_materializes_existing_owned_resource_scope_without_granting_new_full_access(monkeypatch):
    from core.security.migration import PermissionMigration
    from core.security.run_policy import resolve_run_policy, save_run_policy
    from core.plugins.authz import PluginAuthorizationStore
    from core.mcp import authorization
    monkeypatch.setattr(authorization, 'DEFAULT_AUTHZ', PluginAuthorizationStore())
    migration = PermissionMigration(sources=lambda: [dict(kind='session', subject='legacy-owner', agent='default', legacy={'preset': 'full-access'})])
    migration.apply(migration.preview()['fingerprint'])
    def metadata(method, args):
        return {'source_account_id': args['account_id']}
    with identity_scope(agent_id='default', session_id='legacy-owner'):
        assert authorization.check('email', 'fetch_messages', {'account_id': 'school'}, 'local-default', metadata)
    save_run_policy('template', 'default', {'filesystem_mode': 'full-access',
        'capabilities': {'mcp:email': {'read': 'allow'}}})
    resolve_run_policy('new-owner')
    with identity_scope(agent_id='default', session_id='new-owner'), pytest.raises(PermissionError):
        authorization.check('email', 'fetch_messages', {'account_id': 'school'}, 'local-default', metadata)

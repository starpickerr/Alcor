"""Business settings own views of the same live grants; no new authority."""
from datetime import datetime, timedelta, timezone

import pytest

from common.runtime_identity import identity_scope
from core.plugins.caller import caller_origin_scope
from core.security.resource_grants import ResourceGrantStore


@pytest.fixture
def access(monkeypatch, tmp_path):
    from core.security.access_settings import FeatureAccessControl
    monkeypatch.setattr('core.security.migration.ensure_migrated', lambda: None)
    store = ResourceGrantStore(tmp_path / 'access.db')
    monkeypatch.setattr('core.security.access_settings.ResourceGrantStore', lambda: store)
    monkeypatch.setattr('core.security.access_settings.FeatureAccessControl._subjects', lambda self: [
        dict(id='session:default:chat', kind='session', subject='chat', agent='default'),
        dict(id='plugin:example.plugin', kind='plugin', subject='example.plugin')])
    monkeypatch.setattr('core.security.access_settings.FeatureAccessControl._sources', lambda self: [
        dict(id='source-a', provider='email', account_id='account-a', name='A'),
        dict(id='source-b', provider='email', account_id='account-b', name='B')])
    monkeypatch.setattr('agent.permission.mcp_access.inventory', lambda: [])
    with identity_scope(user_id='owner'), caller_origin_scope('ui'):
        yield FeatureAccessControl, store


def test_source_settings_preserve_existing_rows_and_effective_denials(access):
    control, store = access
    first = store.issue(user_id='owner', subject='session:default:chat', resource={'kind': 'folder', 'id': 'account-a'}, operations=['read'])
    other = store.issue(user_id='owner', subject='session:default:chat', resource={'kind': 'folder', 'id': 'account-b'}, operations=['read'])
    store.revoke(first['grant_id'], user_id='owner', expected_revision=1)
    before = store.list_owned('owner')
    result = control('data', 'source-a').inventory()
    assert [row['grant_id'] for row in result['grants']] == [first['grant_id']]
    assert result['grants'][0]['effective'] is False
    assert store.list_owned('owner') == before
    with pytest.raises(PermissionError):
        control('data', 'source-a').save({'action': 'revoke', 'grant_id': other['grant_id'], 'revision': 1})


def test_source_cannot_issue_other_resources_or_unknown_subjects(access):
    control, store = access
    api = control('data', 'source-a')
    body = dict(action='issue', subject='session:default:chat', resource={'kind': 'folder', 'id': 'account-b'}, operations=['read'])
    with pytest.raises(PermissionError):
        api.save(body)
    body['resource']['id'] = 'account-a'
    body['subject'] = 'session:other:forged'
    with pytest.raises(ValueError):
        api.save(body)
    assert store.list_owned('owner') == []


def test_existing_expired_child_grants_are_not_reset_by_settings(access):
    control, store = access
    parent = store.issue(user_id='owner', subject='session:default:chat', resource={'kind': 'folder', 'id': 'account-a'}, operations=['read'])
    child = store.delegate(parent['grant_id'], user_id='owner', subject='plugin:example.plugin', operations=['read'], purposes=['interactive'])
    with store.transaction() as db:
        store._write(db, {**parent, 'valid_until': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()})
    before = store.list_owned('owner')
    result = control('data', 'source-a').inventory()
    assert len(result['grants']) == 2 and all(not row['effective'] for row in result['grants'])
    assert store.list_owned('owner') == before
    with pytest.raises(ValueError):
        control('data', 'source-a').save(dict(action='revoke', grant_id=child['grant_id'], revision=0))


@pytest.mark.parametrize('module,kind', [('tasks', None), ('capabilities', 'mcp'), ('capabilities', 'plugins')])
def test_removed_authorization_management_does_not_modify_existing_facts(access, module, kind):
    control, store = access
    store.issue(user_id='owner', subject='task:default:task-a', resource={'kind': 'folder', 'id': 'account-a'}, operations=['read'], purposes=['background'])
    before = store.list_owned('owner')
    with pytest.raises(ValueError, match='removed'):
        control(module, 'task-a', kind=kind).inventory()
    with pytest.raises(ValueError, match='removed'):
        control(module, 'task-a', kind=kind).save(dict(action='issue'))
    assert store.list_owned('owner') == before


def test_agent_cannot_manage_feature_permissions(access):
    control, _ = access
    with identity_scope(session_id='chat'), caller_origin_scope('tool'):
        with pytest.raises(PermissionError):
            control('data', 'source-a').inventory()


def test_feature_endpoints_bind_context_and_authenticate_before_access(access, monkeypatch):
    from channel.desktop import desktop_channel as desktop
    import json
    control, store = access
    calls = []
    monkeypatch.setattr(desktop, '_require_auth', lambda: calls.append('auth'))
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'input', lambda **kwargs: dict(id='source-a', kind='', agent=''))
    reply = json.loads(desktop.DataAccessSettingsHandler().GET())
    assert calls == ['auth'] and reply['available'][0]['id'] == 'account-a'
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(dict(id='source-a', action='issue',
        subject='session:default:chat', resource={'kind': 'folder', 'id': 'account-b'}, operations=['read'])).encode())
    assert json.loads(desktop.DataAccessSettingsHandler().POST())['status'] == 'error'
    assert store.list_owned('owner') == []
    def deny():
        raise PermissionError('unauthenticated')
    monkeypatch.setattr(desktop, '_require_auth', deny)
    for handler in (desktop.DataAccessSettingsHandler, desktop.CapabilityAccessSettingsHandler):
        with pytest.raises(PermissionError):
            handler().POST()


def test_old_authorization_remains_enforced_by_security_service(access):
    from core.security.service import SecurityService
    control, store = access
    # Use the identical persisted store that SecurityService consults.
    import core.security.resource_grants as grants
    original = grants.ResourceGrantStore
    grants.ResourceGrantStore = lambda: store
    try:
        api = control('data', 'source-a')
        state = api.save(dict(action='issue', subject='session:default:chat', resource={'kind': 'folder', 'id': 'account-a'}, operations=['read']))
        with identity_scope(agent_id='default', session_id='chat'):
            SecurityService.authorize_resource({'kind': 'folder', 'id': 'account-a'}, 'read')
            with pytest.raises(PermissionError):
                SecurityService.authorize_resource({'kind': 'folder', 'id': 'account-a'}, 'write')
        api.save(dict(action='revoke', grant_id=state['grants'][0]['grant_id'], revision=1))
        with identity_scope(agent_id='default', session_id='chat'), pytest.raises(PermissionError):
            SecurityService.authorize_resource({'kind': 'folder', 'id': 'account-a'}, 'read')
    finally:
        grants.ResourceGrantStore = original


def test_source_can_revoke_legacy_course_no_longer_selected(access, monkeypatch):
    control, _ = access
    grant = dict(id='old', server='elearning', consumer='example.plugin', scoped=True,
                 resources=[dict(kind='course', id='account:42')], revision=2, revoked=False)
    monkeypatch.setattr(control, '_sources', lambda self: [dict(id='courses', provider='elearning', config=dict(account_id='account', course_ids=['43']))])
    monkeypatch.setattr('agent.permission.mcp_access.inventory', lambda: [grant])
    updates = []
    monkeypatch.setattr('agent.permission.mcp_access.update', lambda body: updates.append(body))
    control('data', 'courses').save(dict(action='legacy', id='old', revision=2, revoked=True, resources=grant['resources']))
    assert updates[0]['revoked'] is True


def test_non_mcp_restriction_can_be_managed_in_capabilities_without_mcp_prefix(access):
    control, store = access
    store.set_connection_restriction(user_id='owner', subject='user:owner', connection='browser', policy={'allowed': False, 'scopes': []})
    api = control('capabilities', 'browser', kind='connection')
    api.save(dict(action='connection', connection_id='browser', allowed=True, scopes=['read'], revision=1))
    assert store.connection_restrictions(user_id='owner', subjects=['user:owner'], connections=['browser']) == [dict(allowed=True, scopes=['read'])]
    assert store.connection_restrictions(user_id='owner', subjects=['user:owner'], connections=['mcp:browser']) == []


def test_retired_mcp_remains_discoverable_from_capability_catalog(access, tmp_path):
    from core.capabilities.catalog import CapabilityCatalog
    _, store = access
    store.issue(user_id='owner', subject='session:default:chat', resource=dict(kind='connection', id='mcp:removed'), operations=['read'])
    catalog = CapabilityCatalog(tmp_path, mcp_rows=lambda: [])
    detail = catalog.detail('mcp', 'removed')
    assert detail['tabs'] == ['overview', 'configuration']
    assert detail['enabled'] is False and detail['features'] == []


def test_conversation_keeps_its_own_legacy_connection_limits(access):
    control, store = access
    store.set_connection_restriction(user_id='owner', subject='session:default:chat', connection='browser', policy={'allowed': False, 'scopes': []})
    control('sessions', 'chat', agent='default').save(dict(action='connection', connection_id='browser', allowed=True, scopes=['read'], revision=1))
    assert store.connection_restrictions(user_id='owner', subjects=['session:default:chat'], connections=['browser']) == [dict(allowed=True, scopes=['read'])]
    assert store.connection_settings(user_id='owner', subject='user:owner') == []


def test_legacy_connection_settings_are_discoverable_before_first_business_edit(access, monkeypatch):
    from config import conf
    from core.security.access_settings import owner_connection_settings, retained_connections
    monkeypatch.setitem(conf(), 'security_policy', dict(preset='workspace-write', connections={'browser': {'allowed': False, 'scopes': []}, 'email': {'allowed': False, 'scopes': ['read']}}))
    monkeypatch.setattr('core.security.migration.resource_authority_migrated', lambda: False)
    rows = owner_connection_settings()
    assert next(row for row in rows if row['id'] == 'browser')['allowed'] is False
    assert 'mcp:email' in retained_connections()

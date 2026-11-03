"""Conversation MCP convenience actions preserve the original grant authority."""
import pytest

from common.runtime_identity import identity_scope
from core.plugins.caller import caller_origin_scope
from core.security.access_settings import FeatureAccessControl
from core.security.resource_grants import ResourceGrantStore


@pytest.fixture
def access(monkeypatch, tmp_path):
    store = ResourceGrantStore(tmp_path / 'grants.sqlite')
    monkeypatch.setattr('core.security.access_settings.ResourceGrantStore', lambda: store)
    monkeypatch.setattr('core.security.migration.ensure_migrated', lambda: None)
    monkeypatch.setattr(FeatureAccessControl, '_subjects', lambda self: [dict(id='session:default:one',kind='session',subject='one',agent='default')])
    monkeypatch.setattr(FeatureAccessControl, '_sources', lambda self: [])
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager._load_mcp_configs', lambda self: [dict(name='docs')])
    monkeypatch.setattr('agent.permission.mcp_access.inventory', lambda: [])
    with identity_scope(user_id='owner'), caller_origin_scope('desktop'):
        yield FeatureAccessControl('sessions','one',agent='default'), store


def enable(control, **changes):
    return control.save(dict(action='enable_mcp',connection='mcp:docs',operations=['read'],revision=control.inventory()['revision'],**changes))


def test_one_action_is_idempotent_current_conversation_only_and_stale_saves_fail(access):
    control, store = access
    before = control.inventory()['revision']
    state = enable(control)
    grant = state['grants'][0]
    assert grant['subject'] == 'session:default:one'
    assert grant['purposes'] == ['interactive'] and grant['operations'] == ['read']
    assert enable(control)['grants'] == state['grants']
    with pytest.raises(ValueError, match='changed'):
        control.save(dict(action='enable_mcp',connection='mcp:docs',operations=['write'],revision=before))
    with pytest.raises(PermissionError):
        store.check(user_id='owner',subject='session:default:two',resource=grant['resource'],operation='read')
    with pytest.raises(PermissionError):
        store.check(user_id='owner',subject='task:default:one',resource=grant['resource'],operation='read')


def test_revoked_expired_and_denied_scopes_are_not_silently_reissued(access):
    from datetime import datetime, timedelta, timezone
    control, store = access
    state = enable(control)
    grant = state['grants'][0]
    store.revoke(grant['grant_id'],user_id='owner',expected_revision=grant['revision'])
    with pytest.raises(PermissionError,match='revoked or expired'):
        enable(control)
    assert len(store.list_owned('owner')) == 1
    with store.transaction() as db:
        store._write(db,{**grant,'valid_until':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()})
    with pytest.raises(PermissionError,match='revoked or expired'):
        enable(control)
    store.set_connection_restriction(user_id='owner',subject='user:owner',connection='mcp:docs',policy={'allowed':False,'scopes':[]})
    assert control.inventory()['owner_restrictions'][0]['allowed'] is False
    with pytest.raises(PermissionError,match='limits'):
        enable(control)
    assert len(store.list_owned('owner')) == 1


def test_agent_cannot_grant_or_select_unconfigured_connection(access):
    control, store = access
    with pytest.raises(PermissionError):
        control.save(dict(action='enable_mcp',connection='mcp:other',operations=['read'],revision=control.inventory()['revision']))
    with identity_scope(session_id='one'), caller_origin_scope('agent'):
        with pytest.raises(PermissionError):
            enable(control)
    assert store.list_owned('owner') == []

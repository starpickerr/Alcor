import pytest
from common.runtime_identity import identity_scope
from core.plugins.authz import PluginAuthorizationStore
from core.plugins.caller import consumer_plugin_scope


@pytest.fixture
def access(tmp_path, monkeypatch):
    store = PluginAuthorizationStore(path=tmp_path / "grants.db")
    store.issue(
        grant_ref="email-default-grant",
        plugin_id="stella.email",
        user_id="local-default",
        capabilities=[],
        purposes=["interactive", "read"],
    )
    store.issue_scoped(
        grant_ref="mail-read",
        parent_ref="email-default-grant",
        plugin_id="stella.email",
        user_id="local-default",
        consumer_plugin_id="stella.briefing",
        capability="email.fetch",
        methods=["fetch_messages"],
        resources=[{"kind": "account", "id": "a"}],
        purpose="interactive",
    )
    store.revoke("mail-read")
    monkeypatch.setattr("core.mcp.authorization.DEFAULT_AUTHZ", store)
    return store


def test_inherited_grants_are_operable_without_retired_plugin(access):
    from agent.permission.mcp_access import inventory, update

    grants = inventory()
    row = next(row for row in grants if row["id"] == "mail-read")
    assert row["revoked"] and row["resources"] == [{"kind": "account", "id": "a"}]
    update(
        {
            "id": "mail-read",
            "revision": row["revision"],
            "revoked": False,
            "purposes": ["interactive"],
            "resources": [{"kind": "account", "id": "a"}],
            "valid_until": None,
        }
    )
    access.check_scoped(
        grant_ref="email-default-grant",
        plugin_id="stella.email",
        user_id="local-default",
        consumer_plugin_id="stella.briefing",
        method="fetch_messages",
        required_capabilities=["email.fetch"],
        resources=[{"kind": "account", "id": "a"}],
        purpose="interactive",
    )
    with pytest.raises(PermissionError):
        update({"id": "mail-read", "revision": row["revision"], "revoked": True})
    assert not access.get("mail-read").revoked


def test_grants_cannot_be_edited_cross_user_or_from_agent(access):
    from agent.permission.mcp_access import inventory, update

    with identity_scope(user_id="other"):
        assert inventory() == []
        with pytest.raises(PermissionError):
            update({"id": "mail-read", "revision": 2, "revoked": False})
    for scope in (
        identity_scope(session_id="chat"),
        consumer_plugin_scope("stella.briefing"),
    ):
        with scope, pytest.raises(PermissionError):
            update({"id": "mail-read", "revision": 2, "revoked": False})
    with pytest.raises(ValueError):
        update({"id": "mail-read", "revision": 2, "capabilities": ["email.write"]})
    with pytest.raises(ValueError):
        update(
            {"id": "mail-read", "revision": 2, "valid_until": "2020-01-01T00:00:00Z"}
        )
    assert access.get("mail-read").revoked


def test_removed_native_provider_grant_api_keeps_revocation(access, monkeypatch):
    from agent.tools.tool_manager import ToolManager
    manager = ToolManager()
    monkeypatch.setitem(manager.__dict__, '_load_mcp_configs', lambda: [{'name': 'email', 'type': 'stdio'}])
    assert manager._load_mcp_configs()[0]['name'] == 'email'
    from channel.desktop import desktop_channel as desktop

    assert not hasattr(desktop, 'CapabilityAccessSettingsHandler')
    assert access.get('mail-read').revoked


def test_expired_legacy_scope_can_be_revoked_without_reissuing_expiry(access):
    from agent.permission.mcp_access import update
    row = access.get('mail-read')
    expired = '2020-01-01T00:00:00Z'
    access.update_owned('mail-read', user_id='local-default', revision=row.policy_revision, revoked=False, valid_until=expired)
    row = access.get('mail-read')
    update(dict(id='mail-read', revision=row.policy_revision, revoked=True, valid_until=expired))
    assert access.get('mail-read').revoked and access.get('mail-read').valid_until == expired

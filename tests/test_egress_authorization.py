"""External content cannot grant itself permission to leave the application."""

import pytest

from common.runtime_identity import identity_scope


def test_full_access_private_egress_needs_no_prompt_and_stays_exact():
    from core.security.service import SecurityService
    from agent.permission.gateway import SecurityGateway
    from core.security.run_policy import resolve_run_policy, save_run_policy
    with identity_scope(session_id="private-egress"):
        resolve_run_policy("private-egress")
        save_run_policy("session", "private-egress", {"filesystem_mode": "full-access"})
        before = SecurityGateway.pending()
        permit = SecurityService.authorize_egress(
            "https://model.invalid/v1/chat", b"PRIVATE_CONTEXT",
            sources=("private:conversation",), wait=False)
        permit.check_request("https://model.invalid/v1/chat", b"PRIVATE_CONTEXT")
        for url, body in [("https://other.invalid", b"PRIVATE_CONTEXT"),
                          ("https://model.invalid/v1/chat", b"CHANGED_CONTEXT")]:
            with pytest.raises(PermissionError):
                permit.check_request(url, body)
        assert SecurityGateway.pending() == before


@pytest.mark.parametrize(
    "sources",
    [
        (),
        ("unknown",),
        ("credential:owner",),
        ("protected-folder:one",),
        ("public", "credential:owner"),
    ],
)
def test_unknown_credential_and_protected_sources_cannot_be_approved(sources):
    from core.security.service import SecurityService
    from agent.permission.gateway import SecurityGateway

    with identity_scope(session_id="forbidden-egress"):
        before = {row["id"] for row in SecurityGateway.pending()}
        with pytest.raises(PermissionError):
            SecurityService.authorize_egress(
                "https://model.invalid", b"opaque", sources=sources, wait=False
            )
        assert {row["id"] for row in SecurityGateway.pending()} == before


def test_background_private_egress_stops_without_queuing_confirmation():
    from core.security.service import SecurityService
    from agent.permission.gateway import SecurityGateway

    with identity_scope(task_id="background-egress"):
        before = {row["id"] for row in SecurityGateway.pending()}
        with pytest.raises(PermissionError):
            SecurityService.authorize_egress(
                "https://model.invalid",
                b"private",
                sources=("private:task",),
                wait=False,
            )
        assert {row["id"] for row in SecurityGateway.pending()} == before


def test_egress_receipt_binds_bytes_receiver_and_live_policy_revision():
    from core.security.service import SecurityService
    from agent.permission.gateway import SecurityGateway
    from core.security.run_policy import save_run_policy

    with identity_scope(session_id="receipt-binding"):
        from core.security.run_policy import resolve_run_policy
        resolve_run_policy("receipt-binding")
        save_run_policy("session", "receipt-binding", {"filesystem_mode": "workspace-write"})
        with pytest.raises(PermissionError):
            SecurityService.authorize_egress(
                "https://model.invalid/chat",
                b"original",
                sources=("private:conversation",),
                wait=False,
            )
        pending = next(
            row
            for row in SecurityGateway.pending()
            if row["tool"] == "model_egress" and row["session"] == "receipt-binding"
        )
        SecurityGateway.approve(pending["id"])
        receipt = SecurityService.authorize_egress(
            "https://model.invalid/chat",
            b"original",
            sources=("private:conversation",),
            wait=False,
        )
        receipt.check_request("https://model.invalid/chat", b"original")
        with pytest.raises(PermissionError):
            receipt.check_request("https://other.invalid/chat", b"original")
        with pytest.raises(PermissionError):
            receipt.check_request("https://model.invalid/chat", b"changed")
        save_run_policy(
            "session", "receipt-binding", {"filesystem_mode": "workspace-write"}
        )
        with pytest.raises(PermissionError):
            receipt.check()


def test_pending_egress_and_confirmation_cannot_cross_user_boundary():
    from core.security.service import SecurityService
    from agent.permission.gateway import SecurityGateway
    with identity_scope(user_id='egress-owner', session_id='owner-session'):
        from core.security.run_policy import resolve_run_policy, save_run_policy
        resolve_run_policy('owner-session')
        save_run_policy('session', 'owner-session', {'filesystem_mode': 'workspace-write'})
        with pytest.raises(PermissionError):
            SecurityService.authorize_egress('https://private.invalid', b'PRIVATE', sources=('private:conversation',), wait=False)
        pending = next(row for row in SecurityGateway.pending() if row['session'] == 'owner-session')
    with identity_scope(user_id='other-owner'):
        assert pending['id'] not in {row['id'] for row in SecurityGateway.pending()}
        assert not SecurityGateway.approve(pending['id'])
        assert not SecurityGateway.reject(pending['id'])
    with identity_scope(user_id='egress-owner'):
        assert SecurityGateway.reject(pending['id'])


def test_approved_egress_cannot_resume_after_connection_is_disabled_and_reenabled():
    from core.security.connections import ConnectionStore, service_id
    from core.security.service import SecurityService

    with identity_scope(user_id='receipt-owner', session_id='connection-receipt'):
        permit = SecurityService.authorize_egress(
            'https://model.invalid/chat', b'PUBLIC', sources=('public:test',), wait=False)
        identifier, _ = service_id('https://model.invalid/chat')
        store = ConnectionStore()
        store.save('receipt-owner', identifier, False, 0)
        store.save('receipt-owner', identifier, True, 1)
        with pytest.raises(PermissionError, match='connection|Connection'):
            permit.check()


def test_egress_receipt_cannot_authorize_a_different_http_operation():
    from core.security.service import SecurityService
    with identity_scope(session_id='operation-receipt'):
        permit = SecurityService.authorize_egress(
            'https://service.invalid/object', b'', sources=('public:test',), wait=False)
        with pytest.raises(PermissionError):
            permit.check_request('https://service.invalid/object', b'', method='DELETE')


def test_egress_receipt_binds_headers_and_connection_category():
    from core.security.service import SecurityService
    with identity_scope(session_id='headers-receipt'):
        permit = SecurityService.authorize_egress(
            'https://service.invalid/object', b'', sources=('public:test',), wait=False,
            method='GET', headers={'X-Scope': 'one'}, category='public-browser')
        permit.check_request('https://service.invalid/object', b'', method='GET',
                             headers={'x-scope': 'one'}, category='public-browser')
        for headers, category in [({'X-Scope': 'two'}, 'public-browser'), ({'X-Scope': 'one'}, None)]:
            with pytest.raises(PermissionError):
                permit.check_request('https://service.invalid/object', b'', method='GET', headers=headers, category=category)

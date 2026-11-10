"""Trusted egress inputs and exact approvals through the existing gateway."""

from contextvars import copy_context
from dataclasses import dataclass
from hashlib import sha256
import time
import json


def operation_digest(method, headers, category):
    snapshot = (method.upper(), sorted((key.lower(), str(value)) for key, value in (headers or {}).items()), category)
    return sha256(json.dumps(snapshot, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class EgressPermit:
    verify: object
    expires: float
    target: str
    content_digest: str
    operation: str

    def check(self):
        if time.monotonic() >= self.expires:
            raise PermissionError("External request approval expired")
        self.verify()

    def check_request(self, url, body, *, method="POST", headers=None, category=None):
        self.check()
        if (
            sha256(url.encode()).hexdigest() != self.target
            or sha256(body).hexdigest() != self.content_digest
            or operation_digest(method, headers, category) != self.operation
        ):
            raise PermissionError(
                "External request receiver, operation or content changed after approval"
            )


def authorize(url, body, *, sources, wait=True, cancel_event=None, method="POST", headers=None, category=None, transport_check=None):
    from common.runtime_identity import current_identity
    from core.execution.context import current_execution_context
    from core.plugins.caller import current_invocation_purpose
    from agent.permission.gateway import (
        SecurityGateway,
        Capability,
        DecisionKind,
        register_tool,
    )
    from core.security.connections import service_id, ConnectionStore
    from core.security.capabilities import CapabilityDescriptor
    from agent.runtime.tool_invocation import _current_authorization
    from agent.permission.audit import record
    from core.plugins.caller import remaining_invocation_seconds
    from core.plugins.cancellation import current_cancellation_events, check_cancelled
    from agent.protocol.cancel import AgentCancelledError

    if not isinstance(body, bytes):
        raise ValueError("External content requires an immutable byte snapshot")
    connection, receiver = service_id(url)
    user = current_identity().user_id or "local-default"
    store = ConnectionStore()
    revisions = {key: store.state(user, key) for key in (connection, category) if key}

    def check_connections():
        for key, expected in revisions.items():
            state = store.state(user, key)
            if not state["enabled"] or state["revision"] != expected["revision"]:
                raise PermissionError("External connection was disabled or changed after approval")

    check_connections()
    sources = tuple(sorted(set(sources)))
    if not sources or any(
        not isinstance(value, str) or not value.startswith(("public:", "private:"))
        for value in sources
    ):
        record(
            tool="model_egress",
            operation="external_send",
            resource=receiver,
            risk="L4",
            decision="deny",
            reason="Unknown, credential or protected data provenance cannot leave the application.",
        )
        raise PermissionError(
            "Unknown, credential or protected data cannot be sent to an external service"
        )
    private = any(value.startswith("private:") for value in sources)
    identity = current_identity()
    active = current_execution_context()
    if active:
        active.check_active()
    background = bool(
        identity.task_id
        or current_invocation_purpose() == "background"
        or active
        and active.purpose == "background"
    )
    interactive_wait = wait and not background
    mode, kwargs, binding = _current_authorization(
        None, identity.agent_id, identity.session_id, None, interactive_wait
    )
    # This is a transport check, not an Agent-discoverable tool or a substitute
    # for checking the business operation and its allowlist before this point.
    kwargs["allowlist"] = None
    register_tool("model_egress", Capability.EXTERNAL)
    from core.security.connections import connection_catalog
    model_binding = next((row for row in connection_catalog()
                          if row['id'] == connection and row.get('endpoint') == receiver
                          and any(owner.startswith('model:') for owner in row.get('owners', []))), None) if sources == ('private:model-context',) else None
    necessary = bool(model_binding or transport_check and transport_check())
    descriptor = CapabilityDescriptor("builtin:model_egress", "builtin", "read" if not private or necessary else "external_send")
    from core.security.service import SecurityService
    if SecurityService.native_user_operation():
        kwargs['policy'] = {**kwargs['policy'], 'filesystem_mode': 'full-access', 'preset': 'full-access',
                            'rules': {}, 'migration_status': 'complete'}
        kwargs['policy'].pop('error', None)
        mode = 'full-access'
    arguments = dict(
        action="send_private_context" if private else "send_public_input",
        receiver=receiver,
        target=sha256(url.encode()).hexdigest(),
        content_digest=sha256(body).hexdigest(),
        sources=sources,
        task=identity.task_id,
        bytes=len(body),
        connection_revisions={key: value["revision"] for key, value in revisions.items()},
        operation=operation_digest(method, headers, category),
    )

    def check(**extra):
        check_connections()
        return SecurityGateway.check(
            mode,
            "model_egress",
            arguments,
            capability_descriptor=descriptor,
            create_pending=not background,
            **kwargs,
            **extra,
        )

    decision = check()
    if decision.kind is DecisionKind.CONFIRM and interactive_wait:
        events = current_cancellation_events()

        class Cancellation:
            def is_set(self):
                if (
                    cancel_event
                    and cancel_event.is_set()
                    or any(event.is_set() for event in events)
                ):
                    return True
                try:
                    check_connections()
                    remaining_invocation_seconds()
                    return False
                except (PermissionError, AgentCancelledError):
                    return True

        outcome = SecurityGateway.wait_for_decision(decision.request_id, Cancellation())
        if outcome != "approved":
            raise PermissionError(
                "External request confirmation was not approved; no data was sent"
            )
        _, _, fresh_binding = _current_authorization(
            None, identity.agent_id, identity.session_id, None, interactive_wait
        )
        if fresh_binding != binding:
            raise PermissionError(
                "Policy or execution scope changed during external request confirmation"
            )
        decision = check(approval_request_id=decision.request_id)
    if not decision.allowed:
        raise PermissionError(decision.reason)
    context = copy_context()

    def verify():
        def current():
            from core.security.service import check_model_authorization
            check_model_authorization()
            if model_binding and model_binding not in connection_catalog():
                raise PermissionError('Selected model service configuration changed')
            if necessary and transport_check and not transport_check():
                raise PermissionError('Business operation no longer authorizes this transport')
            check_connections()
            remaining_invocation_seconds()
            check_cancelled()
            if active:
                active.check_active()
            _, _, current_binding = _current_authorization(
                None, identity.agent_id, identity.session_id, None, interactive_wait
            )
            if current_binding != binding:
                raise PermissionError(
                    "External request policy or execution scope was revoked"
                )

        context.copy().run(current)

    return EgressPermit(
        verify, time.monotonic() + 300, arguments["target"], arguments["content_digest"], arguments["operation"]
    )

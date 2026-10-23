"""Core gateway that routes Automation task-rule calls to the stella.automation plugin worker.

Core keeps the scheduler tick, occurrence leases, dispatch and delivery
(``agent/scheduler``); only task rules, mutations and handler planning run in the plugin.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from core.plugins.authz import DEFAULT_AUTHZ
from core.plugins.caller import issue_compatibility_grant, managed_plugin_route, plugin_user_id, serialized_gateway, user_grant_ref
from core.plugins.host import PluginSession
from core.plugins.runtime_hub import DEFAULT_REGISTRY

CONTRACT = "stella.automation@1"
PLUGIN_ID = "stella.automation"

_SESSION: PluginSession | None = None
_GRANT_REF = "automation-default-grant"

# Positional parameter names per method; must match the plugin's SERVICE_DESCRIPTORS
# (tests/test_automation_plugin_gateway.py enforces this).
AUTOMATION_READ_METHODS: dict[str, tuple[str, ...]] = {
    "get_task": ("task_id",),
    "list_tasks": ("enabled_only",),
    "calculate_next_run": ("task", "from_time"),
    "validate_task": ("task", "origin"),
    "resolve_handler": ("task",),
}
AUTOMATION_WRITE_METHODS: dict[str, tuple[str, ...]] = {
    "create_task": ("task", "origin"),
    "update_task": ("task_id", "patch", "resolved", "origin"),
    "delete_task": ("task_id",),
    "enable_task": ("task_id", "enabled"),
}
AUTOMATION_METHOD_PARAMS = {**AUTOMATION_READ_METHODS, **AUTOMATION_WRITE_METHODS}


def automation_plugin_enabled() -> bool:
    return managed_plugin_route("stella.automation", os.environ.get("STELLA_AUTOMATION_PLUGIN", ""))


def _plugin_root() -> Path:
    from core.plugins.roots import resolve_plugin_package_root

    return resolve_plugin_package_root(
        plugin_id=PLUGIN_ID,
        env_var="STELLA_AUTOMATION_PLUGIN_ROOT",
        folder_name="automation",
    )


@serialized_gateway
def ensure_automation_session(db_path: Path | None = None) -> PluginSession:
    global _SESSION
    from core.plugins.lifecycle import domain_data_handle
    binding = (domain_data_handle('stella.automation', plugin_user_id()) if db_path is None else
               {'namespace': 'stella.automation', 'dbPath': str(db_path)})
    if _SESSION is not None and (_SESSION.user_id != plugin_user_id() or not _SESSION.is_running or not _SESSION.matches_storage(binding)):
        shutdown_automation_session()
    if _SESSION is not None:
        return _SESSION
    user_id = plugin_user_id()
    session = PluginSession(
        _plugin_root(),
        source_label="first-party-external",
        registry=DEFAULT_REGISTRY,
        user_id=user_id,
        authz=DEFAULT_AUTHZ,
        data_handle=binding,
    )
    if os.environ.get("STELLA_ALLOW_REPO_IMPORT") in {"1", "true", "yes"}:
        os.environ.setdefault("STELLA_REPO_ROOT", str(Path(__file__).resolve().parents[2]))
    issue_compatibility_grant(DEFAULT_AUTHZ,
        grant_ref=user_grant_ref(_GRANT_REF),
        plugin_id=PLUGIN_ID,
        user_id=user_id,
        capabilities=["automation.read", "automation.write", "plugin.invoke"],
        purposes=["interactive", "tool", "job", "any"],
        replace_existing=False,
    )
    session.start()
    _SESSION = session
    return session


@serialized_gateway
def invoke_automation(
    method: str,
    params: dict[str, Any],
    *,
    db_path: Path,
    agent_id: str,
    owner_user_id: str = "",
) -> dict[str, Any]:
    if method not in AUTOMATION_METHOD_PARAMS:
        raise ValueError(f"unknown automation plugin method: {method}")
    from common.runtime_identity import current_identity
    identity = current_identity()
    if (owner_user_id and owner_user_id != plugin_user_id() or
            identity.agent_id and identity.agent_id != agent_id):
        raise PermissionError('Host data handle identity does not match the worker execution')
    owner = owner_user_id or (identity.user_id if identity.user_id not in (None, 'local-default') else '')
    write = method in AUTOMATION_WRITE_METHODS
    payload = dict(params)
    idem = payload.pop("idempotencyKey", None)
    from core.plugins.task_records import task_records
    with task_records(db_path, agent_id, owner, method, payload) as records:
        session = ensure_automation_session(records.path)
        return session.invoke_contract(
            contract=CONTRACT, method=method, input_payload=payload, origin="system",
            grant_ref=user_grant_ref(_GRANT_REF),
            permissions=("automation.write",) if write else ("automation.read",),
            purpose="interactive", idempotency_key=idem or (uuid.uuid4().hex if write else None),
            data_handle={"dbPath": str(records.path), "namespace": PLUGIN_ID,
                         "agentId": agent_id, "userId": owner},
            commit_data=records.commit,
        )


def resolve_handler(task: dict[str, Any], *, db_path: Path, agent_id: str, owner_user_id: str = "") -> dict[str, Any]:
    """Ask the plugin which handler a due task needs; raises on plugin failure."""
    result = invoke_automation(
        "resolve_handler", {"task": task}, db_path=db_path, agent_id=agent_id, owner_user_id=owner_user_id)
    if result.get("status") != "succeeded":
        message = (result.get("error") or {}).get("message") or f"automation plugin returned {result.get('status')}"
        raise RuntimeError(message)
    return result["value"]


@serialized_gateway
def shutdown_automation_session() -> None:
    global _SESSION
    if _SESSION is not None:
        _SESSION.dispose()
        _SESSION = None

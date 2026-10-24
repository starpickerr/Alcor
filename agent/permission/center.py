"""Read and update the Security Center's real persisted state."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from agent.permission import global_mode
from agent.permission.audit import activity_counts, record, recent
from agent.permission.security_policy import MODE_DEFINITIONS, effective_mode, effective_rules, resolve, validate_patch

_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,79}$")


def global_policy() -> dict:
    from core.security.run_policy import default_template, gateway_policy
    return gateway_policy(default_template())


def save_conversation_policy(session: str, value: dict | None, agent: str | None = None) -> dict:
    """Update one conversation, independently of other chats in its project."""
    from core.security.migration import ensure_migrated
    ensure_migrated()
    from core.security.run_policy import gateway_policy, resolve_run_policy, save_run_policy, validate_policy
    current = resolve_run_policy(session, agent)
    legacy_rules = None
    if value is None:
        from core.security.run_policy import default_template
        template = default_template()
        patch = {key: template[key] for key in ('filesystem_mode', 'capabilities', 'connections', 'extra_roots')}
    elif 'filesystem_mode' in value or 'capabilities' in value:
        patch = validate_policy(value)
    else:
        legacy = validate_patch(value)
        legacy_rules = legacy.get('rules')
        patch = {key: legacy[key] for key in ('connections', 'extra_roots') if key in legacy}
        if 'preset' in legacy:
            patch['filesystem_mode'] = legacy.get('base_preset', 'workspace-write') if legacy['preset'] == 'custom' else legacy['preset']
    saved = save_run_policy('session', session, patch, agent_id=agent,
                            expected_revision=value.get('policy_revision', current['policy_revision']) if value else current['policy_revision'],
                            legacy_rules=legacy_rules)
    record(operation='update', resource='conversation policy', conversation=session,
           decision='policy_change', reason='Independent conversation RunPolicy updated.')
    return gateway_policy(saved)


def _write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".security-tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(tmp, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=4)
    os.replace(tmp, path)


def _env_path() -> Path:
    from common.utils import expand_path
    return Path(expand_path("~/.stella/.env"))


def _env_keys() -> set[str]:
    path = _env_path()
    if not path.is_file():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name = line.split("=", 1)[0].strip()
            if _KEY.fullmatch(name):
                names.add(name)
    return names


def secrets() -> list[dict]:
    from config import MODEL_CONFIG_TO_ENV, conf
    names = {name: "credential store" for name in _env_keys()}
    display_names = {}
    cfg = conf()
    for key, value in cfg.items():
        if isinstance(key, str) and (key.endswith("_api_key") or key.endswith("_token")) and value:
            name = MODEL_CONFIG_TO_ENV.get(key, key.upper())
            names[name] = "model configuration" if name not in names else "model configuration + credential store"
    search = (cfg.get("tools") or {}).get("web_search") or {}
    if search.get("bocha_api_key"):
        names["BOCHA_API_KEY"] = "model configuration"
    for entry in cfg.get("custom_providers") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("api_key") or "").strip()
        provider_id = str(entry.get("id") or "").strip()
        if not key or not provider_id:
            continue
        name = f"CUSTOM:{provider_id}"
        names[name] = "custom model provider"
        display_name = entry.get("name")
        if isinstance(display_name, str) and display_name.strip():
            display_names[name] = display_name.strip()
    return [
        {"name": name, "source": source, "configured": True,
         **({"display_name": display_names[name]} if name in display_names else {})}
        for name, source in sorted(names.items())
    ]


def set_secret(name: str, value: str | None) -> None:
    if not isinstance(name, str) or (not name.startswith("CUSTOM:") and not _KEY.fullmatch(name)):
        raise ValueError("invalid credential name")
    if value is not None and (not isinstance(value, str) or "\n" in value or "\r" in value):
        raise ValueError("invalid credential value")
    from config import MODEL_CONFIG_TO_ENV, conf, get_data_root, read_config_template, sync_model_config_to_env
    current = conf()
    if name == "BOCHA_API_KEY":
        from copy import deepcopy
        path = Path(get_data_root()) / "config.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else read_config_template()
        tools = deepcopy(current.get("tools") or {})
        tools.setdefault("web_search", {})["bocha_api_key"] = value or ""
        data["tools"] = tools
        _write_config(path, data)
        current["tools"] = tools
        if name in _env_keys():
            from agent.tools.env_config.env_config import EnvConfig
            tool = EnvConfig()
            entries = tool._read_env_file()
            entries.pop(name, None)
            tool._write_env_file(entries)
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
        record(operation="delete" if value is None else "set", resource=f"credential:{name}", risk="L3",
               decision="policy_change", reason="Search credential status changed.")
        return
    if name.startswith("CUSTOM:"):
        provider_id = name[len("CUSTOM:"):]
        providers = current.get("custom_providers") or []
        if not provider_id or not any(isinstance(p, dict) and p.get("id") == provider_id for p in providers):
            raise ValueError("unknown custom model provider")
        updated = [{**p, "api_key": value or ""} if isinstance(p, dict) and p.get("id") == provider_id else p for p in providers]
        path = Path(get_data_root()) / "config.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else read_config_template()
        data["custom_providers"] = updated
        _write_config(path, data)
        current["custom_providers"] = updated
        from config import sync_image_generation_custom_provider_env
        sync_image_generation_custom_provider_env(current, overwrite=True)
        _refresh_model_clients()
        record(operation="delete" if value is None else "set", resource=f"credential:{name}", risk="L3",
               decision="policy_change", reason="Custom model credential status changed.")
        return
    config_key = next((key for key, env_key in MODEL_CONFIG_TO_ENV.items() if env_key == name), name.lower())
    if (config_key in current or config_key in MODEL_CONFIG_TO_ENV or config_key == "custom_api_key") and (config_key.endswith("_api_key") or config_key.endswith("_token")):
        if name in _env_keys():
            from agent.tools.env_config.env_config import EnvConfig
            env_tool = EnvConfig()
            entries = env_tool._read_env_file()
            entries.pop(name, None)
            env_tool._write_env_file(entries)
        path = Path(get_data_root()) / "config.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else read_config_template()
        data[config_key] = value or ""
        _write_config(path, data)
        current[config_key] = value or ""
        sync_model_config_to_env(current, overwrite=True)
        if config_key not in MODEL_CONFIG_TO_ENV:
            if value:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)
        _refresh_model_clients()
        record(operation="delete" if value is None else "set", resource=f"credential:{name}", risk="L3",
               decision="policy_change", reason="Model credential status changed.")
        return
    from agent.tools.env_config.env_config import EnvConfig
    tool = EnvConfig()
    tool._ensure_env_file()
    entries = tool._read_env_file()
    if value is None:
        entries.pop(name, None)
        os.environ.pop(name, None)
    else:
        entries[name] = value
        os.environ[name] = value
    tool._write_env_file(entries)
    record(operation="delete" if value is None else "set", resource=f"credential:{name}", risk="L3",
           decision="policy_change", reason="Credential status changed in the credential store.")


def _refresh_model_clients() -> None:
    # Existing clients cache credentials; subsequent calls must rebuild after a change.
    from bridge.bridge import Bridge
    Bridge().reset_bot()


def _connection_row(entry_id: str, name: str, *, configured: bool, available: bool,
                    scopes: list | None = None, detail: str = "") -> dict:
    return {
        "id": entry_id, "name": name, "configured": configured, "available": available,
        "connected": available, "scopes": list(scopes or []), "detail": detail,
    }


def connections(policy: dict | None = None) -> list[dict]:
    from core.security.migration import ensure_migrated
    if policy is None:
        ensure_migrated()
    from config import conf
    explicit_policy = policy is not None
    policy = policy or global_policy()
    cfg = conf()
    github_token = bool(os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or cfg.get("github_token"))
    github_verified = bool(github_token and cfg.get("github_verified_at"))
    try:
        from core.services.email_gateway import email_mcp_enabled
        if email_mcp_enabled():
            from application import Application
            accounts = Application.port("email").list_accounts()
            if isinstance(accounts, dict):
                email_configured = bool(accounts.get("configured") or accounts.get("accounts"))
            else:
                email_configured = bool(accounts)
        else:
            email_configured = False
    except Exception:
        email_configured = bool(cfg.get("email_password") or os.environ.get("EMAIL_PASSWORD"))
    try:
        from agent.calendar.apple_sync import AppleCalendarSync
        calendar_status = AppleCalendarSync().status()
        calendar_configured = bool(calendar_status.get("connected") or calendar_status.get("authorization") == "full_access")
        calendar_available = bool(
            calendar_status.get("connected") and calendar_status.get("authorization") == "full_access"
        )
    except Exception:
        calendar_configured = bool(cfg.get("calendar_token") or os.environ.get("CALENDAR_TOKEN"))
        calendar_available = calendar_configured
    try:
        from agent.tools.browser.browser_env import capability_summary
        browser_ready = bool(capability_summary().get("ready"))
    except Exception:
        browser_ready = False
    try:
        from plugins import PluginManager
        plugins_ready = bool(getattr(PluginManager(), "instances", None))
    except Exception:
        plugins_ready = False
    entries = {
        "github": _connection_row(
            "github", "GitHub", configured=github_token, available=github_verified,
            detail="verified" if github_verified else ("configured" if github_token else ""),
        ),
        "email": _connection_row("email", "Email", configured=email_configured, available=email_configured),
        "calendar": _connection_row(
            "calendar", "Calendar", configured=calendar_configured, available=calendar_available,
        ),
        "browser": _connection_row("browser", "Browser", configured=browser_ready, available=browser_ready),
        "plugins": _connection_row("plugins", "Plugins", configured=plugins_ready, available=plugins_ready),
        "external_api": _connection_row("external_api", "External API", configured=False, available=False),
    }
    from agent.tools.tool_manager import ToolManager
    for item in ToolManager().mcp_catalog():
        name=item['name']
        if name in {'email','elearning'}:
            entries.pop(name,None)
        entries['mcp:'+name]=_connection_row('mcp:'+name,name,configured=True,
            available=item.get('enabled',False) and item.get('state')=='ready')
    for key, value in entries.items():
        grant = dict(policy.get("connections", {}).get(key, {}))
        if key in {'mcp:email','mcp:elearning'}:
            legacy=policy.get('connections',{}).get(key.removeprefix('mcp:'),{})
            if legacy.get('allowed') is False:
                grant['allowed'] = False
            old_scopes=legacy.get('scopes')
            if old_scopes and '*' not in old_scopes:
                current=grant.get('scopes')
                grant['scopes']=sorted(set(old_scopes) & set(current)) if current and '*' not in current else list(old_scopes)
                if current and '*' not in current and not grant['scopes']:
                    grant['allowed']=False
        from core.security.migration import resource_authority_migrated
        if resource_authority_migrated() and not explicit_policy:
            from core.security.service import SecurityService
            from core.security.resource_grants import ResourceGrantStore
            from common.runtime_identity import current_identity
            restrictions = SecurityService.legacy_connection_restrictions(key)
            grant = {'allowed': all(row.get('allowed', True) for row in restrictions), 'scopes': []}
            sets = [set(row['scopes']) for row in restrictions if row.get('scopes') and '*' not in row['scopes']]
            if sets:
                scopes = set.intersection(*sets)
                grant.update(scopes=sorted(scopes), allowed=grant['allowed'] and bool(scopes))
            value['revision'] = ResourceGrantStore().connection_revision(user_id=current_identity().user_id or 'local-default', connection=key)
        policy_scopes = grant.get("scopes")
        value.update({
            "allowed": grant.get("allowed", True),
            "scopes": list(policy_scopes) if isinstance(policy_scopes, list) else value["scopes"],
            "last_access": None,
        })
    for row in recent(limit=500, filters={"decision": "allow"}):
        resource = row.get("resource")
        if resource in entries and entries[resource]["last_access"] is None:
            entries[resource]["last_access"] = row.get("at")
    return sorted(entries.values(), key=lambda item: not item["connected"])


def describe_task_permission(task: dict) -> dict:
    """Resolve one background task's effective run permission for display."""
    action = task.get("action") if isinstance(task.get("action"), dict) else {}
    session_id = (
        action.get("execution_session_id")
        or action.get("source_session_id")
        or None
    )
    project = action.get("execution_project_dir") or None
    if project:
        project = str(project)
    if session_id and not project:
        from agent.workspace import project_store
        try:
            project = project_store.get_project_dir(session_id)
        except ValueError:
            project = None
    explicit = task.get("run_permission_mode")
    conversation_mode = action.get("conversation_mode")
    from agent.registry import get_agent_registry
    base = {
        "owner_agent_id": task.get("owner_agent_id") or get_agent_registry().default_agent_id,
        "id": task.get("id"),
        "name": task.get("name") or task.get("id") or "",
        "enabled": bool(task.get("enabled")),
        "conversation_mode": conversation_mode,
        "execution_session_id": session_id,
        "execution_project_dir": project,
        "run_permission_mode": explicit if explicit in {"read-only", "workspace-write", "full-access"} else None,
    }
    from core.security.run_policy import subject_key
    from core.security.policy_store import RunPolicyStore
    policy = RunPolicyStore().get(subject_key('task', str(task.get('id') or ''), task.get('owner_agent_id')))
    if task.get('run_policy_version') == 1 and (not policy or task.get('run_policy_revision') != policy['policy_revision']):
        return {**base, 'effective_mode': None, 'permission_source': 'independent', 'unknown': True,
                'policy_error': 'Task policy is missing or changed; repair permissions before rerunning.'}
    if policy:
        return {**base, 'effective_mode': policy['filesystem_mode'], 'permission_source': 'independent',
                'unknown': policy.get('migration_status') == 'needs_confirmation',
                'policy_error': 'Legacy permissions need confirmation.' if policy.get('migration_status') == 'needs_confirmation' else None}
    if explicit in {"read-only", "workspace-write", "full-access"}:
        return {**base, "effective_mode": explicit, "permission_source": "independent", "unknown": False,
                "policy_error": None}
    if not session_id and not project:
        return {**base, "effective_mode": None, "permission_source": "unspecified", "unknown": True,
                "policy_error": "Task has no bound conversation or independent permission."}
    try:
        policy = resolve(global_mode(), project_dir=project, session_id=session_id)
    except (ValueError, OSError) as error:
        return {**base, "effective_mode": None, "permission_source": "unknown", "unknown": True,
                "policy_error": str(error)}
    if policy.get("error"):
        return {**base, "effective_mode": None, "permission_source": "unknown", "unknown": True,
                "policy_error": policy.get("error")}
    source = "project" if project else ("conversation" if session_id else "global")
    return {**base, "effective_mode": effective_mode(policy), "permission_source": source,
            "unknown": False, "policy_error": None}


def background_tasks() -> list[dict]:
    """List background tasks from the same live store the scheduler API uses."""
    from agent.todos.scheduler_link import default_scheduler_store
    store = default_scheduler_store()
    return [describe_task_permission(task) for task in store.list_tasks()]


def background_task_summary(tasks: list[dict] | None = None) -> dict:
    rows = tasks if tasks is not None else background_tasks()
    modes = {"read-only": 0, "workspace-write": 0, "full-access": 0, "custom": 0}
    unknown = 0
    enabled = paused = 0
    for row in rows:
        if row.get("enabled"):
            enabled += 1
        else:
            paused += 1
        if row.get("unknown") or not row.get("effective_mode"):
            unknown += 1
            continue
        mode = row["effective_mode"]
        if mode in modes:
            modes[mode] += 1
        else:
            modes["custom"] += 1
    return {
        "total": len(rows),
        "enabled": enabled,
        "paused": paused,
        "modes": modes,
        "unknown": unknown,
        "available": True,
    }


def overview(session_id: str | None = None, agent_id: str | None = None) -> dict:
    from agent.workspace import project_store
    project = project_store.get_project_dir(session_id, agent_id) if session_id else None
    mode = global_mode()
    effective = resolve(mode, project_dir=project, session_id=session_id, agent_id=agent_id)
    source = "independent" if session_id else "template"
    from core.workspace.service import resolve_context
    work_root = str(resolve_context(session_id, agent_id).root)
    from agent.permission.gateway import SecurityGateway
    from agent.permission.process_sandbox import capability
    isolation = capability()
    if effective_mode(effective) == "full-access" and isolation["status"] == "enabled":
        isolation = {
            **isolation,
            "reason": (isolation.get("reason") or "")
            + " 完全访问仍拦截其他会话的私有工作区、托管数据、凭据和 Apple 系统授权。",
        }
    activity = [row for row in recent(limit=200) if row.get("at", 0) >= time.time() - 7 * 86400]
    denials = [row for row in activity if row.get("decision") == "deny"]
    high = [row for row in activity if row.get("risk") in ("L3", "L4")]
    counts = activity_counts(days=7)
    active_pending = SecurityGateway.pending()
    all_connections = connections()
    all_secrets = secrets()
    try:
        task_rows = background_tasks()
        task_summary = background_task_summary(task_rows)
    except (OSError, ValueError, TypeError):
        task_rows = []
        task_summary = {"total": None, "enabled": None, "paused": None, "modes": None,
                        "unknown": None, "available": False}
    try:
        from agent.permission.privacy import inventory as privacy_inventory
        privacy = privacy_inventory()
        from agent.permission.privacy import data_access_inventory
        data_access = data_access_inventory(all_connections)
    except (OSError, ValueError, TypeError):
        privacy = {"available": False, "categories": []}
        data_access = {"available":False,"resources":[],"external_transfers":[]}
    return {
        "global_policy": global_policy(), "effective_policy": effective,
        "effective_mode": effective.get("filesystem_mode", effective_mode(effective)), "effective_rules": effective_rules(effective),
        "policy_source": source, "mode_definitions": [row for row in MODE_DEFINITIONS if row['id'] != 'custom'],
        "authorized_roots": [work_root, *(effective.get("extra_roots") or [])],
        "policy_error": effective.get("error"),
        "process_isolation": isolation,
        "project": project, "project_policy": {},
        "projects": project_store.list_known_projects(),
        "conversation_policy": effective if session_id else {},
        "connections": all_connections, "secrets": all_secrets,
        "background_tasks": task_rows,
        "privacy": privacy, "data_access":data_access,
        "summary": {"status": "attention" if denials or active_pending else "normal",
                    "app_password_set": bool(__import__("config").conf().get("web_password")),
                    "connection_count": sum(1 for item in all_connections if item.get("connected")),
                    "secret_count": len(all_secrets), "recent_high_risk": high[:5], "recent_denials": denials[:5],
                    "activity_counts": counts,
                    "background_tasks": task_summary},
    }

"""Trusted default local Server registrations and backward-compatible intent."""

import json
import os
from pathlib import Path
import sys
import shutil

LOCAL_READS = frozenset(
    {"list_accounts", "list_sources", "get_account", "get_settings", "get_cursors"}
)

PUBLIC_TOOLS = {
    "elearning": ["list_courses", "fetch", "list_files", "sync_files"],
    "email": ["list_accounts", "list_sources", "fetch_messages"],
}

# Core-owned semantics for the bundled adapters; remote annotations are hints.
OPERATIONS = {
    'email': {**{name: 'read' for name in ('list_accounts', 'list_sources', 'get_account', 'list_folders',
        'test_connection', 'fetch_messages', 'get_cursors')},
        **{name: 'write' for name in ('save_account', 'set_cursors', 'reset_cursors')}, 'delete_account': 'delete'},
    'elearning': {**{name: 'read' for name in ('get_settings', 'get_file_sync_settings', 'list_courses', 'fetch',
        'list_files', 'fetch_source', 'get_cursors', 'test_connection')},
        **{name: 'write' for name in ('save_settings', 'save_file_sync_settings', 'sync_files', 'set_cursors', 'drain_audit_events')},
        'delete_settings': 'delete'},
}


class ManagedMcpConfig(dict):
    """Core factory provenance; a user JSON field cannot create this type."""

    def __init__(self, *args, data_root=None, legacy_roots=(), package_digest=None,
                 filesystem_outputs=None, output_scope=None, output_temporary=None, credential_env=(),
                 hosted_connector=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.data_root = Path(data_root).absolute() if data_root is not None else None
        self.legacy_roots = tuple(Path(path).absolute() for path in legacy_roots)
        self.package_digest = package_digest
        self.filesystem_outputs = dict(filesystem_outputs or {})
        self.output_scope = output_scope
        self.output_temporary = output_temporary
        self.credential_env = frozenset(credential_env)
        self.hosted_connector = hosted_connector


def _installed_digest(domain):
    """Only Core's distribution catalog may bind a provider to private state."""
    core = Path(__file__).resolve().parents[2]
    root = Path(getattr(sys, '_MEIPASS', core)) if getattr(sys, 'frozen', False) else core
    try:
        catalog = json.loads((root / 'resources/mcp-providers.json').read_text())
        row = catalog['providers'][domain]
        return row.get('bundleSha256' if getattr(sys, 'frozen', False) else 'sourceSha256')
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _package_description(root, domain):
    """Read only bounded public metadata, without importing Server code."""
    from core.capabilities.descriptions import clean_copy

    path = root / f'{domain}_mcp' / 'capability.json'
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            return ''
        with path.open('rb') as stream:
            content = stream.read(65_537)
        if len(content) > 65_536:
            return ''
        metadata = json.loads(content)
        if not isinstance(metadata, dict) or metadata.get('name') != domain:
            return ''
        return clean_copy(metadata.get('description'))
    except (OSError, ValueError):
        return ''


def configurations(manager):
    if os.environ.get("STELLA_MANAGED_MCP", "1") == "0":
        return []
    from common.runtime_identity import use_identity
    from core.plugins.management import default_store

    with use_identity(manager.identity):
        store = default_store()
        user = manager.identity.user_id or "local-default"
        configs = []
        for domain in PUBLIC_TOOLS:
            repo_name = "fudan-elearning-mcp" if domain == "elearning" else "email-mcp"
            repo = Path(
                os.environ.get(
                    f"STELLA_{domain.upper()}_MCP_ROOT",
                    str(Path(__file__).resolve().parents[3] / "MCP" / repo_name),
                )
            ).resolve()
            if getattr(sys, "frozen", False):
                binary = (
                    Path(sys.executable).resolve().parent.parent
                    / (domain + "-mcp")
                    / (domain + "-mcp" + (".exe" if os.name == "nt" else ""))
                )
                command = str(binary)
                launch = []
            elif (repo / "mcp_server.py").is_file():
                python = (
                    repo
                    / ".venv"
                    / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                )
                command = str(python) if python.is_file() else sys.executable
                launch = [str(repo / "mcp_server.py")]
            else:
                command = shutil.which(domain + "-mcp") or domain + "-mcp"
                launch = []
            metadata_root = binary.parent / '_internal' if getattr(sys, 'frozen', False) else repo
            description = _package_description(metadata_root, domain)
            from importlib import import_module

            paths = import_module("core.services." + domain + "_facade")._paths()
            data = paths[0].parent if domain == "email" else paths[0]
            legacy = paths[1].parent if domain == "email" else paths[1]
            legacy_reads = ([legacy / 'mailbox.json'] if domain == 'email' else
                            [legacy / name for name in ('elearning.json', 'elearning-sessions',
                                                        'elearning-courses', 'elearning-file-sync')])
            enabled = store.instance("stella." + domain, user)["intent"] == "enabled"
            if os.environ.get(f"STELLA_{domain.upper()}_PLUGIN", "").lower() in {
                "0",
                "false",
                "no",
            }:
                enabled = False
            if domain == "elearning":
                enabled = enabled and manager.capability_settings.enabled(
                    "tool", "elearning"
                )
            configs.append(
                ManagedMcpConfig({
                    "name": domain,
                    **({'description': description} if description else {}),
                    "type": "stdio",
                    "command": command,
                    "args": launch + ['--hosted-connector']
                    + [
                        "--data-dir",
                        str(data),
                        "--legacy-dir",
                        str(legacy),
                        "--user-id",
                        user,
                    ],
                    "env": {
                        key: os.environ.get(key, "")
                        if user == "local-default" and domain == "email"
                        else ""
                        for key in [
                            "IMAP_HOST",
                            "IMAP_USER",
                            "IMAP_PORT",
                            "IMAP_FOLDER",
                        ]
                    },
                    "enabled": enabled,
                    "timeout": 600,
                    "agentTools": PUBLIC_TOOLS[domain],
                    "settingsUrl": "/" + domain,
                    "toolOperations": dict(OPERATIONS[domain]),
                    "filesystemTools": ['sync_files'] if domain == 'elearning' else [],
                }, data_root=data, legacy_roots=legacy_reads, package_digest=_installed_digest(domain),
                    filesystem_outputs={'sync_files': ('get_settings', 'file_sync_root')} if domain == 'elearning' else {},
                    hosted_connector=domain)
            )
        return configs


def enabled(domain):
    from agent.tools.tool_manager import ToolManager

    manager = ToolManager()
    config = next((c for c in manager._load_mcp_configs() if c["name"] == domain), None)
    return bool(config and manager._mcp_enabled(config))


def invoke(domain, method, params, *, purpose=None):
    from agent.tools.tool_manager import ToolManager
    from core.plugins.cancellation import current_cancellation_events

    events = current_cancellation_events()

    class Cancellation:
        def is_set(self):
            return any(event.is_set() for event in events)

    result = ToolManager().call_mcp_tool(
        domain,
        method,
        params,
        host=True,
        purpose=purpose,
        cancel_event=Cancellation() if events else None,
    )
    return {"status": "succeeded", "value": result}


def native_user_action(host, method, purpose):
    from core.execution.context import current_execution_context
    active = current_execution_context()
    if active:
        active.check_active()
        if active.origin != 'ui' or active.purpose == 'background':
            return False
    from core.plugins.caller import (
        current_caller_origin,
        current_delegation,
        current_consumer_plugin,
        current_invocation_purpose,
    )

    return (
        host
        and purpose != "background"
        and current_invocation_purpose() != "background"
        and method not in {"fetch", "fetch_source", "fetch_messages", "sync_files"}
        and current_caller_origin() in {None, "ui", "system"}
        and not current_delegation()
        and not current_consumer_plugin()
    )

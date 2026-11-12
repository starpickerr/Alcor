"""MCP migration uses real stdio servers and preserves domain state."""

import json
from pathlib import Path
import sys

import pytest

from agent.tools.mcp.mcp_client import McpClient
from core.mcp.managed import ManagedMcpConfig, OPERATIONS
from core.runtime.paths import source_digest

ROOT = Path(__file__).resolve().parents[2]


def server_config(domain, data):
    repo = (
        ROOT / "MCP" / ("fudan-elearning-mcp" if domain == "elearning" else "email-mcp")
    )
    return ManagedMcpConfig({
        "name": domain,
        "type": "stdio",
        "command": sys.executable,
        "args": [
            str(repo / "mcp_server.py"),
            "--data-dir",
            str(data),
            "--user-id",
            "test-user",
        ],
        "timeout": 10,
        "toolOperations": OPERATIONS[domain],
    }, data_root=data, package_digest=source_digest(repo),
        filesystem_outputs={'sync_files': ('get_settings', 'file_sync_root')} if domain == 'elearning' else {})


@pytest.mark.parametrize(
    "domain,method", [("elearning", "get_settings"), ("email", "list_accounts")]
)
def test_independent_server_real_stdio_and_bound_private_state(
    tmp_path, domain, method
):
    client = McpClient(server_config(domain, tmp_path / domain))
    try:
        assert client.initialize(), (
            "independent MCP server must initialize over standard stdio"
        )
        tools = client.list_tools()
        assert method in {t["name"] for t in tools}
        reply = json.loads(client.call_tool(method, {}))["value"]
        assert isinstance(reply, dict)
        assert "password" not in reply
        if domain == "elearning":
            folder = tmp_path / "courses"
            folder.mkdir()
            save = json.loads(
                client.call_tool(
                    "save_settings",
                    {
                        "payload": {
                            "username": "student",
                            "password": "test-secret",
                            "courses": ["42"],
                            "file_sync_root": str(folder),
                        }
                    },
                )
            )["value"]
            assert save["configured"] and save["file_sync_root"] == str(
                folder.resolve()
            )
            assert "test-secret" not in json.dumps(save)
        else:
            save = json.loads(
                client.call_tool(
                    "save_account",
                    {
                        "payload": {
                            "host": "imap.example.com",
                            "user": "test@example.com",
                            "password": "test-secret",
                            "port": 993,
                            "folder": "INBOX",
                        }
                    },
                )
            )["value"]
            assert "test-secret" not in json.dumps(save)
    finally:
        client.shutdown()


def test_server_rejects_data_path_override_and_returns_mcp_error(tmp_path):
    client = McpClient(server_config("elearning", tmp_path / "private"))
    try:
        assert client.initialize()
        response = client._send_request(
            "tools/call",
            {
                "name": "get_settings",
                "arguments": {"data_dir": str(tmp_path / "other")},
            },
        )
        assert response["result"]["isError"] is True
        assert not (tmp_path / "other").exists()
    finally:
        client.shutdown()


def test_mcp_is_error_never_becomes_success_text(monkeypatch):
    client = McpClient({"name": "errors", "type": "stdio", "command": "unused"})
    monkeypatch.setattr(
        client,
        "_send_request",
        lambda *a, **k: {
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": "permission denied"}],
            }
        },
    )
    with pytest.raises(RuntimeError, match="permission denied"):
        client.call_tool("download", {})


def test_default_domains_are_mcp_and_settings_methods_are_not_agent_tools(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STELLA_MANAGED_MCP", "1")
    from agent.tools.tool_manager import ToolManager

    manager = ToolManager()
    configs = {x["name"]: x for x in manager._load_mcp_configs()}
    assert {"elearning", "email"} <= configs.keys()
    assert "save_settings" not in configs["elearning"]["agentTools"]
    assert "sync_files" in configs["elearning"]["agentTools"]
    with pytest.raises(PermissionError):
        manager.call_mcp_tool("elearning", "save_settings", {"payload": {}}, host=False)


def test_migrated_domains_are_not_shipped_or_started_as_plugins():
    from core.plugins.distribution import load_default_plugin_manifest

    ids = {row["pluginId"] for row in load_default_plugin_manifest()["plugins"]}
    assert not {"stella.email", "stella.elearning"} & ids
    for repo in ["Stella-Core/stella-plugins-workspace", "Stella-Plugins"]:
        assert not (ROOT / repo / "plugins/email").exists()
        assert not (ROOT / repo / "plugins/elearning").exists()


def test_settings_and_agent_tools_share_managed_connection_and_durable_switch(
    tmp_path, monkeypatch
, native_settings_action):
    monkeypatch.setenv("STELLA_MANAGED_MCP", "1")
    from core.services import elearning_facade
    from agent.tools.tool_manager import ToolManager

    monkeypatch.setattr(
        elearning_facade,
        "_paths",
        lambda: (tmp_path / "elearning", tmp_path / "briefing"),
    )
    manager = ToolManager()
    facade = elearning_facade.ElearningMcpFacade()
    try:
        value = facade.save_settings(
            {"username": "test", "password": "secret", "courses": ["42"]}
        )
        assert value["configured"] and "secret" not in json.dumps(value)
        assert facade.get_settings()["username"] == "test"
        tools = [
            t.remote_name
            for t in manager._mcp_tool_instances.values()
            if t.server_name == "elearning"
        ]
        assert "sync_files" in tools and "save_settings" not in tools
        original_client = manager._mcp_registry._clients["elearning"]
        manager.set_mcp_enabled("elearning", False)
        assert original_client._closed
        with pytest.raises((PermissionError, RuntimeError), match="disabled"):
            facade.get_settings()
        assert not [
            t
            for t in manager._mcp_tool_instances.values()
            if t.server_name == "elearning"
        ]
        manager.set_mcp_enabled("elearning", True)
        assert facade.get_settings()["username"] == "test"
        assert manager._mcp_registry._clients["elearning"] is not original_client
    finally:
        manager._teardown_mcp_server("elearning")


def test_mcp_file_download_keeps_filesystem_write_permission():
    from agent.permission.gateway import Capability, SecurityGateway, register_tool

    name = "mcp_guarded_download"
    register_tool(
        name,
        Capability.EXTERNAL,
        connection="mcp:elearning",
        read_only=False,
        filesystem_write=True,
    )
    policy = {
        "preset": "full-access",
        "rules": {"workspace_write": "deny"},
        "connections": {},
    }
    assert not SecurityGateway.check(
        "full-access", name, {}, policy=policy, create_pending=False
    ).allowed


def fake_config(domain, data):
    import ast
    import shutil
    config = server_config(domain, data)
    repo = ROOT / 'MCP' / ('fudan-elearning-mcp' if domain == 'elearning' else 'email-mcp')
    bundle = Path(data).parent / (domain + '-fixture-package')
    bundle.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo / (domain + '_mcp'), bundle / (domain + '_mcp'), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(repo / 'requirements.lock', bundle / 'requirements.lock')
    script = bundle / 'server.py'
    shutil.copy2(Path(__file__).parent / 'fixtures/domain_mcp_server.py', script)
    if domain == 'email':
        # Copy the two upstream test doubles, without importing pytest in the
        # runtime or inheriting the host's test/site-packages environment.
        source = (repo / 'tests/test_compatibility.py').read_text()
        nodes = [ast.get_source_segment(source, node) for node in ast.parse(source).body
                 if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in {'_raw', 'FakeImap'}]
        folder = bundle / 'tests'
        folder.mkdir(exist_ok=True)
        (folder / 'test_compatibility.py').write_text('from email.message import EmailMessage as RfcMessage\nSECRET="SYNTHETIC_TOKEN"\n' + '\n'.join(nodes))
    config['args'][0] = str(script)
    config['args'] += ['--domain', domain]
    config.package_digest = source_digest(bundle)
    return config


@pytest.mark.parametrize('stop', ['worker-exit', 'parent-stop'])
def test_output_process_exit_reclaims_only_owned_temporary_files(tmp_path, native_settings_action, stop):
    import threading
    import time
    from contextvars import copy_context
    data, output = tmp_path / 'data', tmp_path / 'output'
    output.mkdir()
    sentinel = output / '.elearning-user-kept'
    sentinel.write_text('KEEP_USER_DATA')
    client = McpClient(fake_config('elearning', data))
    thread = None
    failures = []
    try:
        assert client.initialize()
        client.call_tool('save_settings', {'payload': {'username': 'test', 'password': 'fake',
            'courses': ['42'], 'file_sync_root': str(output)}})
        (data / 'slow-download').touch()
        def download():
            try:
                client.call_tool('sync_files', {})
            except Exception as error:
                failures.append(type(error).__name__)
        thread = threading.Thread(target=copy_context().run, args=(download,))
        thread.start()
        deadline = time.monotonic() + 5
        while not (data / 'download-started').exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (data / 'download-started').exists()
        child = next(iter(client._operation_clients))
        if stop == 'worker-exit':
            child._proc.kill()
            child._proc.wait(timeout=3)
        else:
            client.shutdown()
        thread.join(4)
        assert not thread.is_alive() and failures
        actual = {path.relative_to(output).as_posix() for path in output.rglob('*') if path.is_file()}
        assert actual == {'.elearning-user-kept'}, actual
        assert sentinel.read_text() == 'KEEP_USER_DATA'
        if stop == 'worker-exit':
            assert json.loads(client.call_tool('get_settings', {}))['value']['username'] == 'test'
    finally:
        client.shutdown()
        if thread is not None:
            thread.join(5)


def test_mcp_download_repeats_and_cancellation_prevents_late_publication(tmp_path, native_settings_action):
    import threading
    import time

    data = tmp_path / "data"
    folder = tmp_path / "courses"
    folder.mkdir()
    client = McpClient(fake_config("elearning", data))
    try:
        assert client.initialize()
        persistent_process = client._proc
        client.call_tool(
            "save_settings",
            {
                "payload": {
                    "username": "test",
                    "password": "fake",
                    "courses": ["42"],
                    "file_sync_root": str(folder),
                }
            },
        )
        for _ in range(2):
            result = json.loads(
                client.call_tool("sync_files", {"layout": "courseware"})
            )["value"]
            assert result["status"] == "completed" and not result["errors"]
            assert client._proc is persistent_process and persistent_process.poll() is None
        files = {str(p): p.stat().st_mtime_ns for p in folder.rglob("*.pdf")}
        assert (
            len(files) == 1 and next(folder.rglob("*.pdf")).read_bytes() == b"coursepdf"
        )
        # A separate, fresh selected root makes cancellation exercise a real download.
        cancelled_root = tmp_path / "cancelled"
        cancelled_root.mkdir()
        client.call_tool("save_file_sync_settings", {"root": str(cancelled_root)})
        (data / "download-started").unlink()
        (data / "slow-download").touch()
        cancel = threading.Event()
        failures = []

        def download():
            try:
                client.call_tool("sync_files", {}, cancel_event=cancel)
            except Exception as error:
                failures.append(str(error))

        from contextvars import copy_context
        thread = threading.Thread(target=copy_context().run, args=(download,))
        thread.start()
        deadline = time.monotonic() + 3
        while not (data / "download-started").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (data / "download-started").exists()
        cancel.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and failures
        assert (
            json.loads(client.call_tool("get_settings", {}))["value"]["username"]
            == "test"
        )
        assert not list(cancelled_root.rglob("*.pdf"))
        deadline = time.monotonic() + 3
        while (
            list(cancelled_root.rglob(".elearning-*")) and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert not list(cancelled_root.rglob(".elearning-*"))
        assert files == {str(p): p.stat().st_mtime_ns for p in folder.rglob("*.pdf")}
    finally:
        client.shutdown()


def test_sync_output_permission_does_not_widen_the_persistent_mcp(tmp_path, native_settings_action):
    data, folder = tmp_path / 'data', tmp_path / 'output'
    folder.mkdir()
    client = McpClient(fake_config('elearning', data))
    try:
        assert client.initialize()
        client.call_tool('save_settings', {'payload': {'username': 'test', 'password': 'fake',
            'courses': ['42'], 'file_sync_root': str(folder)}})
        result = client.call_tool_result('sync_files', {})['structuredContent']['value']
        assert result['status'] == 'completed' and len(list(folder.rglob('*.pdf'))) == 1
        # Start a new output, bypass the Core call adapter, and exercise the
        # still-running control process's actual OS profile.
        second = tmp_path / 'second'
        second.mkdir()
        client.call_tool('save_file_sync_settings', {'root': str(second)})
        raw = client._send_request('tools/call', {'name': 'sync_files', 'arguments': {}})
        assert raw['result']['structuredContent']['value']['errors']
        assert not list(second.rglob('*.pdf'))
    finally:
        client.shutdown()


@pytest.mark.parametrize('revoke', [False, True])
def test_closing_parent_mcp_reaps_active_output_process(tmp_path, native_settings_action, revoke):
    import threading
    import time
    from contextvars import copy_context
    from core.security.runtime_trust import RuntimeTrustStore
    data, folder = tmp_path / 'data', tmp_path / 'output'
    folder.mkdir()
    client = McpClient(fake_config('elearning', data))
    failures = []
    def download():
        try:
            client.call_tool('sync_files', {})
        except Exception as error:
            failures.append(error)
    thread = threading.Thread(target=copy_context().run, args=(download,))
    try:
        assert client.initialize()
        client.call_tool('save_settings', {'payload': {'username': 'test', 'password': 'fake',
            'courses': ['42'], 'file_sync_root': str(folder)}})
        (data / 'slow-download').touch()
        thread.start()
        deadline = time.monotonic() + 4
        while not (data / 'download-started').exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (data / 'download-started').exists(), failures
        children = list(client._operation_clients)
        processes = [child._proc for child in children]
        assert len(processes) == 1 and processes[0].poll() is None
        if revoke:
            RuntimeTrustStore().set(**client._runtime_key, trusted=False)
        else:
            client.shutdown()
        thread.join(3)
        assert not thread.is_alive() and failures
        assert all(process.poll() is not None for process in processes)
        assert not client._operation_clients and not list(folder.rglob('*.pdf'))
    finally:
        client.shutdown()
        if thread.ident is not None:
            thread.join(3)


def test_mail_mcp_fetch_proposes_cursor_and_commit_survives_restart(tmp_path):
    config = fake_config("email", tmp_path / "data")
    client = McpClient(config)
    try:
        assert client.initialize()
        account = json.loads(
            client.call_tool(
                "save_account",
                {
                    "payload": {
                        "host": "imap.example.test",
                        "user": "test@example.test",
                        "password": "fake",
                    }
                },
            )
        )["value"]
        result = json.loads(
            client.call_tool(
                "fetch_messages",
                {"account_id": account["id"], "since": "2026-09-08T00:00:00Z"},
            )
        )["value"]
        assert result["items"] and result["cursors"]
        assert (
            json.loads(client.call_tool("get_cursors", {"account_id": account["id"]}))[
                "value"
            ]
            == {}
        )
        client.call_tool(
            "set_cursors", {"account_id": account["id"], "cursors": result["cursors"]}
        )
    finally:
        client.shutdown()
    client = McpClient(config)
    try:
        assert client.initialize()
        assert (
            json.loads(client.call_tool("get_cursors", {"account_id": account["id"]}))[
                "value"
            ]
            == result["cursors"]
        )
    finally:
        client.shutdown()


def test_verified_email_connector_preserves_legacy_environment_and_deleted_state(tmp_path, monkeypatch):
    config = server_config('email', tmp_path / 'email')
    config.hosted_connector = 'email'
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    config['args'].append('--hosted-connector')
    for name, value in {'IMAP_HOST': 'imap.example.test', 'IMAP_USER': 'owner@example.test',
                        'IMAP_PASSWORD': 'SYNTHETIC_LEGACY_SECRET'}.items():
        monkeypatch.setenv(name, value)
    client = McpClient(config)
    try:
        assert client.initialize()
        accounts = client.call_tool_result('list_accounts', {})['structuredContent']['value']
        assert accounts['configured'] and accounts['mailboxes'][0]['id'] == 'environment'
        assert 'SYNTHETIC_LEGACY_SECRET' not in str(accounts)
        client.call_tool('delete_account', {'account_id': 'environment'})
    finally:
        client.shutdown()
    restarted = McpClient(config)
    try:
        assert restarted.initialize()
        accounts = restarted.call_tool_result('list_accounts', {})['structuredContent']['value']
        assert accounts['mailboxes'] == [] and accounts['configured'] is False
    finally:
        restarted.shutdown()


@pytest.mark.parametrize("domain", ["email", "elearning"])
def test_old_connection_revocation_still_blocks_mcp_after_migration(domain):
    from agent.permission.gateway import Capability, SecurityGateway, register_tool

    name = "mcp_migrated_" + domain
    register_tool(name, Capability.EXTERNAL, connection="mcp:" + domain, read_only=True)
    policy = {
        "preset": "full-access",
        "rules": {},
        "connections": {domain: {"allowed": False}},
    }
    assert not SecurityGateway.check(
        "full-access", name, {}, policy=policy, create_pending=False
    ).allowed


@pytest.mark.parametrize(
    "method,params",
    [
        ("test_connection", {"payload": {}}),
        ("list_folders", {}),
        ("save_account", {"payload": {}}),
        ("list_accounts", {}),
    ],
)
def test_host_management_cannot_ignore_current_permission_policy(
    tmp_path, monkeypatch, method, params
):
    monkeypatch.setenv("STELLA_MANAGED_MCP", "1")
    from core.services import email_facade
    from agent.tools.tool_manager import ToolManager

    monkeypatch.setattr(
        email_facade,
        "_paths",
        lambda: (tmp_path / "email/email.db", tmp_path / "briefing/mailbox.json"),
    )
    import agent.runtime.tool_invocation as runtime

    monkeypatch.setattr(
        runtime, "permission_denial", lambda *a, **k: "Connection revoked"
    )
    manager = ToolManager()
    try:
        with pytest.raises(PermissionError, match="Connection revoked"):
            manager.call_mcp_tool("email", method, params, host=True)
    finally:
        manager._teardown_mcp_server("email")


def test_existing_revoked_legacy_grant_blocks_mcp(tmp_path, monkeypatch):
    monkeypatch.setenv("STELLA_MANAGED_MCP", "1")
    from core.plugins.authz import DEFAULT_AUTHZ
    from core.plugins.caller import user_grant_ref, plugin_user_id
    from core.services import elearning_facade
    from agent.tools.tool_manager import ToolManager

    monkeypatch.setattr(DEFAULT_AUTHZ, "_path", tmp_path / "authz.sqlite")
    ref = user_grant_ref("elearning-default-grant")
    DEFAULT_AUTHZ.issue(
        grant_ref=ref,
        plugin_id="stella.elearning",
        user_id=plugin_user_id(),
        capabilities=["elearning.read"],
        purposes=["any"],
    )
    DEFAULT_AUTHZ.revoke(ref)
    monkeypatch.setattr(
        elearning_facade,
        "_paths",
        lambda: (tmp_path / "elearning", tmp_path / "briefing"),
    )
    manager = ToolManager()
    try:
        with pytest.raises(PermissionError, match="revoked"):
            manager.call_mcp_tool("elearning", "get_settings", {}, host=True)
    finally:
        manager._teardown_mcp_server("elearning")


def test_frozen_mcp_command_uses_independent_packaged_server(tmp_path, monkeypatch):
    import sys
    from core.mcp import managed

    backend = tmp_path / "backend/stella-backend/stella-backend"
    backend.parent.mkdir(parents=True)
    backend.touch()
    standalone = tmp_path / "backend/elearning-mcp/elearning-mcp"
    standalone.parent.mkdir(parents=True)
    standalone.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(backend))
    from agent.tools.tool_manager import ToolManager

    monkeypatch.setenv("STELLA_MANAGED_MCP", "1")
    cfg = next(
        c for c in managed.configurations(ToolManager()) if c["name"] == "elearning"
    )
    assert cfg["command"] == str(standalone)
    assert cfg["args"][:2] == ["--hosted-connector", "--data-dir"]


def test_server_enforces_request_resource_scope(tmp_path):
    client = McpClient(fake_config("elearning", tmp_path / "data"))
    try:
        assert client.initialize()
        client.call_tool(
            "save_settings",
            {"payload": {"username": "test", "password": "fake", "courses": ["42"]}},
        )
        result = client._send_request(
            "tools/call",
            {
                "name": "list_files",
                "arguments": {},
                "_meta": {
                    "resourceScope": {
                        "userId": "test-user",
                        "resources": [{"kind": "course", "id": "other:43"}],
                    }
                },
            },
        )
        assert result["result"].get("isError") is True
    finally:
        client.shutdown()


@pytest.mark.parametrize("domain", ["email", "elearning"])
def test_independent_frozen_mcp_artifact_protocol(tmp_path, domain):
    from core.runtime.paths import bundle_digest
    executable = (
        ROOT / "Stella-Core/desktop/build/dist" / (domain + "-mcp") / (domain + "-mcp")
    )
    if not executable.is_file():
        pytest.skip("Independent Server build required for artifact smoke")
    config = ManagedMcpConfig({
        "name": domain,
        "type": "stdio",
        "command": str(executable),
        "args": ["--data-dir", str(tmp_path / domain)],
        "timeout": 90,
    }, data_root=tmp_path / domain, package_digest=bundle_digest(executable.parent))
    client = McpClient(config)
    try:
        assert client.initialize()
        assert len(client.list_tools()) >= 4
        reply = client.call_tool_result(
            "get_settings" if domain == "elearning" else "list_accounts", {}
        )
        assert isinstance(reply["structuredContent"]["value"], dict)
    finally:
        client.shutdown()


def test_legacy_scoped_grants_keep_purpose_and_actual_account_boundaries(
    tmp_path, monkeypatch
):
    import core.mcp.authorization as authorization
    from core.plugins.authz import PluginAuthorizationStore

    store = PluginAuthorizationStore(path=tmp_path / "auth.sqlite")
    monkeypatch.setattr(authorization, "DEFAULT_AUTHZ", store)
    store.issue_scoped(
        grant_ref="email-inbox",
        parent_ref="email-default-grant",
        plugin_id="stella.email",
        user_id="local-default",
        consumer_plugin_id="stella.email",
        capability="email.fetch",
        methods=["fetch_messages"],
        resources=[{"kind": "folder", "id": "account:INBOX"}],
        purpose="interactive",
    )
    scope = authorization.check(
        "email",
        "fetch_messages",
        {"account_id": "a"},
        "local-default",
        lambda *a: {"source_account_id": "account:INBOX"},
        "read",
    )
    assert scope["resourceScope"]["resources"] == [
        {"kind": "folder", "id": "account:INBOX"}
    ]
    with pytest.raises(PermissionError, match="scoped grant denied"):
        authorization.check(
            "email",
            "fetch_messages",
            {"account_id": "b"},
            "local-default",
            lambda *a: {"source_account_id": "account:Other"},
            "read",
        )
    with pytest.raises(PermissionError, match="background"):
        authorization.check(
            "email",
            "fetch_messages",
            {"account_id": "a"},
            "local-default",
            lambda *a: {"source_account_id": "account:INBOX"},
            "background",
        )


def test_plugin_call_is_not_confirmation_from_native_settings():
    from core.mcp.managed import native_user_action
    from core.plugins.caller import caller_origin_scope

    assert native_user_action(True, "save_account", None)
    with caller_origin_scope("agent"):
        assert not native_user_action(True, "save_account", None)
    with caller_origin_scope("scheduler"):
        assert not native_user_action(True, "save_account", None)


def test_mcp_authorization_probe_does_not_execute_the_business_tool(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STELLA_MANAGED_MCP", "1")
    from config import conf

    monkeypatch.setitem(conf(), "agent_permission_mode", "full-access")
    monkeypatch.setitem(
        conf(),
        "security_policy",
        {"preset": "full-access", "rules": {}, "connections": {}},
    )
    from core.services import email_facade
    from agent.tools.tool_manager import ToolManager

    monkeypatch.setattr(
        email_facade,
        "_paths",
        lambda: (tmp_path / "email/email.db", tmp_path / "legacy/mailbox.json"),
    )
    manager = ToolManager()
    try:
        email_facade.EmailMcpFacade().list_accounts()
        client = manager._mcp_registry._clients["email"]
        calls = []
        original = client.call_tool_result

        def observe(method, *args, **kwargs):
            calls.append(method)
            return original(method, *args, **kwargs)

        monkeypatch.setattr(client, "call_tool_result", observe)
        manager.call_mcp_tool(
            "email",
            "fetch_messages",
            {"account_id": "not-a-real-account"},
            check_only=True,
        )
        assert "fetch_messages" not in calls
        monkeypatch.setitem(
            conf(),
            "security_policy",
            {
                "preset": "full-access",
                "rules": {},
                "connections": {"mcp:email": {"allowed": False}},
            },
        )
        with pytest.raises(PermissionError, match="revoked"):
            manager.call_mcp_tool(
                "email",
                "fetch_messages",
                {"account_id": "not-a-real-account"},
                check_only=True,
            )
    finally:
        manager._teardown_mcp_server("email")

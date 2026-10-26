"""Managed dependency environments are independent and built under OS isolation."""

import json
from pathlib import Path
import sys

import pytest


def test_python_environments_share_base_but_do_not_inherit_core_packages(tmp_path):
    from core.runtime.environments import EnvironmentStore

    store = EnvironmentStore(tmp_path / "runtimes")
    one = store.prepare_python(
        kind="plugin",
        identifier="example.one",
        user="alice",
        package=tmp_path / "one",
        digest="a" * 64,
    )
    two = store.prepare_python(
        kind="mcp",
        identifier="example.two",
        user="alice",
        package=tmp_path / "two",
        digest="b" * 64,
    )
    assert one["base_id"] == two["base_id"]
    assert one["path"] != two["path"]
    assert one["python"] != sys.executable
    result = store.run(
        one,
        [
            "-I",
            "-c",
            'import importlib.util,json,sys; print(json.dumps([sys.prefix,importlib.util.find_spec("requests") is None]))',
        ],
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(Path(one["path"]) / "python"), True]
    assert (
        json.loads(
            (Path(one["path"]) / "python/pyvenv.cfg")
            .read_text()
            .split("include-system-site-packages = ")[1]
            .splitlines()[0]
            .lower()
        )
        is False
    )


def test_unlocked_dependencies_refused_without_touching_active_environment(tmp_path):
    from core.runtime.environments import EnvironmentStore

    store = EnvironmentStore(tmp_path / "runtimes")
    package = tmp_path / "package"
    package.mkdir()
    current = store.prepare_python(
        kind="plugin",
        identifier="example.one",
        user="alice",
        package=package,
        digest="a" * 64,
    )
    store.activate(current, validate=lambda candidate: True)
    (package / "requirements.txt").write_text("some-library>=1\n")
    with pytest.raises(ValueError, match="lock|locked"):
        store.prepare_python(
            kind="plugin",
            identifier="example.one",
            user="alice",
            package=package,
            digest="b" * 64,
        )
    assert store.current("plugin", "example.one", "alice")["id"] == current["id"]


def test_failed_validation_preserves_old_selection_and_private_business_data(tmp_path):
    from core.runtime.environments import EnvironmentStore

    store = EnvironmentStore(tmp_path / "runtimes")
    private = tmp_path / "business"
    private.mkdir()
    (private / "credentials").write_text("DO_NOT_REMOVE")
    old = store.prepare_python(
        kind="plugin",
        identifier="example.one",
        user="alice",
        package=tmp_path / "old",
        digest="a" * 64,
    )
    store.activate(old, validate=lambda candidate: True)
    new = store.prepare_python(
        kind="plugin",
        identifier="example.one",
        user="alice",
        package=tmp_path / "new",
        digest="b" * 64,
    )
    with pytest.raises(RuntimeError, match="validation"):
        store.activate(new, validate=lambda candidate: False)
    assert store.current("plugin", "example.one", "alice")["id"] == old["id"]
    assert (private / "credentials").read_text() == "DO_NOT_REMOVE"
    store.remove("plugin", "example.one", "alice")
    assert store.current("plugin", "example.one", "alice") is None
    assert (private / "credentials").read_text() == "DO_NOT_REMOVE"


def test_actual_isolated_build_cannot_read_owner_write_outside_or_network(tmp_path):
    from core.runtime.environments import EnvironmentStore

    package = tmp_path / "package"
    package.mkdir()
    secret = tmp_path / "owner.txt"
    secret.write_text("PRIVATE_OWNER")
    build = package / "build.py"
    build.write_text("""import json,socket,sys
from pathlib import Path
result={}
try: result['read']=Path(sys.argv[1]).read_text()
except OSError: result['read']='denied'
try: Path(sys.argv[2]).write_text('escape');result['write']='allowed'
except OSError: result['write']='denied'
try:
 s=socket.socket();s.bind(('127.0.0.1',0));result['network']='allowed';s.close()
except OSError: result['network']='denied'
Path('result.json').write_text(json.dumps(result))
""")
    store = EnvironmentStore(tmp_path / "runtimes")
    candidate = store.prepare_python(
        kind="plugin",
        identifier="example.build",
        user="alice",
        package=package,
        digest="c" * 64,
        build=["{python}", str(build), str(secret), str(tmp_path / "outside")],
    )
    result = json.loads((Path(candidate["path"]) / "build/result.json").read_text())
    assert result == dict(read="denied", write="denied", network="denied")
    assert not (tmp_path / "outside").exists()


def test_symlinked_runtime_storage_is_rejected(tmp_path):
    from core.runtime.environments import EnvironmentStore

    root = tmp_path / "runtimes"
    root.symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(PermissionError, match="symlink"):
        EnvironmentStore(root)


def test_plugin_worker_uses_its_managed_dependency_environment(tmp_path):
    import shutil
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry

    package = tmp_path / "plugin"
    shutil.copytree("samples/plugins/alpha", package)
    (package / "backend/main.py").write_text("""import sys

def handle(method, params):
    return {'status':'succeeded','sideEffects':'none','value':sys.prefix}
""")
    session = PluginSession(package, source_label="test", registry=PluginRegistry())
    try:
        session.start()
        prefix = session.invoke("ping", {})["value"]
        assert prefix != sys.prefix
        assert prefix == str(Path(session.environment["path"]) / "python")
        assert session.sandbox_state["mode"] == "isolated"
    finally:
        session.dispose()


def test_maintenance_worker_keeps_selected_environment_and_cannot_write_live_private_storage(tmp_path):
    import shutil
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    from core.plugins.storage import private_data_handle
    from core.runtime.environments import EnvironmentStore

    package = tmp_path / 'plugin'
    shutil.copytree('samples/plugins/alpha', package)
    store = EnvironmentStore()
    old = store.prepare_python(kind='plugin', identifier='stella.sample.alpha', user='alice',
                               package=package, digest='a'*64)
    store.activate(old, validate=lambda candidate: True)
    private = Path(private_data_handle('stella.sample.alpha', 'alice', ensure=True)['dataDir'])
    sentinel = private / 'kept.txt'
    sentinel.write_text('KEEP_PRIVATE_DATA')
    scratch = tmp_path / 'scratch'
    (package / 'backend/main.py').write_text(f'''from pathlib import Path
try:
    Path({str(sentinel)!r}).write_text('CORRUPTED')
except OSError:
    pass
def handle(method, params):
    return {{'status': 'succeeded', 'sideEffects': 'none', 'value': None}}
''')
    session = PluginSession(package, source_label='maintenance', registry=PluginRegistry(),
        user_id='alice', maintenance=True,
        data_handle={'namespace': 'stella.sample.alpha', 'dataDir': str(scratch)})
    try:
        session.start()
        runtime_storage = Path(session._storage_directory)
        assert sentinel.read_text() == 'KEEP_PRIVATE_DATA', 'Candidate changed live private storage'
        assert store.current('plugin', 'stella.sample.alpha', 'alice')['id'] == old['id']
    finally:
        session.dispose()
    assert not runtime_storage.exists(), 'Maintenance HOME/cache must be discarded after the worker exits'


def test_agent_file_tools_cannot_mutate_managed_runtime_policy_or_dependencies(
    monkeypatch, tmp_path
):
    from agent.permission.gateway import SecurityGateway, DecisionKind, Scope
    from core.security.run_policy import gateway_policy, resolve_run_policy
    from common.runtime_identity import identity_scope

    root = tmp_path / "state"
    root.mkdir()
    monkeypatch.setattr("common.state_dir.shared_root", lambda: root)
    target = root / "managed-runtimes/environments/owner/environment.json"
    policy = gateway_policy(
        resolve_run_policy("runtime-attacker", seed={"filesystem_mode": "full-access"})
    )
    with identity_scope(session_id="runtime-attacker"):
        decision = SecurityGateway.check(
            "full-access",
            "write",
            {"path": str(target), "content": "tampered"},
            cwd=str(tmp_path),
            scope=Scope(write_roots=(str(tmp_path),)),
            policy=policy,
        )
    assert decision.kind is DecisionKind.DENY


def test_node_environments_share_base_and_keep_dependencies_and_data_separate(tmp_path):
    import shutil

    if not shutil.which("node") or not shutil.which("npm"):
        pytest.skip("Node/npm unavailable")
    from core.runtime.environments import EnvironmentStore

    store = EnvironmentStore(tmp_path / "runtimes")
    package = tmp_path / "node-package"
    package.mkdir()
    (package / "package.json").write_text(
        json.dumps({"name": "isolated-test", "version": "1.0.0"})
    )
    (package / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "isolated-test",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {"": {"name": "isolated-test", "version": "1.0.0"}},
            }
        )
    )
    (package / "main.js").write_text(
        "console.log(JSON.stringify({cwd:process.cwd(),node:process.execPath, injected:process.env.NODE_OPTIONS||null}))"
    )
    monkey = pytest.MonkeyPatch()
    monkey.setenv("NODE_OPTIONS", "--require=/owner-secret")
    try:
        one = store.prepare_node(
            kind="mcp",
            identifier="example.node",
            user="alice",
            package=package,
            digest="d" * 64,
        )
        two = store.prepare_node(
            kind="mcp",
            identifier="example.node",
            user="bob",
            package=package,
            digest="d" * 64,
        )
        assert one["base_id"] == two["base_id"]
        assert one["path"] != two["path"]
        result = store.run(one, ["main.js"])
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["injected"] is None
        assert Path(json.loads(result.stdout)["cwd"]).is_relative_to(Path(one["path"]))
        store.activate(one, validate=lambda candidate: True)
        assert store.current("mcp", "example.node", "alice")["id"] == one["id"]
    finally:
        monkey.undo()


def test_node_rejects_unlocked_dependencies_and_symlinked_source(tmp_path):
    from core.runtime.environments import EnvironmentStore

    package = tmp_path / "node"
    package.mkdir()
    (package / "package.json").write_text(
        '{"name":"test","dependencies":{"escape":"*"}}'
    )
    store = EnvironmentStore(tmp_path / "runtimes")
    with pytest.raises(ValueError, match="lock"):
        store.prepare_node(
            kind="mcp",
            identifier="example.node",
            user="alice",
            package=package,
            digest="d" * 64,
        )
    (package / "package-lock.json").write_text("{}")
    (package / "linked.js").symlink_to(tmp_path / "outside.js")
    with pytest.raises(PermissionError, match="symlink"):
        store.prepare_node(
            kind="mcp",
            identifier="example.node",
            user="alice",
            package=package,
            digest="d" * 64,
        )


def test_local_python_mcp_uses_independent_environment(tmp_path):
    from agent.tools.mcp.mcp_client import McpClient

    package = tmp_path / "mcp"
    package.mkdir()
    script = package / "server.py"
    echo = Path("tests/fixtures/mcp_echo_server.py").read_text()
    script.write_text(echo.replace('text = args.get("text", "")', "text = sys.prefix"))
    client = McpClient(
        {"name": "environment-test", "command": sys.executable, "args": [str(script)]}
    )
    try:
        assert client.initialize()
        value = client.call_tool("echo", {"text": "ignored"}).removeprefix("echo:")
        assert value != sys.prefix
        assert client.environment["python"].startswith(client.environment["path"])
        assert value == str(Path(client.environment["path"]) / "python")
        from core.runtime.environments import EnvironmentStore

        assert (
            EnvironmentStore().current("mcp", "environment-test", "local-default")["id"]
            == client.environment["id"]
        )
    finally:
        client.shutdown()


@pytest.mark.parametrize(
    "command,args,env",
    [
        ("npx", ["unverified-package"], {}),
        (sys.executable, ["-c", 'print("ambiguous")'], {}),
        (sys.executable, ["server.py"], {"VENDOR_API_KEY": "do-not-pass"}),
    ],
)
def test_local_mcp_rejects_implicit_installers_ambiguous_entries_and_raw_credentials(
    tmp_path, command, args, env
):
    from core.security.mcp_runtime import prepare_mcp

    with pytest.raises(
        (ValueError, PermissionError), match="entry|credential|fixed|install"
    ):
        prepare_mcp(dict(name="untrusted", command=command, args=args), env)


def test_plain_password_environment_is_not_a_credential_guard_bypass(tmp_path):
    from core.security.mcp_runtime import prepare_mcp
    script = tmp_path / 'server.py'
    script.write_text('print("ordinary local MCP")')
    with pytest.raises(PermissionError, match='credential'):
        prepare_mcp(dict(name='ordinary', command=sys.executable, args=[str(script)]), {'PASSWORD': 'synthetic'})


def test_local_mcp_cannot_restore_arbitrary_host_environment_inheritance(monkeypatch):
    from agent.tools.mcp.mcp_client import McpClient
    monkeypatch.setenv('OWNER_AUTH_BLOB', 'SYNTHETIC_PRIVATE_HOST_VALUE')
    client = McpClient({'name': 'ordinary', 'inherit_full_env': True})
    environment = client._build_stdio_env({})
    assert 'OWNER_AUTH_BLOB' not in environment
    assert environment.get('PATH')


def test_email_environment_binding_does_not_receive_another_owners_token(monkeypatch):
    from agent.tools.tool_manager import ToolManager
    from core.mcp.managed import configurations
    monkeypatch.setenv('STELLA_MANAGED_MCP', '1')
    monkeypatch.setenv('CANVAS_TOKEN', 'SYNTHETIC_CANVAS_SECRET')
    config = next(row for row in configurations(ToolManager()) if row['name'] == 'email')
    assert 'SYNTHETIC_CANVAS_SECRET' not in str(config.get('env'))


def test_node_installs_verified_archives_offline_and_never_runs_implicit_scripts(
    tmp_path, monkeypatch
):
    import base64
    from hashlib import sha512
    import io
    import tarfile
    from core.runtime.environments import EnvironmentStore

    package = tmp_path / "node"
    package.mkdir()
    archive = tmp_path / "fixture.tgz"
    outside = tmp_path / "implicit-install-script"
    payloads = {
        "package/package.json": json.dumps(
            {
                "name": "fixture-public",
                "version": "1.0.0",
                "main": "index.js",
                "scripts": {
                    "postinstall": "node -e "
                    + json.dumps(
                        "require('fs').writeFileSync("
                        + repr(str(outside))
                        + ", 'escaped')"
                    )
                },
            }
        ).encode(),
        "package/index.js": b'module.exports="ISOLATED_DEPENDENCY";',
    }
    with tarfile.open(archive, "w:gz") as tar:
        for name, content in payloads.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    integrity = (
        "sha512-" + base64.b64encode(sha512(archive.read_bytes()).digest()).decode()
    )
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "consumer",
                "version": "1.0.0",
                "dependencies": {"fixture-public": "1.0.0"},
            }
        )
    )
    (package / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "consumer",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {
                    "": {
                        "name": "consumer",
                        "version": "1.0.0",
                        "dependencies": {"fixture-public": "1.0.0"},
                    },
                    "node_modules/fixture-public": {
                        "version": "1.0.0",
                        "resolved": "https://registry.npmjs.org/fixture-public/-/fixture-public-1.0.0.tgz",
                        "integrity": integrity,
                        "hasInstallScript": True,
                    },
                },
            }
        )
    )
    (package / "main.js").write_text('console.log(require("fixture-public"))')
    monkeypatch.setattr(
        "core.runtime.artifacts.npm_archives", lambda lock: [archive], raising=False
    )
    store = EnvironmentStore(tmp_path / "runtimes")
    candidate = store.prepare_node(
        kind="mcp",
        identifier="example.locked-node",
        user="alice",
        package=package,
        digest="e" * 64,
    )
    result = store.run(candidate, ["main.js"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ISOLATED_DEPENDENCY"
    assert not outside.exists()


def test_environment_selection_rejects_redirected_owner_namespace(tmp_path):
    import shutil
    from core.runtime.environments import EnvironmentStore
    store = EnvironmentStore(tmp_path / 'runtimes')
    record = store.prepare_python(kind='plugin', identifier='example.redirect', user='alice',
        package=tmp_path/'package', digest='a'*64)
    candidate = Path(record['path'])
    outside = tmp_path / 'outside'
    shutil.move(str(candidate.parent), outside)
    candidate.parent.symlink_to(outside, target_is_directory=True)
    altered = {**record, 'path': str(outside / candidate.name)}
    marker = outside / candidate.name / 'environment.json'
    marker.write_text(json.dumps(altered))
    with pytest.raises(PermissionError, match='storage|symlink|escaped'):
        store.activate(altered, validate=lambda candidate: True)
    assert not (outside / 'current.json').exists()


def test_plugin_contributions_are_not_visible_before_environment_selection(tmp_path, monkeypatch):
    import shutil
    import threading
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    from core.runtime.environments import EnvironmentStore
    package = tmp_path / 'plugin'
    shutil.copytree('samples/plugins/alpha', package)
    registry = PluginRegistry()
    session = PluginSession(package, source_label='test', registry=registry)
    entered, release = threading.Event(), threading.Event()
    activate = EnvironmentStore.activate
    def held_selection(self, record, *, validate):
        entered.set()
        assert release.wait(3)
        return activate(self, record, validate=validate)
    monkeypatch.setattr(EnvironmentStore, 'activate', held_selection)
    failures = []
    def start():
        try:
            session.start()
        except BaseException as error:
            failures.append(error)
    worker = threading.Thread(target=start)
    worker.start()
    try:
        assert entered.wait(3)
        assert registry.get(session.plugin_id) is None, 'Unselected environment published callable contributions'
    finally:
        release.set()
        worker.join(4)
        published = registry.get(session.plugin_id)
        session.dispose()
    assert not worker.is_alive() and failures == []
    assert published is not None


def test_locked_real_mcp_dependencies_install_offline_without_core_site_packages(tmp_path, monkeypatch, offline_runtime_artifacts):
    from core.runtime.environments import EnvironmentStore
    package = tmp_path / 'package'
    package.mkdir()
    import shutil
    shutil.copy2(Path('../MCP/email-mcp/requirements.lock'), package / 'requirements.lock')
    (package / 'pyproject.toml').write_text('[project]\nname="synthetic-mcp"\nversion="1.0.0"\n')
    store = EnvironmentStore(tmp_path / 'runtime')
    record = store.prepare_python(kind='mcp', identifier='locked-mcp', user='alice', package=package, digest='e'*64)
    result = store.run(record, ['-I','-c','import mcp,sys; print(mcp.__file__);print(sys.prefix)'])
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith(record['path'] + '/')
    assert result.stdout.splitlines()[1] != sys.prefix


def test_runtime_cache_does_not_fingerprint_local_virtualenv_or_build_products(tmp_path):
    from core.runtime.environments import EnvironmentStore
    package = tmp_path / 'source'
    package.mkdir()
    (package / 'main.py').write_text('print("source")')
    (package / '.venv').mkdir()
    dependency = package / '.venv' / 'unrelated.py'
    dependency.write_text('old local environment')
    store = EnvironmentStore(tmp_path / 'runtimes')
    one = store.prepare_python(kind='mcp', identifier='source-test', user='alice', package=package, digest='a'*64)
    dependency.write_text('different local environment')
    (package / 'dist').mkdir()
    (package / 'dist/artifact.py').write_text('stale generated code')
    two = store.prepare_python(kind='mcp', identifier='source-test', user='alice', package=package, digest='a'*64)
    assert two['id'] == one['id'], 'Local dependency/build files changed the immutable source identity'


def test_source_identity_rejects_linked_import_directory_without_sibling_files(tmp_path):
    from core.runtime.paths import source_digest
    package, dependency = tmp_path / 'package', tmp_path / 'external'
    package.mkdir()
    dependency.mkdir()
    (dependency / '__init__.py').write_text('VALUE = "unbound implementation"')
    (package / 'dependency').symlink_to(dependency, target_is_directory=True)
    with pytest.raises(PermissionError, match='symlink'):
        source_digest(package)


def test_frozen_bundle_identity_binds_internal_links_and_rejects_external_targets(tmp_path):
    from core.runtime.paths import bundle_digest
    package = tmp_path / 'bundle'
    package.mkdir()
    library = package / 'library.dylib'
    library.write_bytes(b'original dylib')
    alias = package / 'alias.dylib'
    alias.symlink_to('library.dylib')
    original = bundle_digest(package)
    library.write_bytes(b'modified dylib')
    assert bundle_digest(package) != original
    outside = tmp_path / 'outside.dylib'
    outside.write_bytes(b'external implementation')
    alias.unlink()
    alias.symlink_to(outside)
    with pytest.raises(PermissionError, match='outside'):
        bundle_digest(package)


@pytest.mark.parametrize('pin', ['missing', 'modified'])
def test_managed_mcp_private_storage_requires_core_pinned_implementation(tmp_path, pin):
    from core.mcp.managed import ManagedMcpConfig
    from core.runtime.paths import source_digest
    from core.security.mcp_runtime import prepare_mcp
    package = tmp_path / 'package'
    package.mkdir()
    script = package / 'server.py'
    script.write_text('print("original implementation")')
    config = ManagedMcpConfig(dict(name='counterfeit', command=sys.executable, args=[str(script)]),
                              data_root=tmp_path / 'private')
    if pin == 'modified':
        config.package_digest = source_digest(package)
        script.write_text('print("substituted implementation")')
    with pytest.raises(PermissionError, match='implementation'):
        prepare_mcp(config, {})


@pytest.mark.parametrize('domain,own_name,other_name', [
    ('email', 'mailbox.json', 'elearning.json'), ('elearning', 'elearning.json', 'mailbox.json'),
])
def test_managed_legacy_binding_does_not_expose_other_provider_credentials(tmp_path, monkeypatch, domain, own_name, other_name):
    import subprocess
    from agent.tools.tool_manager import ToolManager
    from core.mcp.managed import configurations
    from core.runtime.paths import source_digest
    from core.security.mcp_runtime import prepare_mcp
    from core.services import email_facade, elearning_facade
    monkeypatch.setenv('STELLA_MANAGED_MCP', '1')
    legacy = tmp_path / 'briefing'
    legacy.mkdir()
    (legacy / own_name).write_text('own legacy settings')
    (legacy / other_name).write_text('another provider secret')
    monkeypatch.setattr(email_facade, '_paths', lambda: (tmp_path / 'email/email.db', legacy / 'mailbox.json'))
    monkeypatch.setattr(elearning_facade, '_paths', lambda: (tmp_path / 'elearning', legacy))
    config = next(config for config in configurations(ToolManager()) if config['name'] == domain)
    package = tmp_path / 'package'
    package.mkdir()
    script = package / 'probe.py'
    script.write_text('''import json,sys
from pathlib import Path
values=[]
for value in sys.argv[1:]:
 try: values.append(Path(value).read_text())
 except OSError: values.append('denied')
print(json.dumps(values))
''')
    config.update(command=sys.executable, args=[str(script), str(legacy / own_name), str(legacy / other_name)])
    config.package_digest = source_digest(package)
    plan, _ = prepare_mcp(config, {})
    process = subprocess.run(plan.argv, env=plan.env, cwd=plan.cwd, capture_output=True, text=True, timeout=5)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == ['own legacy settings', 'denied']


def test_mcp_storage_binding_is_core_only_and_real_sandbox_stops_sibling_access(tmp_path):
    import subprocess
    from core.mcp.managed import ManagedMcpConfig
    from core.security.mcp_runtime import prepare_mcp
    from core.runtime.paths import source_digest
    package = tmp_path / 'package'
    package.mkdir()
    own, other = tmp_path / 'own', tmp_path / 'other'
    for path in (own, other):
        path.mkdir()
        (path / 'value.txt').write_text(path.name)
    script = package / 'probe.py'
    script.write_text('''import json,sys
from pathlib import Path
values=[]
for name in sys.argv[1:]:
 try: values.append(Path(name).read_text())
 except OSError: values.append('denied')
print(json.dumps(values))
''')
    config = dict(name='binding-probe', command=sys.executable,
                  args=[str(script),str(own/'value.txt'),str(other/'value.txt')],
                  data_root=str(own), write_roots=[str(other)], managed=True)
    for value, expected in [(config, ['denied', 'denied']),
                            (ManagedMcpConfig(config, data_root=own, package_digest=source_digest(package)), ['own', 'denied'])]:
        plan, _ = prepare_mcp(value, {})
        result = subprocess.run(plan.argv, env=plan.env, cwd=plan.cwd, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == expected

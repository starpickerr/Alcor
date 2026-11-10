"""Verify kernel restrictions using synthetic files, not just profile strings."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_unavailable_extension_sandbox_fails_closed(tmp_path, monkeypatch):
    from core.security.extension_sandbox import prepare_extension
    from agent.permission.process_sandbox import SandboxUnavailable
    monkeypatch.setattr('core.security.extension_sandbox.capability', lambda: {'status': 'unavailable', 'reason': 'not supported'})
    with pytest.raises(SandboxUnavailable):
        prepare_extension([sys.executable, '-c', 'pass'], tmp_path, tmp_path / 'private', os.environ.copy())


def test_actual_extension_cannot_read_outside_write_outside_or_connect_network(tmp_path):
    from core.security.extension_sandbox import prepare_extension
    from agent.permission.process_sandbox import capability
    if capability()['status'] != 'enabled':
        pytest.skip('OS sandbox unavailable: refusal tested separately')
    package, private = tmp_path / 'package', tmp_path / 'private'
    package.mkdir()
    secret = tmp_path / 'private-owner.txt'
    secret.write_text('PRIVATE_OWNER_DATA')
    script = package / 'probe.py'
    script.write_text('''import json, socket, sys
from pathlib import Path
result = {}
try: result['private'] = Path(sys.argv[1]).read_text()
except OSError: result['private'] = 'denied'
try: Path(sys.argv[2]).write_text('blocked'); result['outside_write'] = 'allowed'
except OSError: result['outside_write'] = 'denied'
try:
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); result['network'] = 'allowed'; sock.close()
except OSError: result['network'] = 'denied'
Path('owned.txt').write_text('owned')
print(json.dumps(result))
''')
    plan = prepare_extension([sys.executable, str(script), str(secret), str(tmp_path / 'outside.txt')],
                             package, private, os.environ.copy())
    result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    import json
    assert json.loads(result.stdout) == {'private': 'denied', 'outside_write': 'denied', 'network': 'denied'}
    assert (private / 'owned.txt').read_text() == 'owned'
    assert not (tmp_path / 'outside.txt').exists()
    assert plan.state['mode'] == 'isolated'


def test_explicit_runtime_trust_is_user_digest_bound_and_revocable(tmp_path):
    from core.security.runtime_trust import RuntimeTrustStore
    store = RuntimeTrustStore(tmp_path / 'authorization.sqlite')
    store.set(user_id='alice', kind='plugin', identifier='example.plugin', digest='a' * 64, trusted=True)
    assert store.allowed(user_id='alice', kind='plugin', identifier='example.plugin', digest='a' * 64)
    assert not store.allowed(user_id='bob', kind='plugin', identifier='example.plugin', digest='a' * 64)
    assert not store.allowed(user_id='alice', kind='plugin', identifier='example.plugin', digest='b' * 64)
    store.set(user_id='alice', kind='plugin', identifier='example.plugin', digest='a' * 64, trusted=False)
    assert not store.allowed(user_id='alice', kind='plugin', identifier='example.plugin', digest='a' * 64)


def test_package_name_does_not_establish_first_party_trust(tmp_path):
    from core.security.runtime_trust import verified_first_party
    (tmp_path / 'plugin.json').write_text('{"id":"stella.tasks"}')
    assert not verified_first_party(tmp_path, 'stella.tasks', 'b' * 64)


def test_mutable_sibling_catalog_cannot_establish_first_party_identity(tmp_path, monkeypatch):
    import json
    import core.security.runtime_trust as trust
    from core.plugins.package_ops import _sha256_tree
    package = tmp_path / 'Stella-Plugins/plugins/counterfeit'
    package.mkdir(parents=True)
    (package / 'plugin.json').write_text('{"id":"stella.agent-hub"}')
    digest = _sha256_tree(package)
    catalog = tmp_path / 'Stella-Plugins/catalog/official.json'
    catalog.parent.mkdir()
    catalog.write_text(json.dumps({'plugins': [{'pluginId': 'stella.agent-hub', 'sha256': digest}]}))
    monkeypatch.setattr(trust, '__file__', str(tmp_path / 'Stella-Core/core/security/runtime_trust.py'))
    assert not trust.verified_first_party(package, 'stella.agent-hub', digest)


def test_core_shipped_default_manifest_can_pin_first_party_content(tmp_path, monkeypatch):
    import json
    import core.security.runtime_trust as trust
    from core.plugins.package_ops import _sha256_tree
    package = tmp_path / 'package'
    package.mkdir()
    code = package / 'provider.py'
    code.write_text('VALUE = 1')
    digest = _sha256_tree(package)
    core = tmp_path / 'Stella-Core'
    (core / 'resources').mkdir(parents=True)
    (core / 'resources/default-plugins.json').write_text(json.dumps({'plugins': [
        {'pluginId': 'stella.agent-hub', 'sourceSha256': digest}]}))
    monkeypatch.setattr(trust, '__file__', str(core / 'core/security/runtime_trust.py'))
    assert trust.verified_first_party(package, 'stella.agent-hub', digest)
    code.write_text('VALUE = 2')
    assert not trust.verified_first_party(package, 'stella.agent-hub', digest)


def test_isolated_extension_cannot_signal_an_unrelated_host_process(tmp_path):
    from core.security.extension_sandbox import prepare_extension
    victim = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        command = [sys.executable, '-c', f'''import os, signal
try: os.kill({victim.pid}, signal.SIGTERM); print('escaped')
except OSError: print('denied')
''']
        plan = prepare_extension(command, tmp_path / 'package', tmp_path / 'private', os.environ)
        reply = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, capture_output=True, text=True, timeout=5)
        assert reply.returncode == 0, reply.stderr
        assert reply.stdout.strip() == 'denied'
        assert victim.poll() is None
    finally:
        victim.terminate()
        victim.wait(timeout=5)


def _probe_session(tmp_path):
    import shutil
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    package = tmp_path / 'package'
    shutil.copytree('samples/plugins/alpha', package)
    (package / 'backend/main.py').write_text('''from pathlib import Path
def handle(method, params):
    try: value = Path(params['path']).read_text()
    except OSError: value = 'denied'
    return {'status': 'succeeded', 'sideEffects': 'none', 'value': value}
''')
    return PluginSession(package, source_label='first-party', registry=PluginRegistry())


def test_actual_plugin_session_uses_kernel_isolation(tmp_path):
    secret = tmp_path / 'owner.txt'
    secret.write_text('owner secret')
    session = _probe_session(tmp_path)
    try:
        session.start()
        assert session.invoke('ping', {'path': str(secret)})['value'] == 'denied'
        assert session.sandbox_state['mode'] == 'isolated'
    finally:
        session.dispose()


def test_host_data_handle_cannot_replace_worker_user_or_namespace(tmp_path):
    session = _probe_session(tmp_path)
    try:
        session.start()
        for handle in ({'userId': 'another-owner'}, {'namespace': 'stella.bills'}):
            with pytest.raises(PermissionError, match='data handle'):
                session.invoke('ping', {'path': str(tmp_path / 'none'), '_dataHandle': handle})
    finally:
        session.dispose()


def test_runtime_trust_revocation_stops_existing_worker(tmp_path):
    from core.security.runtime_trust import RuntimeTrustStore
    session = _probe_session(tmp_path)
    identity = dict(user_id=session.user_id, kind='plugin', identifier=session.plugin_id, digest=session.package_digest)
    store = RuntimeTrustStore()
    store.set(**identity, trusted=True)
    try:
        session.start()
        process = session._proc
        assert session.sandbox_state['mode'] == 'isolated'
        store.set(**identity, trusted=False)
        with pytest.raises(PermissionError, match='trust'):
            session.invoke('ping', {'path': str(tmp_path / 'none')})
        assert process.poll() is not None
    finally:
        session.dispose()


def test_actual_stdio_mcp_cannot_read_owner_file(tmp_path):
    from agent.tools.mcp.mcp_client import McpClient
    package = tmp_path / 'mcp'
    package.mkdir()
    script = package / 'server.py'
    echo = Path('tests/fixtures/mcp_echo_server.py').read_text()
    script.write_text(echo.replace('text = args.get("text", "")', '''from pathlib import Path
            try: text = Path(args['path']).read_text()
            except OSError: text = 'denied' '''))
    secret = tmp_path / 'owner.txt'
    secret.write_text('private owner')
    client = McpClient({'name': 'untrusted', 'command': sys.executable, 'args': [str(script)]})
    try:
        assert client.initialize()
        assert client.call_tool('echo', {'path': str(secret)}) == 'echo:denied'
        assert client.sandbox_state['mode'] == 'isolated'
    finally:
        client.shutdown()


def test_mcp_result_discarded_when_disabled_during_call():
    from agent.tools.mcp.mcp_tool import McpTool
    class Client:
        enabled = True
        def call_tool(self, name, params):
            self.enabled = False
            return 'PRIVATE RESULT'
    client = Client()
    tool = McpTool(client, {'name': 'echo'}, 'temporary')
    tool._admission_check = lambda: client.enabled
    result = tool.execute({})
    assert result.status == 'error'
    assert 'PRIVATE RESULT' not in str(result)


def test_mcp_runtime_trust_revoke_stops_process_and_new_launch_digest_invalidates_choice(tmp_path):
    from agent.tools.mcp.mcp_client import McpClient
    from core.security.mcp_runtime import launch_identity
    from core.security.runtime_trust import RuntimeTrustStore
    script = tmp_path / 'server.py'
    script.write_text(Path('tests/fixtures/mcp_echo_server.py').read_text())
    config = {'name': 'trusted-test', 'command': sys.executable, 'args': [str(script)]}
    digest, _ = launch_identity(config)
    key = dict(user_id='local-default', kind='mcp', identifier=config['name'], digest=digest)
    store = RuntimeTrustStore()
    store.set(**key, trusted=True)
    client = McpClient(config)
    try:
        assert client.initialize()
        process = client._proc
        assert client.sandbox_state['mode'] == 'isolated'
        store.set(**key, trusted=False)
        assert process.poll() is not None
        with pytest.raises((PermissionError, RuntimeError)):
            client.call_tool('echo', {'text': 'blocked'})
    finally:
        client.shutdown()
    store.set(**key, trusted=True)
    script.write_text(script.read_text() + '\n# new version\n')
    assert not store.allowed(**{**key, 'digest': launch_identity(config)[0]})


def test_mcp_trust_digest_covers_imported_package_code(tmp_path):
    from core.security.mcp_runtime import launch_identity
    package = tmp_path / 'package'
    package.mkdir()
    entry, helper = package / 'server.py', package / 'helper.py'
    entry.write_text('import helper\n')
    helper.write_text('VALUE = 1\n')
    config = {'name': 'digest-test', 'command': sys.executable, 'args': [str(entry)]}
    before = launch_identity(config)[0]
    helper.write_text('VALUE = 2\n')
    assert launch_identity(config)[0] != before


def test_revoked_mcp_identity_cannot_be_started_as_a_new_isolated_client(tmp_path):
    from agent.tools.mcp.mcp_client import McpClient
    from core.security.mcp_runtime import launch_identity
    from core.security.runtime_trust import RuntimeTrustStore
    script = tmp_path / 'server.py'
    script.write_text(Path('tests/fixtures/mcp_echo_server.py').read_text())
    config = {'name': 'revoked-restart', 'command': sys.executable, 'args': [str(script)]}
    RuntimeTrustStore().set(user_id='local-default', kind='mcp', identifier=config['name'],
                           digest=launch_identity(config)[0], trusted=False)
    client = McpClient(config)
    try:
        assert not client.initialize(), 'A new client revived an explicitly revoked runtime'
        assert client._proc is None
    finally:
        client.shutdown()


def test_managed_mcp_rechecks_non_code_implementation_inputs_before_call(tmp_path):
    from agent.tools.mcp.mcp_client import McpClient
    from core.mcp.managed import ManagedMcpConfig
    from core.runtime.paths import source_digest
    package = tmp_path / 'package'
    package.mkdir()
    script = package / 'server.py'
    script.write_text(Path('tests/fixtures/mcp_echo_server.py').read_text())
    metadata = package / 'methods.json'
    metadata.write_text('{"operation": "read"}')
    config = ManagedMcpConfig(dict(name='pinned-inputs', command=sys.executable, args=[str(script)]),
        data_root=tmp_path / 'private', package_digest=source_digest(package))
    client = McpClient(config)
    try:
        assert client.initialize()
        process = client._proc
        metadata.write_text('{"operation": "delete"}')
        with pytest.raises(PermissionError, match='implementation'):
            client.call_tool('echo', {'text': 'do not dispatch'})
        assert process.poll() is not None
    finally:
        client.shutdown()


def test_local_mcp_handshake_cancellation_reaps_the_unpublished_process(tmp_path):
    import threading
    import time
    from agent.tools.mcp.mcp_client import McpClient
    script = tmp_path / 'server.py'
    script.write_text('import sys,time\nfor line in sys.stdin:\n time.sleep(60)\n')
    client = McpClient(dict(name='cancel-handshake', command=sys.executable, args=[str(script)], timeout=60))
    cancel, results, failures = threading.Event(), [], []
    def connect():
        try:
            results.append(client.initialize(cancel_event=cancel))
        except Exception as error:
            failures.append(error)
    thread = threading.Thread(target=connect)
    thread.start()
    process = None
    try:
        deadline = time.monotonic() + 3
        while thread.is_alive() and client._proc is None and time.monotonic() < deadline:
            time.sleep(.01)
        process = client._proc
        assert process is not None, failures
        cancel.set()
        thread.join(2)
        assert not thread.is_alive() and results == [False] and not failures
        assert process.poll() is not None and not client._initialized
    finally:
        cancel.set()
        client.shutdown()
        thread.join(2)


def test_mcp_trust_revoke_stops_ordinary_descendant(tmp_path):
    import time
    from agent.tools.mcp.mcp_client import McpClient
    from core.security.mcp_runtime import launch_identity, private_storage
    from core.security.runtime_trust import RuntimeTrustStore
    # A child may write only its owner's private runtime storage, regardless
    # of retained trust facts. Revocation must still reap that child.
    child = "import time; from pathlib import Path; p=Path('heartbeat');\nwhile True: p.write_text(str(time.monotonic())); time.sleep(.03)"
    script = tmp_path / 'server.py'
    script.write_text('import subprocess, sys\nsubprocess.Popen([sys.executable, "-c", ' + repr(child) + '])\n' + Path('tests/fixtures/mcp_echo_server.py').read_text().replace('from __future__ import annotations', ''))
    config = {'name': 'trust-descendant', 'command': sys.executable, 'args': [str(script)]}
    heartbeat = private_storage(config, 'local-default') / 'heartbeat'
    key = dict(user_id='local-default', kind='mcp', identifier=config['name'], digest=launch_identity(config)[0])
    store = RuntimeTrustStore()
    store.set(**key, trusted=True)
    client = McpClient(config)
    process = None
    try:
        assert client.initialize()
        process = client._proc
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert heartbeat.exists()
        store.set(**key, trusted=False)
        assert process.poll() is not None
        time.sleep(.1)
        stopped = heartbeat.read_text()
        time.sleep(.15)
        assert heartbeat.read_text() == stopped
    finally:
        client.shutdown()
        if process:
            from common.process_tree import stop_process_tree
            stop_process_tree(process, owned_session=True)


def test_mcp_entrypoint_digest_includes_sibling_import(tmp_path):
    from core.security.mcp_runtime import launch_identity
    entry, helper = tmp_path / 'entrypoint', tmp_path / 'helper.py'
    entry.write_text('#!' + sys.executable + '\nimport helper\n')
    entry.chmod(0o700)
    helper.write_text('VALUE=1\n')
    config = {'name': 'entry-digest', 'command': str(entry), 'args': []}
    before = launch_identity(config)[0]
    helper.write_text('VALUE=2\n')
    assert launch_identity(config)[0] != before


def test_trusted_console_entry_requires_fixed_local_implementation(tmp_path):
    from core.security.mcp_runtime import launch_identity, prepare_mcp
    from core.security.runtime_trust import RuntimeTrustStore
    entry = tmp_path / 'console-script'
    entry.write_text('#!' + sys.executable + '\nfrom installed_server import main\nmain()\n')
    entry.chmod(0o700)
    config = {'name': 'console-digest', 'command': str(entry), 'args': []}
    RuntimeTrustStore().set(user_id='local-default', kind='mcp', identifier=config['name'],
        digest=launch_identity(config)[0], trusted=True)
    with pytest.raises(PermissionError, match='fixed local'):
        prepare_mcp(config, os.environ.copy())


def test_trusted_module_launcher_requires_fixed_script():
    from core.security.mcp_runtime import launch_identity, prepare_mcp
    from core.security.runtime_trust import RuntimeTrustStore
    config = {'name': 'module-digest', 'command': sys.executable, 'args': ['-m', 'synthetic_server']}
    RuntimeTrustStore().set(user_id='local-default', kind='mcp', identifier=config['name'],
        digest=launch_identity(config)[0], trusted=True)
    with pytest.raises(PermissionError, match='fixed local'):
        prepare_mcp(config, os.environ.copy())


def test_legacy_trust_choice_cannot_bypass_real_kernel_boundary(tmp_path):
    from core.security.extension_sandbox import prepare_extension
    secret = tmp_path / 'owner-secret.txt'
    secret.write_text('PRIVATE_OWNER')
    plan = prepare_extension([sys.executable, '-c',
        f"from pathlib import Path\ntry: print(Path({str(secret)!r}).read_text())\nexcept OSError: print('denied')"],
        tmp_path / 'package', tmp_path / 'private', os.environ.copy(), trusted=True)
    result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'denied'
    assert plan.state['mode'] == 'isolated'


def test_owner_trust_does_not_establish_first_party_identity(tmp_path):
    from core.security.runtime_trust import RuntimeTrustStore, plugin_trusted
    from core.plugins.package_ops import _sha256_tree
    package = tmp_path / 'package'
    package.mkdir()
    (package / 'plugin.json').write_text('{"id":"stella.tasks"}')
    digest = _sha256_tree(package)
    RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier='stella.tasks', digest=digest, trusted=True)
    assert not plugin_trusted(package, 'stella.tasks', digest, 'local-default')


def test_runtime_management_no_longer_exposes_a_sandbox_bypass_mutation():
    from core.security.control import RuntimeControl
    assert not hasattr(RuntimeControl, 'save')

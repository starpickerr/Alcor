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
        assert session.sandbox_state['mode'] == 'trusted'
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
        assert client.sandbox_state['mode'] == 'trusted'
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

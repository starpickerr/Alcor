"""Integration regressions discovered while consolidating the Agent Hub branch."""
import time
from pathlib import Path

import pytest

from agent.tools.agent_hub.agent_hub_tool import AgentHubTool
from core.execution.artifacts import seal_artifact
from core.execution.workspace_lease import allocate_workspace_lease
from core.services import agent_hub_gateway


@pytest.fixture(autouse=True)
def isolated_hub(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLA_AGENT_HUB_PLUGIN', '1')
    monkeypatch.setattr('common.state_dir.agent_hub_db', lambda **_: tmp_path / 'hub.db')
    monkeypatch.setattr('common.state_dir.executions_dir', lambda **_: tmp_path / 'executions')
    monkeypatch.setenv('STELLA_AGENT_HUB_PLUGIN_ROOT', str(Path('stella-plugins-workspace/plugins/agent-hub').resolve()))
    monkeypatch.setenv('STELLA_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(agent_hub_gateway, 'apply_user_preferences', lambda: {})
    yield
    agent_hub_gateway.shutdown_agent_hub_session()


def test_status_lookup_does_not_recover_a_live_execution():
    tool = AgentHubTool()
    service = tool._execution_service()
    row = service.create(owner_plugin_id='stella.agent-hub', task_kind='test', goal='running',
                         budget={'maxWallTime': 10, 'maxAttempts': 1, 'maxConcurrent': 1})
    service.transition(row['executionId'], 'running')
    assert AgentHubTool()._status(row['executionId'])['execution']['status'] == 'running'


@pytest.mark.parametrize('identifier', ['../escaped', '/escaped-task'])
def test_workspace_id_cannot_escape_root(tmp_path, identifier):
    with pytest.raises((ValueError, PermissionError)):
        allocate_workspace_lease(work_root=tmp_path / 'work', execution_id=identifier, attempt_id='attempt')


def test_artifact_id_cannot_escape_store(tmp_path):
    source = tmp_path / 'result.txt'
    source.write_text('result')
    with pytest.raises((ValueError, PermissionError)):
        seal_artifact(store_root=tmp_path / 'artifacts', source_path=source, execution_id='../escaped')


def test_cli_submit_is_nonblocking_and_observation_retains_run(tmp_path):
    import sys
    fake = tmp_path / 'codex'
    fake.write_text(f'#!{sys.executable}\nimport time,sys\nif "--skip-git-repo-check" not in sys.argv: sys.exit(42)\ntime.sleep(1)\nprint("completed")\n')
    fake.chmod(0o755)
    config = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(fake), 'settings': {}}})
    tool = AgentHubTool()
    started = time.monotonic()
    result = tool.execute(action='submit', goal='test', provider_config_id=config['providerConfigId'])
    elapsed = time.monotonic() - started
    assert result.status == 'success', result.result
    assert elapsed < 0.75, f'submit blocked for {elapsed:.2f}s'
    eid = result.result['executionId']
    deadline = time.monotonic() + 5
    row = None
    while time.monotonic() < deadline:
        row = tool._status(eid)['execution']
        if row['status'] in {'succeeded', 'failed'}:
            break
        time.sleep(0.05)
    assert row['status'] == 'succeeded', row
    assert row['artifactRefs']
    assert 'completed' in tool._status(eid)['artifacts'][0]['text']


def test_delegation_is_an_external_capability_and_respects_read_only():
    from agent.permission.gateway import SecurityGateway
    assert SecurityGateway.check('full-access', 'agent_hub', {'action': 'submit', 'goal': 'task'})
    assert not SecurityGateway.check('read-only', 'agent_hub', {'action': 'submit', 'goal': 'task'})


def test_external_cli_inherits_parent_filesystem_ceiling(tmp_path, monkeypatch):
    import json
    import sys
    from common.runtime_identity import identity_scope
    from core.security.run_policy import save_run_policy
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-host-secret')
    secret = tmp_path / 'owner-private.txt'
    secret.write_text('OWNER_PRIVATE')
    outside = tmp_path / 'outside.txt'
    fake = tmp_path / 'codex'
    fake.write_text(f'''#!{sys.executable}
import json, os
from pathlib import Path
result = {{'host_secret': 'OPENAI_API_KEY' in os.environ}}
try: result['read'] = Path({str(secret)!r}).read_text()
except OSError: result['read'] = 'denied'
try: Path({str(outside)!r}).write_text('escaped'); result['write'] = 'allowed'
except OSError: result['write'] = 'denied'
print(json.dumps(result))
''')
    fake.chmod(0o755)
    config = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(fake), 'settings': {}}})
    save_run_policy('session', 'parent', {'filesystem_mode': 'read-only', 'capabilities': {
        'plugin:stella.agent-hub': {'read': 'allow', 'write': 'allow', 'system_execute': 'allow'}}})
    with identity_scope(session_id='parent'):
        tool = AgentHubTool()
        result = tool.execute(action='submit', goal='probe', provider_config_id=config['providerConfigId'])
        assert result.status == 'success', result.result
        eid = result.result['executionId']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = tool._status(eid)
            if status['execution']['status'] in {'succeeded', 'failed'}:
                break
            time.sleep(.02)
        assert status['execution']['status'] == 'succeeded', status
        assert json.loads(status['artifacts'][0]['text']) == {'read': 'denied', 'write': 'denied', 'host_secret': False}
    assert not outside.exists()


@pytest.mark.parametrize('local_id', ['codex-app-server', 'cursor-acp'])
def test_unfinished_protocol_modes_are_blocked_not_ready(local_id, tmp_path):
    import sys
    row = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': local_id, 'enabled': True, 'binaryPath': sys.executable, 'settings': {}}})
    probe = agent_hub_gateway.invoke_ok('probe_provider', {'provider_config_id': row['providerConfigId']})
    assert probe['status'] == 'blocked'
    assert all(probe['capabilities'][key] is False for key in
               ['streaming', 'persistentSession', 'statusQuery', 'artifacts'])
    result = AgentHubTool().execute(action='submit', goal='must not run', provider_config_id=row['providerConfigId'])
    start = result.result
    assert start['status'] == 'failed'
    assert start['error']['code'] == 'unsupported_protocol_execution'


def _process_alive(pid):
    import subprocess
    result = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True)
    state = result.stdout.strip()
    return bool(state) and not state.startswith('Z')


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX process groups')
def test_supervisor_revokes_descendants_after_leader_exit(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from core.execution.process_supervisor import ProcessSupervisor
    code = 'import subprocess,sys; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); print(p.pid,flush=True)'
    proc = subprocess.Popen([sys.executable, '-c', code], start_new_session=True,
                            stdout=subprocess.PIPE, text=True)
    child = int(proc.stdout.readline())
    proc.wait(timeout=3)
    sup = ProcessSupervisor()
    sup.register('lease', proc)
    try:
        assert _process_alive(child)
        sup.revoke('lease')
        deadline = time.monotonic() + 2
        while _process_alive(child) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _process_alive(child)
    finally:
        try:
            os.kill(child, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX process groups')
def test_cli_completion_does_not_leave_descendants(tmp_path):
    import os
    import signal
    import sys
    fake = tmp_path / 'codex'
    fake.write_text(f'#!{sys.executable}\nimport subprocess,sys\np=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])\nopen("child.pid","w").write(str(p.pid))\nprint("complete")\n')
    fake.chmod(0o755)
    row = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(fake), 'settings': {}}})
    tool = AgentHubTool()
    result = tool.execute(action='submit', goal='test', provider_config_id=row['providerConfigId'])
    assert result.status == 'success', result.result
    pid_file = Path(tool._execution_service().get(result.result['executionId'])['workspacePath']) / 'child.pid'
    child = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = tool._status(result.result['executionId'])['execution']['status']
            if status in {'succeeded', 'failed'} and pid_file.exists():
                break
            time.sleep(0.03)
        child = int(pid_file.read_text())
        assert not _process_alive(child)
    finally:
        if child:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX session ownership')
def test_worker_crash_reaps_owned_cli_groups(tmp_path):
    import json
    import os
    import signal
    import sys
    fake = tmp_path / 'codex'
    fake.write_text(f'#!{sys.executable}\nimport subprocess,sys,os,json,time\np=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])\nopen("children.json","w").write(json.dumps([os.getpid(),p.pid]))\ntime.sleep(30)\n')
    fake.chmod(0o755)
    row = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(fake), 'settings': {}}})
    result = AgentHubTool().execute(action='submit', goal='test', provider_config_id=row['providerConfigId'])
    assert result.status == 'success'
    pid_file = Path(AgentHubTool()._execution_service().get(result.result['executionId'])['workspacePath']) / 'children.json'
    pids = []
    try:
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pids = json.loads(pid_file.read_text())
        session = agent_hub_gateway.ensure_agent_hub_session()
        session._proc.kill()
        session._proc.wait(timeout=3)
        deadline = time.monotonic() + 3
        while any(_process_alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not any(_process_alive(pid) for pid in pids)
    finally:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_provider_cannot_start_without_a_core_execution_lease(tmp_path):
    row = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(tmp_path / 'missing'), 'settings': {}}})
    with pytest.raises((PermissionError, ValueError), match='execution|lease'):
        agent_hub_gateway.invoke_ok('provider_start', {'provider_config_id': row['providerConfigId'],
            'task': {'executionId': 'unowned', 'workspacePath': str(tmp_path), 'goal': 'reject'}})


def test_lost_start_reply_keeps_execution_id_and_marks_unknown(tmp_path, monkeypatch):
    row = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(tmp_path / 'missing'), 'settings': {}}})
    real = agent_hub_gateway.invoke_ok

    def lose_reply(method, *args, **kwargs):
        if method == 'provider_start':
            raise RuntimeError('lost RPC reply')
        return real(method, *args, **kwargs)

    monkeypatch.setattr(agent_hub_gateway, 'invoke_ok', lose_reply)
    result = AgentHubTool().execute(action='submit', goal='test', provider_config_id=row['providerConfigId'])
    assert result.status == 'success', result.result
    assert result.result['executionId']
    assert result.result['status'] == 'outcome_unknown'
    assert AgentHubTool()._status(result.result['executionId'])['execution']['status'] == 'outcome_unknown'


def test_artifact_reference_resolves_after_reopening_and_is_execution_scoped(tmp_path):
    from core.execution.artifacts import resolve_artifact
    source = tmp_path / 'result.txt'
    source.write_text('persistent result')
    artifact = seal_artifact(store_root=tmp_path / 'store', source_path=source, execution_id='exec-1')
    reopened = resolve_artifact(store_root=tmp_path / 'store', artifact_ref=artifact['artifactRef'], execution_id='exec-1')
    assert Path(reopened['path']).read_text() == 'persistent result'
    with pytest.raises(FileNotFoundError):
        resolve_artifact(store_root=tmp_path / 'store', artifact_ref=artifact['artifactRef'], execution_id='other-execution')


@pytest.mark.parametrize('selector', [{'provider_local_id': 'codex-cli'}, {'agent_id': 'codex', 'access_mode': 'cli'}, {}])
def test_once_approved_public_selector_starts_real_provider(tmp_path, selector):
    import sys
    from common.runtime_identity import identity_scope
    from core.security.run_policy import save_run_policy
    from agent.runtime.tool_invocation import permission_denial, invocation_scope
    from agent.permission.gateway import SecurityGateway
    fake = tmp_path / 'codex'
    fake.write_text(f'#!{sys.executable}\nimport sys\nprint("codex 1" if "--version" in sys.argv else "completed")\n')
    fake.chmod(0o755)
    config = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(fake), 'settings': {}}})
    session_id = 'hub-once-' + '-'.join(selector.keys())
    save_run_policy('session', session_id, {'filesystem_mode': 'read-only', 'capabilities': {
        'plugin:stella.agent-hub': {'read': 'allow', 'write': 'allow', 'system_execute': 'confirm'}}})
    args = {'action': 'submit', 'goal': 'synthetic goal', 'acceptance': {'checks': ['completed']}, **selector}
    with identity_scope(session_id=session_id):
        assert permission_denial('agent_hub', args)
        pending = next(row for row in SecurityGateway.pending() if row['session'] == session_id)
        assert SecurityGateway.approve(pending['id'])
        assert permission_denial('agent_hub', args) is None
        with invocation_scope('hub-approved'):
            result = AgentHubTool().execute(**args)
        assert result.result.get('providerConfigId') == config['providerConfigId'], result.result
        assert result.result['status'] in {'running', 'succeeded'}, result.result
        assert not [row for row in SecurityGateway.pending() if row['session'] == session_id]


@pytest.mark.parametrize('changed', [
    {'goal': 'another goal'}, {'provider_local_id': 'claude-code'},
    {'acceptance': {'checks': ['different']}}, {'acceptance': {'checks': ['same', 'extra']}},
])
def test_hub_once_receipt_rejects_changed_public_request(changed):
    from common.runtime_identity import identity_scope
    from core.security.run_policy import save_run_policy
    from agent.runtime.tool_invocation import permission_denial, invocation_scope, _current_authorization
    from agent.permission.gateway import SecurityGateway
    from core.security.approvals import inherited
    from core.security.capabilities import describe_tool
    args = {'action': 'submit', 'goal': 'original', 'provider_local_id': 'codex-cli', 'acceptance': {'checks': ['same']}}
    save_run_policy('session', 'hub-changed', {'filesystem_mode': 'read-only', 'capabilities': {
        'plugin:stella.agent-hub': {'system_execute': 'confirm'}}})
    with identity_scope(session_id='hub-changed'):
        assert permission_denial('agent_hub', args)
        pending = next(row for row in SecurityGateway.pending() if row['session'] == 'hub-changed')
        assert SecurityGateway.approve(pending['id'])
        assert permission_denial('agent_hub', args) is None
        with invocation_scope('hub-changed'):
            binding = _current_authorization(None, None, 'hub-changed', None, False)[2]
            assert not inherited(describe_tool('agent_hub', args), binding, {**args, **changed})


def test_hub_derived_once_approval_binds_provider_and_complete_payload():
    from copy import deepcopy
    from common.runtime_identity import identity_scope
    from core.security.run_policy import save_run_policy
    from agent.runtime.tool_invocation import permission_denial, invocation_scope, _current_authorization
    from agent.permission.gateway import SecurityGateway
    from core.security.approvals import agent_hub_operation_scope, inherited
    from core.security.capabilities import describe_tool
    args = {'action': 'submit', 'goal': 'approved', 'provider_local_id': 'codex-cli'}
    save_run_policy('session', 'hub-payload', {'filesystem_mode': 'read-only', 'capabilities': {
        'plugin:stella.agent-hub': {'system_execute': 'confirm'}}})
    payload = {'provider_config_id': 'approved-provider', 'task': {'goal': 'approved', 'acceptance': {'checks': ['one']}}}
    with identity_scope(session_id='hub-payload'):
        assert permission_denial('agent_hub', args)
        request = next(row for row in SecurityGateway.pending() if row['session'] == 'hub-payload')
        assert SecurityGateway.approve(request['id'])
        assert permission_denial('agent_hub', args) is None
        with invocation_scope('hub-payload'):
            descriptor = describe_tool('agent_hub', args)
            binding = _current_authorization(None, None, 'hub-payload', None, False)[2]
            with agent_hub_operation_scope(args, 'provider_start', payload):
                assert inherited(descriptor, binding, {'method': 'provider_start', 'input': payload})
                changed = deepcopy(payload)
                changed['provider_config_id'] = 'other-provider'
                assert not inherited(descriptor, binding, {'method': 'provider_start', 'input': changed})
                changed = deepcopy(payload)
                changed['task']['acceptance']['checks'].append('extra')
                assert not inherited(descriptor, binding, {'method': 'provider_start', 'input': changed})
                assert not inherited(descriptor, binding, {'method': 'other', 'input': payload})
                save_run_policy('session', 'hub-payload', {'capabilities': {}})
                changed_binding = _current_authorization(None, None, 'hub-payload', None, False)[2]
                assert not inherited(descriptor, changed_binding, {'method': 'provider_start', 'input': payload})
            assert not inherited(descriptor, binding, {'method': 'provider_start', 'input': payload})


@pytest.mark.parametrize('stage,expected', [('start', 'failed'), ('observe', 'outcome_unknown')])
def test_hub_permission_failure_preserves_whether_provider_was_started(tmp_path, monkeypatch, stage, expected):
    import sys
    fake = tmp_path / 'codex'
    fake.write_text(f'#!{sys.executable}\nprint("synthetic codex")\n')
    fake.chmod(0o755)
    config = agent_hub_gateway.invoke_ok('upsert_provider', {'provider': {
        'providerLocalId': 'codex-cli', 'enabled': True, 'binaryPath': str(fake), 'settings': {}}})
    tool = AgentHubTool()
    if stage == 'start':
        original = agent_hub_gateway.invoke_ok
        def before_dispatch(method, *args, **kwargs):
            if method == 'provider_start':
                raise PermissionError('revoked before dispatch')
            return original(method, *args, **kwargs)
        monkeypatch.setattr(agent_hub_gateway, 'invoke_ok', before_dispatch)
    else:
        def after_start(*args):
            raise PermissionError('result authorization failed after actual start')
        monkeypatch.setattr(tool, '_observe_execution', after_start)
    result = tool.execute(action='submit', goal='state probe', provider_config_id=config['providerConfigId'])
    assert result.result['status'] == expected, result.result

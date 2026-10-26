"""Dependency rebuilds exercise real isolated MCP processes and retained state."""
import json
from pathlib import Path
import sys

import pytest

from agent.tools.mcp.mcp_client import McpClient
from agent.tools.mcp.mcp_tool import McpTool
from agent.tools.tool_manager import ToolManager
from common.runtime_identity import identity_scope
from core.mcp.managed import ManagedMcpConfig
from core.runtime.environments import EnvironmentStore
from core.runtime.paths import source_digest
from core.security.mcp_runtime import environment_identity


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr('common.state_dir.shared_root', lambda: tmp_path / 'state')
    monkeypatch.setattr(ToolManager, '_instances', {})
    package = tmp_path / 'package'
    package.mkdir()
    script = package / 'server.py'
    script.write_text('''import json,sys,os
from pathlib import Path
root=Path(sys.argv[sys.argv.index('--data-dir')+1])
root.mkdir(parents=True,exist_ok=True)
marker=root/'starts'
marker.write_text(str(int(marker.read_text())+1) if marker.exists() else '1')
version='1.0.0'
if os.environ.get('AUDIT_OWNER_FILE'):
 try:
  Path(os.environ['AUDIT_OWNER_FILE']).read_bytes();version='leaked'
 except OSError: version='denied'
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request: continue
 method=request['method']
 if method=='initialize':
  result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'probe','version':version}}
 elif method=='tools/list':
  result={'tools':[{'name':'info','description':'Read test state','inputSchema':{'type':'object','properties':{}},'annotations':{'readOnlyHint':True}}]}
  if os.environ.get('REJECT_VALIDATION') and root.name=='business': result={'tools':'invalid'}
 else:
  result={'content':[{'type':'text','text':json.dumps({'prefix':sys.prefix,'starts':marker.read_text()})}]}
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
''')
    configs = {}
    clients = []

    def make(user='alice', enabled=True):
        with identity_scope(user_id=user):
            manager = ToolManager()
            data = tmp_path / ('business-' + user)
            config = ManagedMcpConfig({'name': 'probe', 'type': 'stdio', 'command': sys.executable,
                'args': [str(script), '--data-dir', str(data)], 'enabled': enabled, 'timeout': 3,
                'agentTools': ['info']}, data_root=data, package_digest=source_digest(package))
            configs[user] = config
            monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [configs[user]])
            return manager, config, data

    def start(config):
        client = McpClient(config)
        clients.append(client)
        assert client.initialize()
        return client

    yield make, start, configs
    for client in clients:
        client.shutdown()
    for manager in ToolManager.instances():
        manager.shutdown_mcp()


def test_rebuild_validates_fresh_process_without_touching_business_and_selects_next_environment(setup):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, data = make()
    with identity_scope(user_id='alice'):
        old = start(config)
        old_environment = old.environment
        (data / 'kept').write_text('ACCOUNT_AND_BUSINESS')
        manager.capability_settings.set_enabled('tool', McpTool.name_for('probe', 'info'), False)
        result = McpLifecycle(manager).rebuild('probe')
        assert result['stage'] == 'completed'
        assert old._proc is None
        selected = EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config))
        assert selected['id'] != old_environment['id']
        assert (data / 'starts').read_text() == '1'
        assert (data / 'kept').read_text() == 'ACCOUNT_AND_BUSINESS'
        assert manager._mcp_enabled(config)
        assert not manager.capability_settings.enabled('tool', McpTool.name_for('probe', 'info'))
        fresh = start(config)
        assert fresh.environment == selected
        assert json.loads(fresh.call_tool('info', {})) == {
            'prefix': str(Path(selected['path']) / 'python'), 'starts': '2'}
        assert Path(old_environment['path']).is_dir()


def test_failed_candidate_keeps_active_process_and_original_environment(setup, monkeypatch):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, data = make()
    prepare = EnvironmentStore.prepare_python
    candidates = []
    def broken(store, **values):
        record = prepare(store, **values)
        if values.get('rebuild'):
            executable = Path(record['python'])
            executable.unlink()
            executable.symlink_to('/usr/bin/false')
            candidates.append(record)
        return record
    with identity_scope(user_id='alice'):
        old = start(config)
        pointer = Path(old.environment['path']).parent / 'current.json'
        before = pointer.read_bytes()
        monkeypatch.setattr(EnvironmentStore, 'prepare_python', broken)
        with pytest.raises(RuntimeError, match='validation'):
            McpLifecycle(manager).rebuild('probe')
        assert old._proc is not None and old._proc.poll() is None
        assert pointer.read_bytes() == before
        assert json.loads(old.call_tool('info', {}))['starts'] == '1'
        assert (data / 'starts').read_text() == '1'
        assert candidates and json.loads((Path(candidates[0]['path']) / 'environment.json').read_text())['state'] == 'failed'


def test_rebuild_only_stops_matching_owner_and_keeps_disabled_intent(setup):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, data = make(enabled=False)
    _, other_config, other_data = make(user='bob')
    with identity_scope(user_id='bob'):
        other = start(other_config)
    with identity_scope(user_id='alice'):
        operation = McpLifecycle(manager).rebuild('probe')
        assert operation['stage'] == 'completed'
        assert not manager._mcp_enabled(config)
        assert not data.exists()  # candidate validation cannot initialize the real account directory
        assert other._proc is not None and other._proc.poll() is None
        assert other_data.exists()
        assert EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config)) is not None


@pytest.mark.parametrize('stage', ['prepared', 'draining', 'selected'])
def test_rebuild_recovers_persisted_stage_without_changing_tool_intent(setup, stage):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, data = make()
    class Interrupted(BaseException):
        pass
    def interrupt(current, operation):
        if current == stage:
            raise Interrupted()
    with identity_scope(user_id='alice'):
        old = start(config)
        service = McpLifecycle(manager)
        with pytest.raises(Interrupted):
            service.rebuild('probe', checkpoint=interrupt)
        pending = service.pending('probe')
        assert len(pending) == 1 and pending[0]['stage'] == stage
        if stage == 'draining':
            with pytest.raises(PermissionError, match='rebuild|maintenance'):
                old.call_tool('info', {})
            denied = McpClient({**config, 'maintenance': True, 'rebuild_environment': True,
                                'maintenance_storage': str(data.parent / 'wire-override')})
            try:
                assert denied.initialize() is False
                assert denied._proc is None
            finally:
                denied.shutdown()
        finished = service.resume('probe', pending[0]['operationId'])
        assert finished['stage'] == 'completed' and service.pending('probe') == []
        assert (data / 'starts').read_text() == '1'
        fresh = start(config)
        assert fresh.environment == EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config))


def test_unrelated_provider_switch_does_not_invalidate_prepared_rebuild(setup):
    from core.mcp.lifecycle import McpLifecycle
    make, _, _ = setup
    manager, _, _ = make()
    class Interrupted(BaseException):
        pass
    def interrupt(stage, operation):
        if stage == 'prepared':
            raise Interrupted()
    with identity_scope(user_id='alice'):
        service = McpLifecycle(manager)
        with pytest.raises(Interrupted):
            service.rebuild('probe', checkpoint=interrupt)
        operation = service.pending('probe')[0]
        manager.capability_settings.set_enabled('mcp', 'unrelated', False)
        assert service.resume('probe', operation['operationId'])['stage'] == 'completed'


def test_other_workspace_cannot_consume_or_fail_pending_rebuild(setup, monkeypatch, tmp_path):
    from core.mcp.lifecycle import McpLifecycle
    make, _, _ = setup
    manager, _, _ = make()
    class Interrupted(BaseException):
        pass
    def interrupt(stage, operation):
        if stage == 'prepared':
            raise Interrupted()
    with identity_scope(user_id='alice'):
        service = McpLifecycle(manager)
        with pytest.raises(Interrupted):
            service.rebuild('probe', checkpoint=interrupt)
        operation = service.pending('probe')[0]
        with monkeypatch.context() as changed:
            changed.setattr(manager, 'workspace_root', str(tmp_path / 'other-workspace'))
            with pytest.raises(PermissionError, match='workspace'):
                service.resume('probe', operation['operationId'])
        assert service.pending('probe')[0]['stage'] == 'prepared'
        assert service.resume('probe', operation['operationId'])['stage'] == 'completed'


@pytest.mark.parametrize('change', ['switch', 'source'])
def test_changed_provider_invalidates_prepared_candidate_and_retains_previous_selection(setup, change):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, data = make()
    class Interrupted(BaseException):
        pass
    def interrupt(stage, operation):
        if stage == 'prepared':
            raise Interrupted()
    with identity_scope(user_id='alice'):
        old = start(config)
        selected = old.environment
        service = McpLifecycle(manager)
        with pytest.raises(Interrupted):
            service.rebuild('probe', checkpoint=interrupt)
        operation = service.pending('probe')[0]
        if change == 'switch':
            manager.capability_settings.set_enabled('mcp', 'probe', False)
            manager.capability_settings.set_enabled('mcp', 'probe', True)
        else:
            entry = Path(config['args'][0])
            entry.write_text(entry.read_text() + '\n# changed source\n')
        with pytest.raises(PermissionError):
            service.resume('probe', operation['operationId'])
        assert EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=selected['launch_digest']) == selected
        assert service.pending('probe') == []
        assert (data / 'starts').read_text() == '1'


def test_draining_prevents_late_tool_publication(setup):
    from agent.tools.mcp.mcp_client import McpClientRegistry
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, _ = make()
    class Interrupted(BaseException):
        pass
    def interrupt(stage, operation):
        if stage == 'draining':
            raise Interrupted()
    with identity_scope(user_id='alice'):
        old = start(config)
        schemas = old.list_tools()
        manager._mcp_registry = McpClientRegistry()
        manager._mcp_generations['probe'] = 1
        manager._mcp_active_configs['probe'] = config
        manager._mcp_status['probe'] = 'pending'
        service = McpLifecycle(manager)
        with pytest.raises(Interrupted):
            service.rebuild('probe', checkpoint=interrupt)
        assert manager._publish_mcp_tools(config, old, 1, schemas) is False
        assert manager._mcp_tool_instances == {}
        service.resume('probe', service.pending('probe')[0]['operationId'])
        assert manager._mcp_generations['probe'] > 1
        assert manager._publish_mcp_tools(config, old, 1, schemas) is False


def test_managed_domain_validation_cannot_read_account_or_migration_data(tmp_path, monkeypatch, native_settings_action):
    from core.mcp.lifecycle import McpLifecycle
    from test_managed_mcp_domains import server_config
    monkeypatch.setattr(ToolManager, '_instances', {})
    with identity_scope(user_id='test-user'):
        manager = ToolManager()
        data = tmp_path / 'business'
        config = server_config('elearning', data)
        monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [config])
        client = McpClient(config)
        try:
            assert client.initialize()
            client.call_tool('save_settings', {'payload': {'username': 'student', 'password': 'synthetic', 'courses': ['42']}})
            before = {path.relative_to(data): path.read_bytes() for path in data.rglob('*') if path.is_file()}
            result = McpLifecycle(manager).rebuild('elearning')
            assert result['stage'] == 'completed'
            assert {path.relative_to(data): path.read_bytes() for path in data.rglob('*') if path.is_file()} == before
        finally:
            client.shutdown()
            manager.shutdown_mcp()


def test_mcp_rebuild_api_and_inventory_reuse_owner_lifecycle(setup, monkeypatch):
    import channel.desktop.desktop_channel as desktop
    from core.security.control import RuntimeControl
    make, start, _ = setup
    manager, config, _ = make()
    body = {'action': 'rebuild', 'kind': 'mcp', 'id': 'probe'}
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(body).encode())
    with identity_scope(user_id='alice'):
        old = start(config)
        row = next(row for row in RuntimeControl().inventory()['runtimes'] if row['kind'] == 'mcp')
        assert row['can_rebuild'] is True
        reply = json.loads(desktop.SecurityRuntimeTrustHandler().POST())
        assert reply['status'] == 'success', reply
        assert old._proc is None
        row = next(row for row in reply['runtimes'] if row['kind'] == 'mcp')
        assert row['environment']['id'] != old.environment['id'] and row['running'] is False
        assert row['can_rebuild'] is True
    for scope in ({'session_id': 'agent-session'}, {'task_id': 'background-task'}):
        with identity_scope(user_id='alice', **scope):
            assert json.loads(desktop.SecurityRuntimeTrustHandler().POST())['status'] == 'error'


def test_mcp_resume_api_only_exposes_current_workspace_owner_operation(setup, monkeypatch):
    import channel.desktop.desktop_channel as desktop
    from core.mcp.lifecycle import McpLifecycle
    from core.security.control import RuntimeControl
    make, _, _ = setup
    manager, _, _ = make()
    class Interrupted(BaseException):
        pass
    def interrupt(stage, operation):
        if stage == 'prepared':
            raise Interrupted()
    with identity_scope(user_id='alice'):
        service = McpLifecycle(manager)
        with pytest.raises(Interrupted):
            service.rebuild('probe', checkpoint=interrupt)
        operation = service.pending('probe')[0]
        row = next(row for row in RuntimeControl().inventory()['runtimes'] if row['kind'] == 'mcp')
        assert row['can_rebuild'] is False and row['rebuild_operation'] == operation['operationId']
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    body = {'action': 'resume', 'kind': 'mcp', 'id': 'probe', 'operationId': operation['operationId']}
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(body).encode())
    with identity_scope(user_id='bob'):
        assert json.loads(desktop.SecurityRuntimeTrustHandler().POST())['status'] == 'error'
    with identity_scope(user_id='alice'):
        assert service.pending('probe')[0]['stage'] == 'prepared'
        reply = json.loads(desktop.SecurityRuntimeTrustHandler().POST())
        assert reply['status'] == 'success', reply
        assert service.pending('probe') == []


@pytest.mark.parametrize('config', [
    {'name': 'probe', 'type': 'streamable-http', 'url': 'https://remote.invalid'},
    {'name': 'probe', 'type': 'stdio', 'command': '/usr/bin/true', 'args': []},
])
def test_remote_or_executable_provider_does_not_claim_dependency_rebuild(setup, config):
    from core.mcp.lifecycle import McpLifecycle, rebuild_support
    make, _, configs = setup
    manager, _, _ = make()
    configs['alice'] = config
    with identity_scope(user_id='alice'):
        supported, reason = rebuild_support(config)
        assert supported is False and reason
        with pytest.raises(ValueError):
            McpLifecycle(manager).rebuild('probe')
        assert McpLifecycle(manager).pending('probe') == []


@pytest.mark.parametrize('field,value', [('kind', []), ('action', {})])
def test_rebuild_api_rejects_malformed_discriminators_without_internal_error(setup, monkeypatch, field, value):
    import channel.desktop.desktop_channel as desktop
    body = {'action': 'rebuild', 'kind': 'mcp', 'id': 'probe', field: value}
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(body).encode())
    with identity_scope(user_id='alice'):
        assert json.loads(desktop.SecurityRuntimeTrustHandler().POST())['status'] == 'error'


def test_selection_finalization_failure_keeps_maintenance_gate_until_recovery(setup, monkeypatch):
    from core.mcp.lifecycle import McpLifecycle
    from core.plugins.management import ManagementStore
    make, _, _ = setup
    manager, config, _ = make()
    advance = ManagementStore.advance
    with identity_scope(user_id='alice'):
        service = McpLifecycle(manager)
        def fail_after_selection(store, operation, stage, *args, **kwargs):
            if stage == 'selected':
                raise RuntimeError('Simulated operation journal interruption')
            return advance(store, operation, stage, *args, **kwargs)
        with monkeypatch.context() as interrupted:
            interrupted.setattr(ManagementStore, 'advance', fail_after_selection)
            with pytest.raises(RuntimeError):
                service.rebuild('probe')
        operation = service.pending('probe')
        assert len(operation) == 1 and operation[0]['stage'] == 'draining'
        from core.mcp.lifecycle import check_admission
        with pytest.raises(PermissionError):
            check_admission('probe', 'alice')
        selected = EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config))
        assert selected is not None
        assert service.resume('probe', operation[0]['operationId'])['stage'] == 'completed'
        assert EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config)) == selected


def test_real_node_mcp_rebuild_uses_independent_snapshot_and_preserves_data(setup):
    import shutil
    from core.mcp.lifecycle import McpLifecycle
    if not shutil.which('node') or not shutil.which('npm'):
        pytest.skip('Node/npm unavailable')
    make, start, _ = setup
    manager, config, data = make()
    package = Path(config['args'][0]).parent
    script = package / 'main.js'
    script.write_text('''const fs=require('fs'),path=require('path'),readline=require('readline');
const data=process.argv[process.argv.indexOf('--data-dir')+1];
fs.mkdirSync(data,{recursive:true});
const marker=path.join(data,'starts');
fs.writeFileSync(marker,fs.existsSync(marker)?String(Number(fs.readFileSync(marker,'utf8'))+1):'1');
readline.createInterface({input:process.stdin}).on('line',line=>{
 const request=JSON.parse(line); if(request.id===undefined)return;
 let result;
 if(request.method==='initialize') result={protocolVersion:'2024-11-05',capabilities:{tools:{}},serverInfo:{name:'node-probe',version:'1.0.0'}};
 else if(request.method==='tools/list') result={tools:[{name:'info',inputSchema:{type:'object',properties:{}}}]};
 else result={content:[{type:'text',text:JSON.stringify({cwd:process.cwd(),starts:fs.readFileSync(marker,'utf8')})}]};
 process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,result})+'\\n');
});
''')
    (package / 'package.json').write_text(json.dumps({'name': 'node-probe', 'version': '1.0.0'}))
    (package / 'package-lock.json').write_text(json.dumps({'name': 'node-probe', 'version': '1.0.0',
        'lockfileVersion': 3, 'packages': {'': {'name': 'node-probe', 'version': '1.0.0'}}}))
    config['command'] = shutil.which('node')
    config['args'][0] = str(script)
    config.package_digest = source_digest(package)
    with identity_scope(user_id='alice'):
        old = start(config)
        old_environment = old.environment
        assert McpLifecycle(manager).rebuild('probe')['stage'] == 'completed'
        selected = EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config))
        assert selected['runtime'] == 'node' and selected['id'] != old_environment['id']
        assert (data / 'starts').read_text() == '1'
        assert old._proc is None
        fresh = start(config)
        assert fresh.environment == selected
        assert json.loads(fresh.call_tool('info', {}))['starts'] == '2'
        assert Path(selected['path'], 'project', 'main.js').is_file()


def test_invalid_candidate_tool_listing_preserves_old_process_and_marks_candidate_failed(setup):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, data = make()
    config['env'] = {'REJECT_VALIDATION': '1'}
    with identity_scope(user_id='alice'):
        old = start(config)
        assert old.list_tools()[0]['name'] == 'info'
        with pytest.raises(ValueError, match='tool'):
            McpLifecycle(manager).rebuild('probe')
        assert old._proc is not None and old._proc.poll() is None
        assert old.list_tools()[0]['name'] == 'info'
        selected = EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config))
        assert selected == old.environment
        candidates = list(Path(selected['path']).parent.glob('*/environment.json'))
        assert len(candidates) == 2
        assert sorted(json.loads(marker.read_text())['state'] for marker in candidates) == ['failed', 'ready']
        assert (data / 'starts').read_text() == '1'


def test_candidate_real_os_boundary_denies_original_account_data(setup, monkeypatch):
    from core.mcp.lifecycle import McpLifecycle, McpMaintenanceConfig
    make, start, _ = setup
    manager, config, data = make()
    data.mkdir()
    private = data / 'account'
    private.write_text('SYNTHETIC_ACCOUNT')
    config['env'] = {'AUDIT_OWNER_FILE': str(private)}
    observed = []
    list_tools = McpClient.list_tools
    def observe(client):
        if isinstance(client.config, McpMaintenanceConfig):
            observed.append(client.server_version)
        return list_tools(client)
    monkeypatch.setattr(McpClient, 'list_tools', observe)
    with identity_scope(user_id='alice'):
        old = start(config)
        assert old.server_version == 'leaked'  # normal owner-bound runtime has its account directory
        assert McpLifecycle(manager).rebuild('probe')['stage'] == 'completed'
        assert observed == ['denied']
        assert private.read_text() == 'SYNTHETIC_ACCOUNT'


def test_rebuild_waits_for_initialization_lease_and_reaps_late_process(setup, monkeypatch):
    import threading
    from contextvars import copy_context
    from core.mcp.lifecycle import McpLifecycle
    make, _, _ = setup
    manager, config, _ = make()
    entered, release, draining, finished = (threading.Event() for _ in range(4))
    results, failures = [], []
    with identity_scope(user_id='alice'):
        late = McpClient(config)
        original = late._handshake
        def delayed_handshake():
            entered.set()
            if not release.wait(8):
                raise RuntimeError('Test initialization barrier was not released')
            return original()
        monkeypatch.setattr(late, '_handshake', delayed_handshake)
        initialize = threading.Thread(target=copy_context().run, args=(late.initialize,))
        initialize.start()
        def checkpoint(stage, operation):
            if stage == 'draining':
                draining.set()
        def rebuild():
            try:
                results.append(McpLifecycle(manager).rebuild('probe', checkpoint=checkpoint))
            except Exception as error:
                failures.append(error)
            finally:
                finished.set()
        maintenance = threading.Thread(target=copy_context().run, args=(rebuild,))
        try:
            assert entered.wait(4)
            maintenance.start()
            assert draining.wait(4)
            assert not finished.wait(0.1)
            release.set()
            initialize.join(4)
            maintenance.join(4)
            assert not initialize.is_alive() and not maintenance.is_alive()
            assert not failures and results[0]['stage'] == 'completed'
            assert late._proc is None
        finally:
            release.set()
            late.shutdown()
            initialize.join(5)
            if maintenance.ident is not None:
                maintenance.join(5)


def test_same_user_different_launch_keeps_its_environment_and_live_connection(setup):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, _ = make()
    other_config = ManagedMcpConfig({**config, 'env': {'SERVICE_ORIGIN': 'https://other.invalid'}}, data_root=config.data_root,
                                   package_digest=config.package_digest)
    class Interrupted(BaseException):
        pass
    def interrupt(stage, operation):
        if stage == 'draining':
            raise Interrupted()
    with identity_scope(user_id='alice'):
        old = start(config)
        other = start(other_config)
        selected_other = other.environment
        service = McpLifecycle(manager)
        with pytest.raises(Interrupted):
            service.rebuild('probe', checkpoint=interrupt)
        assert json.loads(other.call_tool('info', {}))['prefix'] == str(Path(selected_other['path']) / 'python')
        assert service.resume('probe', service.pending('probe')[0]['operationId'])['stage'] == 'completed'
        assert old._proc is None
        assert other._proc is not None and other._proc.poll() is None
        assert json.loads(other.call_tool('info', {}))['prefix'] == str(Path(selected_other['path']) / 'python')
        assert other.environment['id'] == selected_other['id']


def test_reenable_after_dependency_rebuild_uses_selected_environment(setup):
    from core.mcp.lifecycle import McpLifecycle
    make, start, _ = setup
    manager, config, _ = make(enabled=False)
    with identity_scope(user_id='alice'):
        service = McpLifecycle(manager)
        assert service.rebuild('probe')['stage'] == 'completed'
        selected = EnvironmentStore().current('mcp', 'probe', 'alice', launch_digest=environment_identity(config))
        assert selected is not None
        config['enabled'] = True
        fresh = start(config)
        assert fresh.environment['id'] == selected['id']

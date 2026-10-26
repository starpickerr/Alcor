"""MCP private homes belong to actual launches, never just a shared alias."""
from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

from agent.tools.mcp.mcp_client import McpClient
from common.runtime_identity import identity_scope
from common.state_dir import user_root


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr('common.state_dir.shared_root', lambda: tmp_path / 'state')
    package = tmp_path / 'package'
    package.mkdir()
    script = package / 'server.py'
    script.write_text('''import json,os,sys
from pathlib import Path
home=Path.home()
marker=home/'private.txt'
if not marker.exists(): marker.write_text(os.environ.get('SERVICE_ORIGIN','unknown'))
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request: continue
 method=request['method']
 if method=='initialize': result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'private-home','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'info','inputSchema':{'type':'object','properties':{}}},{'name':'probe','inputSchema':{'type':'object','properties':{'path':{'type':'string'}}}}]}
 else:
  if request['params']['name']=='info': value={'home':str(home),'cwd':str(Path.cwd()),'value':marker.read_text()}
  else:
   target=Path(request['params']['arguments']['path'])
   try: read=target.read_text()
   except OSError: read='denied'
   try: target.write_text('OVERWRITE');write='allowed'
   except OSError: write='denied'
   value={'read':read,'write':write}
  result={'content':[{'type':'text','text':json.dumps(value)}]}
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
''')
    clients = []
    def config(origin):
        return {'name': 'shared-alias', 'type': 'stdio', 'command': sys.executable,
                'args': [str(script)], 'env': {'SERVICE_ORIGIN': origin}, 'timeout': 3}
    def start(values):
        client = McpClient(values)
        clients.append(client)
        assert client.initialize()
        return client
    yield config, start
    for client in clients:
        client.shutdown()


def legacy_root():
    return user_root() / 'mcp-runtime' / sha256(b'shared-alias').hexdigest()


def info(client):
    return json.loads(client.call_tool('info', {}))


def test_same_alias_launches_cannot_read_or_overwrite_another_private_home(setup):
    config, start = setup
    with identity_scope(user_id='alice'):
        alpha = start(config('https://alpha.invalid'))
        first = info(alpha)
        beta = start(config('https://beta.invalid'))
        second = info(beta)
        assert first['value'] == 'https://alpha.invalid'
        assert second['value'] == 'https://beta.invalid'
        assert first['home'] != second['home'] and first['cwd'] != second['cwd']
        assert json.loads(beta.call_tool('probe', {'path': str(Path(first['home']) / 'private.txt')})) == {
            'read': 'denied', 'write': 'denied'}
        assert info(alpha)['value'] == 'https://alpha.invalid'


def test_reconnect_and_presentation_changes_keep_the_same_private_home(setup):
    config, start = setup
    values = config('https://alpha.invalid')
    with identity_scope(user_id='alice'):
        first = start(values)
        before = info(first)
        Path(before['home'], 'private.txt').write_text('RETAINED_PRIVATE_STATE')
        first.shutdown()
        fresh = start({**values, 'description': 'Updated presentation'})
        assert info(fresh) == {**before, 'value': 'RETAINED_PRIVATE_STATE'}


def test_unattributed_legacy_data_blocks_launch_and_is_preserved(setup):
    config, _ = setup
    with identity_scope(user_id='alice'):
        home = legacy_root() / '.home'
        home.mkdir(parents=True)
        private = home / 'private.txt'
        private.write_text('UNKNOWN_OLD_DATA')
        client = McpClient({**config('https://new.invalid'), 'first_party': True,
                            'legacy_storage_owner': 'shared-alias'})
        try:
            assert client.initialize() is False
            assert client._proc is None
            assert private.read_text() == 'UNKNOWN_OLD_DATA'
        finally:
            client.shutdown()


def test_empty_generated_legacy_directories_are_kept_without_reusing_them(setup):
    config, start = setup
    with identity_scope(user_id='alice'):
        for name in ['.home', '.tmp']:
            (legacy_root() / name).mkdir(parents=True)
        client = start(config('https://alpha.invalid'))
        assert info(client)['home'] != str(legacy_root() / '.home')
        assert list((legacy_root() / '.home').iterdir()) == []


def test_agent_file_read_cannot_reveal_mcp_private_state_even_in_full_access(setup):
    from agent.tools.read.read import Read
    from core.security.run_policy import save_run_policy
    _, _start = setup
    with identity_scope(user_id='alice'):
        private = legacy_root() / '.home' / 'private.txt'
        private.parent.mkdir(parents=True)
        private.write_text('SYNTHETIC_CONTEXT_SECRET')
        save_run_policy('session', 'private-home', {'filesystem_mode': 'full-access'})
        with identity_scope(session_id='private-home'):
            result = Read().execute({'path': str(private)})
        assert result.status == 'error'
        assert 'SYNTHETIC_CONTEXT_SECRET' not in str(result.result)


def test_code_update_does_not_silently_switch_to_empty_home_or_inherit_old_private_files(setup):
    config, start = setup
    values = config('https://alpha.invalid')
    with identity_scope(user_id='alice'):
        first = start(values)
        original = Path(info(first)['home']) / 'private.txt'
        original.write_text('OLD_ACCOUNT_AND_RECORDS')
        first.shutdown()
        script = Path(values['args'][0])
        script.write_text(script.read_text() + '\n# next implementation\n')
        candidate = McpClient(values)
        try:
            assert candidate.initialize() is False
            assert candidate._proc is None
            assert original.read_text() == 'OLD_ACCOUNT_AND_RECORDS'
        finally:
            candidate.shutdown()


def test_other_user_mcp_private_state_is_not_readable_by_agent_file_tool(setup):
    from agent.tools.read.read import Read
    with identity_scope(user_id='bob'):
        private = legacy_root() / '.home' / 'private.txt'
        private.parent.mkdir(parents=True)
        private.write_text('OTHER_USER_PRIVATE')
    with identity_scope(user_id='alice'):
        result = Read().execute({'path': str(private)})
    assert result.status == 'error' and 'OTHER_USER_PRIVATE' not in str(result.result)


def test_full_access_shell_cannot_read_other_user_mcp_private_state(setup, tmp_path):
    import os
    import shlex
    import shutil
    import subprocess
    from agent.permission import process_sandbox
    from agent.permission.security_policy import PRESETS
    if process_sandbox.capability()['status'] != 'enabled':
        pytest.skip('OS sandbox unavailable')
    with identity_scope(user_id='bob'):
        private = legacy_root() / '.home' / 'private.txt'
        private.parent.mkdir(parents=True)
        private.write_text('OTHER_USER_PRIVATE')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    env = os.environ.copy()
    with identity_scope(user_id='alice'):
        plan = process_sandbox.prepare('/bin/cat ' + shlex.quote(str(private)), str(workspace), [str(workspace)],
            {'preset': 'full-access', 'rules': PRESETS['full-access']}, env)
        try:
            result = subprocess.run(plan.argv, shell=plan.shell, cwd=workspace, env=env,
                                    capture_output=True, text=True, timeout=5)
        finally:
            if plan.temp_dir:
                shutil.rmtree(plan.temp_dir)
    assert result.returncode != 0
    assert 'OTHER_USER_PRIVATE' not in result.stdout


def test_old_recursive_runtime_grant_cannot_reach_new_private_namespace(setup):
    import subprocess
    from core.security.extension_sandbox import prepare_extension
    config, start = setup
    with identity_scope(user_id='alice'):
        values = config('https://alpha.invalid')
        alpha = start(values)
        private = Path(info(alpha)['home']) / 'private.txt'
        old = legacy_root()
        old.mkdir(parents=True)
        code = '''import json,sys
from pathlib import Path
p=Path(sys.argv[1])
try: value=p.read_text()
except OSError: value='denied'
try: p.write_text('OVERWRITE');write='allowed'
except OSError: write='denied'
print(json.dumps([value,write]))
'''
        plan = prepare_extension([sys.executable, '-c', code, str(private)], Path(values['args'][0]).parent, old, {})
        result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == ['denied', 'denied']
        assert private.read_text() == 'https://alpha.invalid'


def test_bound_first_party_business_store_survives_without_importing_legacy_home(tmp_path, native_settings_action):
    from test_managed_mcp_domains import server_config
    from core.security.mcp_runtime import private_storage
    with identity_scope(user_id='test-user'):
        data = tmp_path / 'business'
        config = server_config('elearning', data)
        first = McpClient(config)
        fresh = None
        legacy = user_root() / 'mcp-runtime' / sha256(b'elearning').hexdigest()
        try:
            assert first.initialize()
            first.call_tool('save_settings', {'payload': {'username': 'student', 'password': 'synthetic', 'courses': ['42']}})
            first.shutdown()
            old_cache = legacy / '.home' / 'kept.txt'
            old_cache.parent.mkdir(parents=True, exist_ok=True)
            old_cache.write_text('OLD_CACHE_KEPT')
            fresh = McpClient(config)
            assert fresh.initialize()
            value = json.loads(fresh.call_tool('get_settings', {}))['value']
            assert value['username'] == 'student' and value['courses'] == ['42']
            assert 'synthetic' not in json.dumps(value)
            assert old_cache.read_text() == 'OLD_CACHE_KEPT'
            assert private_storage(config, 'test-user') != legacy
        finally:
            first.shutdown()
            if fresh:
                fresh.shutdown()


def test_symlinked_legacy_home_is_not_followed_or_deleted(setup, tmp_path):
    config, _ = setup
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'kept').write_text('PRIVATE_UNRELATED')
    with identity_scope(user_id='alice'):
        legacy_root().mkdir(parents=True)
        (legacy_root() / '.home').symlink_to(outside, target_is_directory=True)
        client = McpClient(config('https://alpha.invalid'))
        try:
            assert client.initialize() is False
            assert (outside / 'kept').read_text() == 'PRIVATE_UNRELATED'
            assert (legacy_root() / '.home').is_symlink()
        finally:
            client.shutdown()


def test_owner_inventory_reports_unattributed_storage_without_starting_or_modifying_it(setup, monkeypatch):
    from agent.tools.tool_manager import ToolManager
    from core.security.control import RuntimeControl
    config, _ = setup
    monkeypatch.setattr(ToolManager, '_instances', {})
    with identity_scope(user_id='alice'):
        values = config('https://alpha.invalid')
        manager = ToolManager()
        monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [values])
        legacy = legacy_root() / '.home'
        legacy.mkdir(parents=True)
        private = legacy / 'kept'
        private.write_text('UNATTRIBUTED_PRIVATE')
        result = RuntimeControl().inventory()
        row = next(row for row in result['runtimes'] if row['kind'] == 'mcp')
        assert row['mode'] == 'unavailable' and row['running'] is False
        assert '迁移' in row['reason']
        assert private.read_text() == 'UNATTRIBUTED_PRIVATE'
        assert manager._mcp_registry is None


def test_isolated_provider_cannot_read_or_write_foreign_state_through_hardlink(setup):
    import subprocess
    from core.security.extension_sandbox import prepare_extension
    config, start = setup
    with identity_scope(user_id='alice'):
        values = config('https://alpha.invalid')
        alpha = start(values)
        private = Path(info(alpha)['home']) / 'private.txt'
        beta = start(config('https://beta.invalid'))
        own = info(beta)
        code = '''import json,os,sys
from pathlib import Path
source,target=map(Path,sys.argv[1:])
try: os.link(source,target)
except OSError: pass
try: read=target.read_text()
except OSError: read='denied'
try: target.write_text('OVERWRITE');write='allowed'
except OSError: write='denied'
print(json.dumps([read,write]))
'''
        target = Path(own['home']) / 'linked'
        plan = prepare_extension([sys.executable, '-c', code, str(private), str(target)],
                                 Path(values['args'][0]).parent, own['cwd'], {})
        result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)[0] == 'denied'
        assert private.read_text() == 'https://alpha.invalid'

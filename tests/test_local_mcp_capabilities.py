"""Real isolated stdio processes receive only explicitly granted capabilities."""
import http.server
import json
import sys
import threading

import pytest

from agent.tools.mcp.mcp_client import McpClient
from common.runtime_identity import identity_scope
from core.plugins.caller import caller_origin_scope
from core.security.mcp_credentials import McpCredentialStore, runtime_env
from core.security.mcp_runtime import launch_identity
from core.security.runtime_trust import RuntimeTrustStore


@pytest.fixture
def local(tmp_path, monkeypatch):
    package = tmp_path / 'source'
    package.mkdir()
    script = package / 'server.py'
    script.write_text('''import json,sys,os,urllib.request
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request: continue
 method=request['method']
 if method=='initialize': result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'capabilities','version':'1'}}
 elif method=='tools/list': result={'tools':[{'name':'probe','inputSchema':{'type':'object'}}]}
 else:
  try:
   with urllib.request.urlopen(os.environ['TEST_URL'],timeout=1) as response: network=response.read().decode()
  except OSError: network='denied'
  result={'content':[{'type':'text','text':json.dumps({'network':network,'dedicated':os.environ.get('API_TOKEN'),'host':os.environ.get('CORE_ONLY_SECRET')})}]}
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
''')
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length','2')
            self.end_headers()
            self.wfile.write(b'OK')
    server = http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    config = dict(name='synthetic-local',type='stdio',command=sys.executable,args=[str(script)],timeout=3,
                  env={'TEST_URL':f'http://127.0.0.1:{server.server_port}'})
    monkeypatch.setenv('CORE_ONLY_SECRET','MUST_NOT_BE_INHERITED')
    try:
        yield config, script, tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_network_is_explicit_files_stay_isolated_and_mutation_stops_old_instance(local):
    config, script, _ = local
    with identity_scope(user_id='owner'), caller_origin_scope('desktop'):
        first = McpClient(config)
        second = None
        try:
            assert first.initialize()
            assert json.loads(first.call_tool('probe',{})) == {'network':'denied','dedicated':None,'host':None}
            key = dict(user_id='owner',kind='mcp',identifier=config['name'],digest=launch_identity(config)[0])
            store = RuntimeTrustStore()
            store.set_capabilities(**{k:v for k,v in key.items() if k != 'kind'},network=True,credentials=False,expected_revision=0)
            assert first._proc is None
            second = McpClient(config)
            assert second.initialize()
            assert second.sandbox_state['mode'] == 'isolated'
            assert json.loads(second.call_tool('probe',{}))['network'] == 'OK'
            script.write_text(script.read_text()+'\n# new implementation\n')
            with pytest.raises(PermissionError,match='changed'):
                second.call_tool('probe',{})
            assert not store.capabilities(**{**key,'digest':launch_identity(config)[0]})['network']
        finally:
            first.shutdown()
            if second:
                second.shutdown()


@pytest.mark.skipif(sys.platform != 'darwin',reason='Real isolated Keychain credentials')
def test_dedicated_env_is_content_bound_and_other_services_cannot_borrow_reference(local, isolated_keychain):
    config, script, root = local
    path = root / 'mcp.json'
    config['env']['API_TOKEN'] = 'SYNTHETIC_DEDICATED_TOKEN'
    path.write_text(json.dumps({'mcpServers':{config['name']:{key:value for key,value in config.items() if key != 'name'}}}))
    with identity_scope(user_id='owner'), caller_origin_scope('desktop'):
        store = McpCredentialStore(path)
        store.inventory(config['name'],migrate=True)
        bound = store.configs()[0]
        with pytest.raises(PermissionError,match='dedicated env'):
            runtime_env(bound)
        key = dict(user_id='owner',identifier=config['name'],digest=launch_identity(bound)[0])
        RuntimeTrustStore().set_capabilities(**key,network=False,credentials=True,expected_revision=0)
        client = McpClient(bound)
        try:
            assert client.initialize()
            value = json.loads(client.call_tool('probe',{}))
            assert value == {'network':'denied','dedicated':'SYNTHETIC_DEDICATED_TOKEN','host':None}
            with pytest.raises(PermissionError):
                runtime_env({**bound,'name':'borrower'})
            state = store.inventory(config['name'])
            store.save(config['name'],'env','API_TOKEN','ROTATED_DEDICATED',expected_revision=state['revision'])
            with pytest.raises(PermissionError,match='configuration changed'):
                client.call_tool('probe',{})
            assert client._proc is None
        finally:
            client.shutdown()


def test_host_cwd_is_rejected_in_preflight_and_launch(local):
    from core.security.mcp_runtime import prepare_mcp
    config, _, directory = local
    config = {**config, 'cwd': str(directory)}
    with pytest.raises(PermissionError, match='private runtime directory'):
        launch_identity(config)
    with pytest.raises(PermissionError, match='private runtime directory'):
        prepare_mcp(config, {})

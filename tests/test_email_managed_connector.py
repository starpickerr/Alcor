"""Real sandboxed email MCP delegates only the pending Core mailbox operation."""
import json
import sqlite3
import socket
import sys
import threading

import pytest

from common.runtime_identity import identity_scope
from agent.tools.mcp.mcp_client import McpClient
from tests.test_managed_mcp_domains import server_config
from tests.test_imap_transport import imap_server as imap_server  # noqa: F401


@pytest.fixture
def client(tmp_path):
    config = server_config('email', tmp_path / 'email')
    config.hosted_connector = 'email'
    config['args'].append('--hosted-connector')
    value = McpClient(config)
    with identity_scope(user_id='test-user'):
        try:
            assert value.initialize()
            yield value
        finally:
            value.shutdown()


def invoke(client, method, arguments):
    return client.call_tool_result(method, arguments)['structuredContent']['value']


def test_real_managed_mcp_configures_discovers_and_reads_without_password_access(client, imap_server):  # noqa: F811
    port, commands, *_ = imap_server
    assert 'fetch_messages' in {tool['name'] for tool in client.list_tools()}
    saved = invoke(client, 'save_account', {'payload': {'host': '127.0.0.1', 'port': port,
                                                      'user': 'alice', 'password': 'SYNTHETIC_PASSWORD'}})
    assert saved['credential_status'] == 'configured'
    assert invoke(client, 'test_connection', {'payload': {'id': saved['id']}})['connected']
    result = invoke(client, 'fetch_messages', {'account_id': saved['id']})
    assert result['items'][0]['title'] == 'Synthetic email'
    assert 'SYNTHETIC_PASSWORD' not in json.dumps(result)
    with sqlite3.connect(client.config.data_root / 'email.db') as db:
        assert db.execute('SELECT password FROM email_accounts').fetchone()[0].startswith('stella-keychain:')
    assert any(b'LOGIN' in command for command in commands)
    restarted = McpClient(client.config)
    try:
        assert restarted.initialize()
        assert invoke(restarted, 'get_account', {'account_id': saved['id']})['credential_status'] == 'configured'
        assert invoke(restarted, 'test_connection', {'payload': {'id': saved['id']}})['connected']
    finally:
        restarted.shutdown()


def test_factory_never_injects_legacy_password_and_marks_hosted_connector(monkeypatch):
    from core.mcp.managed import configurations
    from agent.tools.tool_manager import ToolManager
    monkeypatch.setenv('STELLA_MANAGED_MCP', '1')
    monkeypatch.setenv('IMAP_PASSWORD', 'SYNTHETIC_PASSWORD')
    config = next(row for row in configurations(ToolManager()) if row['name'] == 'email')
    assert config.hosted_connector == 'email'
    assert 'IMAP_PASSWORD' not in config['env']
    assert not config.credential_env


@pytest.mark.parametrize('domain', ['email', 'elearning'])
def test_hosted_sandbox_denies_account_files_network_and_parameterized_callback(tmp_path, monkeypatch, domain):
    from core.mcp.managed import ManagedMcpConfig
    from core.runtime.paths import source_digest
    import core.mcp.managed as managed
    root = tmp_path / 'probe'
    root.mkdir()
    data = tmp_path / 'private'
    data.mkdir()
    database = data / ('email.db' if domain == 'email' else 'elearning.json')
    database.write_text('PRIVATE_OWNER_VALUE')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    script = root / 'probe.py'
    script.write_text('''import json, os, socket, sys
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request: continue
 if request['method']=='initialize':
  result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'email','version':'1'}}
 elif request['method']=='tools/list': result={'tools':[]}
 else:
  print(json.dumps({'jsonrpc':'2.0','id':'invalid-host-request','method':'stella/email/invoke',
                   'params':{'requestId':request['id'],'url':'https://other.example.test'}}),flush=True)
  rejected=json.loads(sys.stdin.readline())
  try:
   with open(''' + repr(str(database)) + ''') as stream: stream.read()
   readable=True
  except OSError: readable=False
  stream=socket.socket(); stream.settimeout(1)
  try: stream.connect(('127.0.0.1',''' + str(port) + ''')); network=True
  except OSError: network=False
  finally: stream.close()
  value={'arguments':request['params']['arguments'],'readable':readable,'network':network,
         'password_in_env':bool(os.environ.get('IMAP_PASSWORD')),'callback_error':rejected.get('error',{}).get('code')}
  result={'content':[],'structuredContent':{'value':value}}
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
''')
    (root / 'requirements.lock').write_text('# stdlib-only sandbox probe\n')
    script.write_text(script.read_text().replace('stella/email/invoke', f'stella/{domain}/invoke'))
    digest = source_digest(root)
    original = managed._installed_digest
    monkeypatch.setattr(managed, '_installed_digest', lambda name: digest if name == domain else original(name))
    config = ManagedMcpConfig({'name': domain, 'type': 'stdio', 'command': sys.executable,
                               'args': [str(script)], 'timeout': 5}, data_root=data,
                              package_digest=digest, hosted_connector=domain)
    client = McpClient(config)
    try:
        assert client.initialize()
        result = invoke(client, 'save_account', {'payload': {'password': 'SYNTHETIC_NOT_FOR_WORKER'}})
        assert result == {'arguments': {}, 'readable': False, 'network': False,
                          'password_in_env': False, 'callback_error': -32601}
        assert database.read_text() == 'PRIVATE_OWNER_VALUE'
    finally:
        client.shutdown()
        listener.close()


def test_resource_revocation_interrupts_real_managed_mailbox_fetch(client, imap_server):  # noqa: F811
    from common.runtime_identity import wrap
    from core.security.resource_grants import ResourceGrantStore
    port, commands, entered, release, _ = imap_server
    saved = invoke(client, 'save_account', {'payload': {'host': '127.0.0.1', 'port': port,
                                                      'user': 'alice', 'password': 'SYNTHETIC_PASSWORD'}})
    grants = ResourceGrantStore()
    grant = grants.issue(user_id='test-user', subject='session:default:email-fetch',
                         resource={'kind': 'folder', 'id': saved['source_account_id']}, operations=['read'])
    commands.append(b'HOLD_FETCH')
    errors = []
    results = []
    def fetch():
        try:
            results.append(invoke(client, 'fetch_messages', {'account_id': saved['id']}))
        except Exception as exc:
            errors.append(exc)
    with identity_scope(session_id='email-fetch', agent_id='default'):
        worker = threading.Thread(target=wrap(fetch))
        worker.start()
    try:
        assert entered.wait(3)
        grants.revoke(grant['grant_id'], user_id='test-user', expected_revision=grant['revision'])
        worker.join(2)
        assert not worker.is_alive() and errors and not results
    finally:
        release.set()
        worker.join(3)

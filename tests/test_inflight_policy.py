"""Revocation must prevent release of a result already in flight."""
import json
import threading
import time
from pathlib import Path

import pytest

from common.runtime_identity import identity_scope
from core.security.run_policy import save_run_policy


@pytest.mark.parametrize('revoke', ['policy', 'generation'])
@pytest.mark.parametrize('effect,side_effects', [('query', 'none'), ('mutation', 'committed')])
def test_real_worker_discards_body_after_revocation(tmp_path, revoke, effect, side_effects):
    from core.plugins.host import PluginSession
    from core.plugins.registry import PluginRegistry
    from core.plugins.authz import PluginAuthorizationStore
    package = tmp_path / 'plugin'
    package.mkdir()
    (package / 'plugin.json').write_text(json.dumps({'manifestVersion': 1, 'id': 'review.slow', 'name': 'slow',
        'version': '1.0.0', 'hostApi': '^1.0.0', 'runtime': {'kind': 'python', 'entry': 'main.py', 'abi': 'cp312'},
        'permissions': [{'capability': 'sample.read', 'required': True}], 'contributes': {'services': ['review.slow@1']}}))
    (package / 'main.py').write_text('''import time
from pathlib import Path
SERVICE_DESCRIPTORS = [{'contract':'review.slow@1','methods':[{'name':'run','effect':''' + repr(effect) + ''','operation':'read' if ''' + repr(effect) + ''' == 'query' else 'write','permissions':['sample.read']}]}]
def handle_invoke(params):
    Path('started').write_text('ready')
    while not Path('release').exists(): time.sleep(.01)
    return {'status':'succeeded','sideEffects':''' + repr(side_effects) + ''','value':'PRIVATE_INFLIGHT_BODY'}
''')
    authz = PluginAuthorizationStore()
    authz.issue(grant_ref='slow-grant', plugin_id='review.slow', user_id='local-default', capabilities=['sample.read'])
    session = PluginSession(package, source_label='test', registry=PluginRegistry(), authz=authz)
    save_run_policy('session', 'inflight', {'filesystem_mode': 'workspace-write',
        'capabilities': {'plugin:review.slow': {'read': 'allow', 'write': 'allow'}}})
    results = []
    def run():
        with identity_scope(session_id='inflight'):
            try:
                results.append(session.invoke_contract(contract='review.slow@1', method='run', input_payload={},
                    origin='tool', grant_ref='slow-grant'))
            except Exception as exc:
                results.append(exc)
    thread = None
    try:
        session.start()
        private = Path(session._storage_directory)
        (private / 'started').unlink(missing_ok=True)
        (private / 'release').unlink(missing_ok=True)
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 5
        while not (private / 'started').exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert (private / 'started').exists(), results
        if revoke == 'policy':
            save_run_policy('session', 'inflight', {'filesystem_mode': 'read-only'})
        else:
            session.registry.revoke(session.plugin_id, session.generation)
        (private / 'release').touch()
        thread.join(5)
        assert not thread.is_alive()
        assert 'PRIVATE_INFLIGHT_BODY' not in str(results)
        assert results[0]['status'] == 'failed'
        assert results[0]['sideEffects'] == side_effects
    finally:
        if session._storage_directory:
            (Path(session._storage_directory) / 'release').touch()
        if thread:
            thread.join(5)
        session.dispose()


def test_mcp_discards_result_after_run_policy_change():
    from agent.tools.mcp.mcp_tool import McpTool
    save_run_policy('session', 'mcp-inflight', {'filesystem_mode': 'read-only', 'capabilities': {'mcp:slow': {'read': 'allow'}}})
    class Client:
        def call_tool(self, name, params):
            save_run_policy('session', 'mcp-inflight', {'capabilities': {'mcp:slow': {'read': 'deny'}}})
            return 'PRIVATE_MCP_BODY'
    with identity_scope(session_id='mcp-inflight'):
        result = McpTool(Client(), {'name': 'read'}, 'slow').execute({})
    assert result.status == 'error'
    assert 'PRIVATE_MCP_BODY' not in str(result)

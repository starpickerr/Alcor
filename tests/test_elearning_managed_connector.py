"""The actual isolated provider invokes only its pending Core operation."""
import json

from agent.tools.mcp.mcp_client import McpClient
from tests.test_managed_mcp_domains import server_config
from tests.test_elearning_transport import api as api  # noqa: F401


def invoke(client, method, arguments):
    return client.call_tool_result(method, arguments)['structuredContent']['value']


def test_factory_uses_hosted_elearning(monkeypatch):
    from agent.tools.tool_manager import ToolManager
    from core.mcp.managed import configurations
    monkeypatch.setenv('STELLA_MANAGED_MCP', '1')
    config = next(row for row in configurations(ToolManager()) if row['name'] == 'elearning')
    assert config.hosted_connector == 'elearning'
    assert '--hosted-connector' in config['args']
    assert not config.credential_env


def test_actual_managed_provider_saves_discovers_and_restarts(api):  # noqa: F811
    store, _, received, *_ = api
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    config.hosted_connector = 'elearning'
    config['args'].append('--hosted-connector')
    for _ in range(2):
        client = McpClient(config)
        try:
            assert client.initialize()
            assert 'list_courses' in {row['name'] for row in client.list_tools()}
            saved = invoke(client, 'save_settings', {'payload': {'courses': ['42'], 'enabled': True}})
            assert saved['credential_states']['token'] == 'configured'
            assert invoke(client, 'list_courses', {}) == [{'id': '42', 'name': 'Synthetic course'}]
            assert 'SYNTHETIC_' not in json.dumps(saved)
        finally:
            client.shutdown()
    assert len(received) == 2
    assert all(row['Authorization'] == 'Bearer SYNTHETIC_TOKEN' for _, row in received)
    assert 'SYNTHETIC_' not in store.path.read_text()


def test_actual_hosted_sync_uses_original_output_child_and_keeps_local_edits(api, tmp_path, native_settings_action):  # noqa: F811
    store, url, _, *_ = api
    output = tmp_path / 'courseware'
    output.mkdir()
    config = server_config('elearning', store.root)
    config['args'][config['args'].index('--user-id') + 1] = 'local-default'
    config.hosted_connector = 'elearning'
    config['args'].append('--hosted-connector')
    client = McpClient(config)
    try:
        assert client.initialize()
        invoke(client, 'save_settings', {'payload': {'base_url': url,
            'token': 'SYNTHETIC_TOKEN', 'courses': ['42'], 'file_sync_root': str(output)}})
        result = invoke(client, 'sync_files', {})
        assert result['status'] == 'completed' and result['processed'] == 1
        saved = next(output.rglob('*.pdf'))
        assert saved.read_bytes() == b'SYNTHETIC_PDF'
        saved.write_bytes(b'LOCAL_EDITS')
        assert invoke(client, 'sync_files', {})['status'] == 'completed'
        assert saved.read_bytes() == b'LOCAL_EDITS'
        invoke(client, 'delete_settings', {})
        assert saved.read_bytes() == b'LOCAL_EDITS'
        assert json.loads(store.path.read_text())['token'] == ''
    finally:
        client.shutdown()

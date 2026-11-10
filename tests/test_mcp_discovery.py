"""Discovery errors must never look like a successful empty capability list."""
from unittest.mock import Mock

import pytest

from agent.tools.mcp.mcp_client import McpClient


def client_with(*responses):
    client = McpClient({'name': 'discovery'})
    client._send_request = Mock(side_effect=responses)
    return client


@pytest.mark.parametrize('response', [
    {'error': {'message': 'secret'}}, {}, {'result': {}},
    {'result': {'tools': None}}, {'result': {'tools': [{}]}},
    {'result': {'tools': [{'name': 'x', 'inputSchema': []}]}},
])
def test_invalid_tools_response_is_failure(response):
    with pytest.raises(ValueError, match='tool'):
        client_with(response).list_tools()


def test_tools_transport_failure_propagates():
    with pytest.raises(TimeoutError):
        client_with(TimeoutError('offline')).list_tools()


def test_tools_empty_is_valid():
    assert client_with({'result': {'tools': []}}).list_tools() == []


def test_tools_pagination():
    client = client_with(
        {'result': {'tools': [{'name': 'a', 'inputSchema': {}}], 'nextCursor': 'page2'}},
        {'result': {'tools': [{'name': 'b', 'inputSchema': {}}]}},
    )
    assert [row['name'] for row in client.list_tools()] == ['a', 'b']
    assert client._send_request.call_args_list[1].args == ('tools/list', {'cursor': 'page2'})


def test_tools_repeated_cursor_is_failure():
    client = client_with(*[{'result': {'tools': [], 'nextCursor': 'same'}}] * 2)
    with pytest.raises(ValueError, match='cursor'):
        client.list_tools()


def test_handshake_captures_provider_version():
    client = client_with({'result': {'serverInfo': {'name': 'provider', 'version': '2.3.4'}}})
    client._send_notification = Mock()
    assert client._handshake()
    assert client.server_version == '2.3.4'


def make_manager(tmp_path, monkeypatch):
    from agent.tools.tool_manager import ToolManager
    from agent.tools.capability_settings import CapabilitySettings
    from common.runtime_identity import RuntimeIdentity
    manager = object.__new__(ToolManager)
    manager.__init__()
    manager.identity = RuntimeIdentity(user_id='alice')
    manager.capability_settings = CapabilitySettings(str(tmp_path), user_id='alice')
    config = {'name': 'discovery', 'type': 'stdio', 'agentTools': ['visible', 'second']}
    monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [config])
    client = Mock()
    client._runtime_key = None  # Protocol double has no local process or OS launch identity.
    client.needs_auth = False
    client.server_version = '2.3.4'
    client.initialize.return_value = True
    client.list_tools.return_value = [dict(name=name, inputSchema={}) for name in ['visible', 'private']]
    monkeypatch.setattr('agent.tools.mcp.mcp_client.McpClient', lambda cfg: client)
    return manager, config, client


def wait_ready(manager, state='ready'):
    import time
    deadline = time.monotonic() + 3
    while manager.list_mcp_status().get('discovery') != state:
        assert time.monotonic() < deadline
        time.sleep(.01)


def test_discover_reuses_client_filters_and_preserves_switch(tmp_path, monkeypatch):
    from agent.tools.mcp.mcp_tool import McpTool
    manager, config, client = make_manager(tmp_path, monkeypatch)
    manager.capability_settings.set_enabled('tool', McpTool.name_for('discovery', 'visible'), False)
    manager._load_mcp_tools_async([config])
    old = next(iter(manager._mcp_tool_instances.values()))
    client.list_tools.return_value = [dict(name=name, inputSchema={}) for name in ['visible', 'second', 'private']]
    manager.discover_mcp_tools('discovery')
    wait_ready(manager)
    assert client.initialize.call_count == 1
    assert not client.shutdown.called
    row = manager.mcp_catalog()[0]
    assert row['version'] == '2.3.4'
    assert [tool['name'] for tool in row['tools']] == ['second', 'visible']
    assert row['tools'][1]['enabled'] is False
    assert not old.is_available()
    manager.shutdown_mcp()


def test_discovery_failure_retry_and_real_empty(tmp_path, monkeypatch):
    manager, config, client = make_manager(tmp_path, monkeypatch)
    client.list_tools.side_effect = TimeoutError('sensitive')
    manager._load_mcp_tools_async([config])
    assert manager.list_mcp_status()['discovery'] == 'failed'
    assert manager.mcp_catalog()[0]['tools'] == []
    client.list_tools.side_effect = None
    client.list_tools.return_value = []
    manager.discover_mcp_tools('discovery')
    wait_ready(manager)
    assert manager.mcp_catalog()[0]['tools'] == []
    manager.shutdown_mcp()


def test_disabled_discovery_does_not_enable_or_connect(tmp_path, monkeypatch):
    manager, config, client = make_manager(tmp_path, monkeypatch)
    manager.capability_settings.set_enabled('mcp', 'discovery', False)
    with pytest.raises(PermissionError):
        manager.discover_mcp_tools('discovery')
    assert not client.initialize.called


def test_old_discovery_cannot_publish_after_disable(tmp_path, monkeypatch):
    import threading
    manager, config, client = make_manager(tmp_path, monkeypatch)
    manager._load_mcp_tools_async([config])
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    publish = manager._publish_mcp_tools
    def observed_publish(*args):
        try:
            return publish(*args)
        finally:
            finished.set()
    monkeypatch.setattr(manager, '_publish_mcp_tools', observed_publish)
    def delayed():
        started.set()
        assert release.wait(3)
        return [dict(name='second', inputSchema={})]
    client.list_tools.side_effect = delayed
    manager.discover_mcp_tools('discovery')
    assert started.wait(3)
    manager.set_mcp_enabled('discovery', False)
    release.set()
    # Teardown empties pending immediately. Wait for the actual late publication attempt.
    assert finished.wait(3), 'Old worker did not finish its publication attempt'
    assert manager.mcp_catalog()[0]['state'] == 'disabled'
    assert not manager._mcp_tool_instances


def test_discovery_api_auth_and_validation(monkeypatch):
    import json
    from channel.desktop import desktop_channel as desktop
    manager = Mock()
    manager.mcp_catalog.return_value = [dict(name='discovery', state='pending')]
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager', lambda: manager)
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *a: None)
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(dict(name='discovery', action='discover')).encode())
    assert json.loads(desktop.McpCatalogHandler().POST())['server']['state'] == 'pending'
    manager.discover_mcp_tools.assert_called_once_with('discovery')
    manager.set_mcp_enabled.assert_not_called()
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(dict(name='discovery', action='discover', enabled=True)).encode())
    assert json.loads(desktop.McpCatalogHandler().POST())['status'] == 'error'
    def deny():
        raise PermissionError('unauthenticated')
    monkeypatch.setattr(desktop, '_require_auth', deny)
    with pytest.raises(PermissionError):
        desktop.McpCatalogHandler().POST()


@pytest.mark.parametrize('collection', ['list', 'dict'])
def test_refresh_replaces_cached_agent_wrappers_on_same_client(tmp_path, monkeypatch, collection):
    from types import SimpleNamespace
    manager, config, client = make_manager(tmp_path, monkeypatch)
    manager._load_mcp_tools_async([config])
    agent = SimpleNamespace(tools=[] if collection == 'list' else {})
    manager.sync_mcp_into_agent(agent)
    old = agent.tools[0] if collection == 'list' else next(iter(agent.tools.values()))
    manager.discover_mcp_tools('discovery')
    wait_ready(manager)
    manager.sync_mcp_into_agent(agent)
    new = agent.tools[0] if collection == 'list' else next(iter(agent.tools.values()))
    assert old.client is new.client
    assert not old.is_available() and new.is_available()
    manager.shutdown_mcp()

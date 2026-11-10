"""One existing enablement store gates discovery, cached tools and direct MCP calls."""
from types import SimpleNamespace
import pytest

from agent.tools.capability_settings import CapabilitySettings
from agent.tools.mcp.mcp_tool import McpTool
from agent.tools.base_tool import is_tool_available


def test_mcp_tool_intent_is_independent_and_survives_server_toggle(tmp_path):
    settings = CapabilitySettings(tmp_path)
    first = McpTool(None, {'name': 'list_courses'}, 'elearning')
    second = McpTool(None, {'name': 'fetch'}, 'elearning')
    other = McpTool(None, {'name': 'list_courses'}, 'other')
    for tool in (first, second, other):
        tool._capability_settings = settings
    settings.set_enabled('tool', first.name, False)
    assert not is_tool_available(first)
    assert is_tool_available(second) and is_tool_available(other)
    assert first.execute({}).status == 'error'
    assert first.execute_tool({}).status == 'error'
    settings.set_enabled('mcp', 'elearning', False)
    assert not is_tool_available(second)
    settings.set_enabled('mcp', 'elearning', True)
    first._capability_settings = CapabilitySettings(tmp_path)
    assert not is_tool_available(first)
    assert is_tool_available(second)


def test_disabled_mcp_tool_disappears_from_discovery_and_agent_sync(tmp_path, monkeypatch):
    from tests.test_capability_enablement import _manager
    manager = _manager(tmp_path, monkeypatch)
    tool = McpTool(None, {'name': 'search'}, 'Documents')
    tool._capability_settings = manager.capability_settings
    manager._mcp_tool_instances = {tool.name: tool}
    manager.tool_classes = {}
    agent = SimpleNamespace(tools={tool.name: tool})
    manager.capability_settings.set_enabled('tool', tool.name, False)
    assert manager.list_tools() == {}
    manager.sync_mcp_into_agent(agent)
    assert tool.name not in agent.tools


def test_direct_mcp_call_stops_before_connecting_when_tool_disabled(tmp_path, monkeypatch):
    from tests.test_capability_enablement import _manager
    from common.runtime_identity import identity_scope
    import pytest
    manager = _manager(tmp_path, monkeypatch)
    tool = McpTool(None, {'name': 'search'}, 'Documents')
    manager.capability_settings.set_enabled('tool', tool.name, False)
    def unexpected(*args):
        raise AssertionError('Disabled tool must not connect')
    monkeypatch.setattr(manager, '_queue_mcp_servers', unexpected)
    with identity_scope(user_id='alice'), pytest.raises(PermissionError, match='disabled'):
        manager.call_mcp_tool('Documents', 'search', {})


def test_feature_projection_exposes_same_persisted_switch_id():
    from core.capabilities.features import mcp_features, plugin_features
    rows = mcp_features(dict(name='docs', enabled=True, state='ready', tools=[dict(name='search', enabled=False)]))
    assert rows[0]['enabled'] is False
    assert rows[0]['toggleId'] == McpTool(None, {'name': 'search'}, 'docs').name
    plugin = plugin_features(dict(pluginId='stella.bills', intent='enabled', state='stopped'),
        [dict(id='bills', enabled=False, available=True, source=dict(type='plugin', plugin_ids=['stella.bills']))])
    assert plugin[0]['enabled'] is False and plugin[0]['toggleId'] == 'bills'


def test_tool_endpoint_binds_switch_to_registered_provider(monkeypatch, tmp_path):
    import json
    from channel.desktop import desktop_channel as desktop
    settings = CapabilitySettings(tmp_path)
    monkeypatch.setattr(desktop, '_require_auth', lambda: None)
    monkeypatch.setattr(desktop.web, 'header', lambda *args: None)
    monkeypatch.setattr('agent.tools.capability_settings.CapabilitySettings', lambda *args, **kwargs: settings)
    monkeypatch.setattr(desktop, '_capability_catalog', lambda: SimpleNamespace(detail=lambda kind, provider:
        dict(features=[dict(id='search', toggleId=McpTool.name_for('docs', 'search'))]) if (kind, provider) == ('mcp', 'docs') else dict(features=[])))
    body = dict(id='search', enabled=False, kind='mcp', provider='docs')
    monkeypatch.setattr(desktop.web, 'data', lambda: json.dumps(body).encode())
    result = json.loads(desktop.ToolsHandler().POST())
    assert result['status'] == 'success', result
    assert settings.enabled('tool', McpTool.name_for('docs', 'search')) is False
    body.update(id='unregistered')
    assert json.loads(desktop.ToolsHandler().POST())['status'] == 'error'
    body.update(id='search', enabled='false')
    assert json.loads(desktop.ToolsHandler().POST())['status'] == 'error'


def test_removed_task_and_provider_authorization_controls_keep_stored_restrictions(tmp_path, monkeypatch):
    import pytest
    from core.security.access_settings import FeatureAccessControl
    from core.security.resource_grants import ResourceGrantStore
    from common.runtime_identity import identity_scope
    monkeypatch.setattr('core.security.migration.ensure_migrated', lambda: None)
    store = ResourceGrantStore(tmp_path / 'grants.db')
    monkeypatch.setattr('core.security.access_settings.ResourceGrantStore', lambda: store)
    store.set_connection_restriction(user_id='owner', subject='task:default:daily', connection='mcp:docs', policy=dict(allowed=False, scopes=[]))
    before = store.connection_settings(user_id='owner', subject='task:default:daily')
    monkeypatch.setattr(FeatureAccessControl, '_subjects', lambda self: [dict(id='plugin:daily', kind='plugin', subject='daily')])
    monkeypatch.setattr(FeatureAccessControl, '_sources', lambda self: [])
    from agent.tools.tool_manager import ToolManager
    monkeypatch.setitem(ToolManager().__dict__, '_load_mcp_configs', lambda: [dict(name='daily')])
    monkeypatch.setattr('agent.permission.mcp_access.inventory', lambda: [])
    with identity_scope(user_id='owner'):
        for module, kind in [('tasks', None), ('capabilities', 'plugins'), ('capabilities', 'mcp')]:
            with pytest.raises(ValueError, match='removed'):
                FeatureAccessControl(module, 'daily', kind=kind).inventory()
    assert store.connection_settings(user_id='owner', subject='task:default:daily') == before


@pytest.mark.parametrize('stage,check_only', [('result', False), ('authorization', False), ('authorization', True)])
def test_direct_mcp_rechecks_switch_before_dispatch_and_after_call(tmp_path, monkeypatch, stage, check_only):
    import pytest
    from tests.test_capability_enablement import _manager
    from common.runtime_identity import identity_scope
    from core.security.run_policy import save_run_policy
    from core.security.resource_grants import ResourceGrantStore
    manager = _manager(tmp_path, monkeypatch)
    tool = McpTool(None, {'name': 'search'}, 'Documents')
    calls = []
    def result(*args, **kwargs):
        calls.append('target')
        if stage == 'result':
            manager.capability_settings.set_enabled('tool', tool.name, False)
        return dict(structuredContent=dict(text='private result'))
    manager._mcp_active_configs = {'Documents': dict(name='Documents', type='stdio')}
    manager._mcp_status = {'Documents': 'ready'}
    manager._mcp_generations = {'Documents': 1}
    manager._mcp_schemas = {'Documents': {'search': dict(name='search')}}
    manager._mcp_registry = SimpleNamespace(_clients={'Documents': SimpleNamespace(call_tool_result=result)})
    manager._mcp_tool_instances = {}
    monkeypatch.setattr(manager, '_queue_mcp_servers', lambda *args: None)
    monkeypatch.setattr(manager, '_mcp_admitted', lambda *args: True)
    if stage == 'authorization':
        monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [dict(name='Documents', type='stdio', settingsUrl='/Documents')])
        def authorization(*args):
            manager.capability_settings.set_enabled('tool', tool.name, False)
        monkeypatch.setattr('core.mcp.authorization.check', authorization)
    with identity_scope(user_id='alice', session_id='direct'):
        save_run_policy('session', 'direct', dict(filesystem_mode='full-access'))
        from agent.registry import get_agent_registry
        ResourceGrantStore().issue(user_id='alice', subject=f'session:{get_agent_registry().default_agent_id}:direct',
            resource=dict(kind='connection', id='mcp:Documents'), operations=['read', 'system_execute'])
        with pytest.raises(PermissionError, match='disabled'):
            manager.call_mcp_tool('Documents', 'search', {}, check_only=check_only)
        if stage == 'authorization':
            assert calls == []

"""Feature presentation is read from registered definitions, never guessed owners."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.capabilities.catalog import CapabilityCatalog


def plugin_catalog(tmp_path, contributes, *, tools=(), enabled=True, published=None):
    row = dict(pluginId='fixture.provider', name='测试提供者', intent='enabled' if enabled else 'disabled',
               state='stopped', version='1.0.0', digest=None,
               manifest={'description': '包内介绍', 'contributes': contributes}, contributes=published or {})
    manager = SimpleNamespace(inventory=lambda user: [row])
    return CapabilityCatalog(tmp_path, plugin_manager=manager, tool_rows=lambda: list(tools)), row


def test_plugin_groups_pages_and_adapter_tools_without_id_collision(tmp_path):
    tools = [dict(id='shared', title={'zh': '管理记录'}, intro={'zh': '查询和维护记录'}, enabled=True, available=True,
                  source={'type': 'plugin', 'plugin_ids': ['fixture.provider']}, inputSchema={'type': 'object', 'properties': {'year': {'type': 'integer'}}},
                  toolDescription='完整工具说明')]
    service, _ = plugin_catalog(tmp_path, {'pages': ['shared'], 'services': ['fixture.contract@1']}, tools=tools)
    features = service.detail('plugins', 'fixture.provider')['features']
    assert {(item['type'], item['id']) for item in features} == {('pages', 'shared'), ('tools', 'shared'), ('services', 'fixture.contract@1')}
    page = next(item for item in features if item['type'] == 'pages')
    tool = next(item for item in features if item['type'] == 'tools')
    assert page['pageId'] == 'shared' and page['name'] == '测试提供者'
    assert tool['source'] == 'plugin' and tool['name']['zh'] == '管理记录'
    assert tool['inputSchema']['properties']['year']['type'] == 'integer'
    assert tool['detailDescription'] == '完整工具说明'
    assert 'target' not in tool  # Expands here; does not masquerade as a Core tool.


def test_published_labels_override_declarations_and_unrelated_tools_stay_out(tmp_path):
    service, row = plugin_catalog(tmp_path, {'tools': ['search'], 'pages': ['page.main']}, tools=[
        dict(id='other', title='Other', intro='Other', source={'type': 'plugin', 'plugin_ids': ['elsewhere']}),
        dict(id='core', title='Core', intro='Core', source={'type': 'builtin', 'plugin_ids': []}),
    ], published={'tools': [{'id': 'search', 'title': '搜索资料', 'description': '检索登记的资料', 'inputSchema': {'type': 'object'}}]})
    row['state'] = 'active'
    features = service.detail('plugins', 'fixture.provider')['features']
    tool = next(item for item in features if item['type'] == 'tools')
    assert tool['name'] == '搜索资料' and tool['description'] == '检索登记的资料'
    assert len(features) == 2 and all(item['source'] == 'plugin' for item in features)


@pytest.mark.parametrize('with_adapter', [False, True])
def test_id_only_publication_preserves_declared_metadata_and_adapter_keeps_schemas(tmp_path, with_adapter):
    declaration = {'id': 'lookup', 'title': '查找文档', 'description': '按关键词检索文档',
                   'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}},
                   'outputSchema': {'type': 'object', 'properties': {'results': {'type': 'array'}}}}
    adapters = [dict(id='lookup', title='查询资料', intro='通过适配工具查询资料', enabled=True, available=True,
                     source={'type': 'plugin', 'plugin_ids': ['fixture.provider']})] if with_adapter else []
    service, _ = plugin_catalog(tmp_path, {'tools': [declaration]}, published={'tools': ['lookup']}, tools=adapters)
    feature, = service.detail('plugins', 'fixture.provider')['features']
    assert feature['name'] == ('查询资料' if with_adapter else '查找文档')
    assert feature['description'] == ('通过适配工具查询资料' if with_adapter else '按关键词检索文档')
    assert feature['inputSchema']['properties']['query']['type'] == 'string'
    assert feature['outputSchema']['properties']['results']['type'] == 'array'
    assert feature['available'] is True


@pytest.mark.parametrize('contributes,expected', [({'pages': ['one']}, ['pages']), ({'tools': ['one']}, ['tools']), ({}, [])])
def test_single_kind_empty_and_disabled_plugin_do_not_invent_other_features(tmp_path, contributes, expected):
    service, _ = plugin_catalog(tmp_path, contributes, enabled=False)
    features = service.detail('plugins', 'fixture.provider')['features']
    assert [item['type'] for item in features] == expected
    assert all(item['available'] is False for item in features)


def test_mcp_discovery_metadata_reaches_feature_details_without_config(tmp_path, monkeypatch):
    from agent.tools.mcp.mcp_client import McpClient
    from agent.tools.mcp.mcp_tool import McpTool
    from agent.tools.tool_manager import ToolManager
    client = McpClient({'name': 'docs', 'type': 'stdio', 'command': 'unused'})
    monkeypatch.setattr(client, '_send_request', lambda *args: {'result': {'tools': [{
        'name': 'lookup', 'title': '查找文档', 'description': '按关键词查询文档',
        'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']},
        'outputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True},
    }]}})
    schema = client.list_tools()[0]
    tool = McpTool(client, schema, 'docs')
    manager = object.__new__(ToolManager)
    manager.__init__()
    config = {'name': 'docs', 'type': 'stdio', 'env': {'TOKEN': 'DO_NOT_EXPOSE'}, 'headers': {'Authorization': 'DO_NOT_EXPOSE'}}
    monkeypatch.setattr(manager, '_load_mcp_configs', lambda: [config])
    manager._mcp_status = {'docs': 'ready'}
    manager._mcp_tool_instances = {tool.name: tool}
    catalog = CapabilityCatalog(tmp_path, mcp_rows=manager.mcp_catalog)
    detail = catalog.detail('mcp', 'docs')
    feature, = detail['features']
    assert feature['type'] == 'tools' and feature['source'] == 'mcp'
    assert feature['name'] == '查找文档' and feature['id'] == 'lookup'
    assert feature['inputSchema']['required'] == ['query'] and feature['outputSchema'] == {'type': 'object'}
    assert 'DO_NOT_EXPOSE' not in json.dumps(detail)
    assert tool.read_only is False  # Display metadata must not change the existing execution classification.
    assert 'lookup: 按关键词查询文档' not in json.dumps(detail['overview'], ensure_ascii=False)


@pytest.mark.parametrize('state,enabled', [('failed', True), ('disabled', False), ('not_started', True)])
def test_mcp_stale_tools_do_not_appear_as_discovered_when_not_ready(tmp_path, state, enabled):
    catalog = CapabilityCatalog(tmp_path, mcp_rows=lambda: [dict(name='offline', transport='stdio', enabled=enabled, state=state,
        tools=[dict(name='stale', description='Old tool')])])
    assert catalog.detail('mcp', 'offline')['features'] == []


def test_adapter_schema_uses_loaded_class_without_instantiating_it():
    from agent.tools.catalog import build_catalog
    class Definition:
        params = {'type': 'object', 'properties': {'path': {'type': 'string'}}}
        description = '实际注册的文件读取说明'
        def __init__(self):
            raise AssertionError('Catalog must not instantiate tools')
    settings = SimpleNamespace(enabled=lambda *args: True)
    rows = build_catalog({'read'}, settings, definitions={'read': Definition})
    row = next(row for row in rows if row['id'] == 'read')
    assert row['inputSchema']['properties']['path']['type'] == 'string'
    assert row['toolDescription'] == '实际注册的文件读取说明'


def test_declared_tool_without_publication_does_not_claim_runtime_availability(tmp_path):
    service, _ = plugin_catalog(tmp_path, {'tools': ['declared']})
    feature, = service.detail('plugins', 'fixture.provider')['features']
    assert feature['available'] is None


def test_registered_plugin_adapter_outside_static_catalog_keeps_its_definition(monkeypatch):
    class Definition:
        description = 'Search the knowledge base. Long-term memories use a different tool.'
        params = {'type': 'object', 'properties': {'query': {'type': 'string'}}}
        def __init__(self):
            raise AssertionError('Do not instantiate registered definitions')
    manager = SimpleNamespace(tool_classes={'knowledge_search': Definition}, capability_settings=SimpleNamespace(enabled=lambda *args: True))
    monkeypatch.setattr('agent.tools.catalog.tools_payload', lambda: {'tools': []})
    monkeypatch.setattr('agent.tools.tool_manager.ToolManager', lambda: manager)
    monkeypatch.setattr('agent.tools.provenance.plugin_tool_available', lambda name: True)
    tool, = CapabilityCatalog._tools()
    assert tool['id'] == 'knowledge_search' and tool['source']['plugin_ids'] == ['stella.knowledge']
    assert tool['title'] == 'Search the knowledge base.'
    assert tool['inputSchema']['properties']['query']['type'] == 'string'


def test_real_elearning_server_publishes_four_public_tool_titles_and_parameters(tmp_path):
    from agent.tools.mcp.mcp_client import McpClient
    from core.mcp.managed import PUBLIC_TOOLS
    repo = Path(__file__).resolve().parents[2] / 'MCP/fudan-elearning-mcp'
    python = repo / '.venv/bin/python'
    client = McpClient({'name': 'elearning', 'type': 'stdio', 'command': str(python) if python.is_file() else sys.executable,
                        'args': [str(repo / 'mcp_server.py'), '--data-dir', str(tmp_path)], 'timeout': 10})
    try:
        assert client.initialize()
        rows = [row for row in client.list_tools() if row['name'] in PUBLIC_TOOLS['elearning']]
        assert {row['name'] for row in rows} == {'fetch', 'list_courses', 'list_files', 'sync_files'}
        assert all(row.get('title') and row['title'] != row['name'] for row in rows)
        assert all(isinstance(row['inputSchema'].get('properties'), dict) for row in rows)
    finally:
        client.shutdown()


def test_user_tool_copy_prefers_catalog_prose_and_preserves_raw_definition(tmp_path):
    raw = "操作 action='add', amount=32.5。"
    tools = [dict(id='ledger', title='管理收支', intro={'zh': '查询和记录收支。', 'en': 'Track income and expenses.'},
                  detail={'zh': '查看每月支出。你可以说：帮我看看这个月花了多少钱。', 'en': 'Review monthly spending.'},
                  toolDescription=raw, inputSchema={'type': 'object'}, enabled=True, available=True,
                  source={'type': 'plugin', 'plugin_ids': ['fixture.provider']})]
    service, _ = plugin_catalog(tmp_path, {}, tools=tools)
    feature, = service.detail('plugins', 'fixture.provider')['features']
    assert feature['userSummary']['zh'] == '查询和记录收支。'
    assert feature['userDetails']['zh'] == '查看每月支出。\n你可以说：帮我看看这个月花了多少钱。'
    assert feature['userSummary']['en'] == 'Track income and expenses.'
    assert feature['detailDescription'] == raw
    assert feature['inputSchema'] == {'type': 'object'}


def test_mcp_user_copy_omits_code_and_only_expands_for_additional_prose(tmp_path):
    catalog = CapabilityCatalog(tmp_path, mcp_rows=lambda: [dict(name='docs', transport='stdio', enabled=True, state='ready', tools=[
        dict(name='lookup', description='查找文档。\n支持按主题寻找相关资料。\n```python\nlookup(query="test")\n```\n参数 query: string', inputSchema={'type': 'object'}),
        dict(name='short', description='列出文档。'),
        dict(name='code', description='action="list"\n{"type": "object"}')])])
    lookup, short, code = catalog.detail('mcp', 'docs')['features']
    assert lookup['userSummary']['zh'] == '查找文档。'
    assert lookup['userDetails']['zh'] == '支持按主题寻找相关资料。'
    assert short['userDetails'] == {}
    assert code['userSummary'] == {} and code['userDetails'] == {}
    assert 'lookup(query=' in lookup['description']

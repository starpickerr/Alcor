"""Reusable feature groups use real provider definitions and preserve tab boundaries."""
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

URL = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
disabled = [False]
opened = []


def plugin(identifier, name, contributes):
    return dict(pluginId=identifier, name=name, intent='enabled', state='stopped', version='1.0.0', digest=None,
                manifest={'description': '由提供者维护的介绍。', 'contributes': contributes})


with tempfile.TemporaryDirectory(prefix='stella-feature-ui-') as state:
    from agent.tools.bills.bills_tool import BillsTool
    from agent.tools.catalog import CATALOG
    from agent.tools.mcp.mcp_client import McpClient
    from core.capabilities.catalog import CapabilityCatalog
    from core.mcp.managed import PUBLIC_TOOLS

    repo = ROOT.parent / 'MCP/fudan-elearning-mcp'
    client = McpClient({'name': 'elearning', 'type': 'stdio', 'command': str(repo / '.venv/bin/python'),
                       'args': [str(repo / 'mcp_server.py'), '--data-dir', state], 'timeout': 10})
    try:
        assert client.initialize()
        discovered = [tool for tool in client.list_tools() if tool['name'] in PUBLIC_TOOLS['elearning']]
    finally:
        client.shutdown()
    bills_manifest = json.loads((ROOT.parent / 'Stella-Plugins/plugins/bills/plugin.json').read_text())
    providers = [plugin('stella.bills', '账单', bills_manifest['contributes']),
                 plugin('example.viewer', '资料查看器', {'pages': ['viewer.main']}),
                 plugin('example.second', '第二查看器', {'pages': ['viewer.main']}),
                 plugin('example.multiple', '资料处理', {'tools': [{'id': 'extract', 'title': '提取内容', 'description': '提取文档正文', 'inputSchema': {'type': 'object'}}],
                      'pages': ['multiple.main'], 'skills': [{'id': 'review', 'title': '检查流程'}], 'mcp': [{'id': 'remote', 'title': '资料连接'}],
                      'services': ['example.service@1']}),
                 plugin('example.empty', '空提供者', {})]
    adapter = next(row for row in CATALOG if row['id'] == 'bills')
    tool_rows = [dict(id='bills', title=adapter['title'], intro=adapter['intro'], detail=adapter['detail'],
                     enabled=True, available=True, source={'type': 'plugin', 'plugin_ids': ['stella.bills']},
                     inputSchema=BillsTool.params, toolDescription=BillsTool.description)]

    def inventory(user):
        return [{**row, 'intent': 'disabled' if disabled[0] and row['pluginId'] in {'stella.bills', 'example.viewer'} else 'enabled'} for row in providers]

    mcps = [dict(name='elearning', transport='stdio', enabled=True, state='ready', tools=discovered, settings_url='/elearning', description='课程服务介绍。'),
            dict(name='legacy', transport='stdio', enabled=True, state='ready', tools=[
                dict(name='search_documents', description='Search published documents. Find papers about a topic. You can say: Find papers about climate change.\nlookup(query="climate")', inputSchema={'type': 'object'}),
                dict(name='Search', title='Search', description='Search another collection', inputSchema={'type': 'object'})]),
            dict(name='offline', transport='stdio', enabled=True, state='failed', tools=[])]
    catalog = CapabilityCatalog(state, plugin_manager=SimpleNamespace(inventory=inventory), tool_rows=lambda: tool_rows, mcp_rows=lambda: mcps)

    def backend(route):
        path = urlparse(route.request.url).path
        data = {'status': 'success'}
        if path == '/auth/check':
            data.update(auth_required=False, authenticated=True)
        elif path == '/api/health':
            data['status'] = 'ok'
        elif path == '/api/models':
            data.update(providers=[{'id': 'test', 'configured': True, 'label': 'Test'}], capabilities={'chat': {'current_provider': 'test', 'current_model': 'test'}})
        elif path == '/api/capabilities':
            query = parse_qs(urlparse(route.request.url).query)
            data['detail'] = catalog.detail(query['kind'][0], query['id'][0])
        elif path == '/api/plugins/pages/open':
            body = route.request.post_data_json
            opened.append(body)
            if body['pluginId'] in {'example.viewer', 'example.second'} and body['pageId'] == 'viewer.main':
                data = {'status': 'ok', 'result': {**body, 'contentUrl': '/api/feature-page?owner=' + body['pluginId'], 'channelNonce': 'test'}}
            else:
                data = {'status': 'error', 'message': 'Page is not registered'}
        elif path == '/api/feature-page':
            route.fulfill(status=200, content_type='text/html', body='<meta charset="utf-8"><h1>提供者原生页面</h1><p>' + parse_qs(urlparse(route.request.url).query)['owner'][0] + '</p>')
            return
        elif path == '/api/plugins/runtime':
            data['plugins'] = inventory('local-default')
        elif path == '/api/plugins/settings':
            data['result'] = []
        elif path == '/api/bills/ledger':
            data.update(entries=[])
        route.fulfill(status=200, content_type='application/json', body=json.dumps(data), headers={
            'Access-Control-Allow-Origin': URL, 'Access-Control-Allow-Credentials': 'true'})

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1280, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("localStorage.setItem('stella_lang','zh'); window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null};")
            page.route('http://127.0.0.1:9876/**', backend)
            page.goto(URL + '/#/skills/mcp/elearning?section=features', wait_until='networkidle')
            panel = page.get_by_role('tabpanel')
            expect(panel.get_by_role('heading', name='提供的工具', exact=True)).to_be_visible()
            expect(panel.get_by_label('工具数量', exact=True)).to_have_text('4')
            expect(panel.locator('[data-feature-group]')).to_have_count(1)
            expect(panel.get_by_text('获取课程数据', exact=True)).to_be_visible()
            expect(panel.get_by_text('内置工具', exact=True)).to_have_count(0)
            expect(panel.get_by_role('button', name='停用', exact=True)).to_have_count(0)
            tool = panel.locator('[data-feature-id="fetch"]')
            expect(tool.locator('pre')).to_have_count(0)
            expect(panel.get_by_label('输入参数 JSON')).to_have_count(0)
            expect(panel.get_by_text('技术信息', exact=True)).to_have_count(0)
            expect(page.get_by_role('tab')).to_have_text(['功能', '配置', '关于'])
            page.screenshot(path='/tmp/stella-features-elearning.png', full_page=True)
            page.get_by_role('tab', name='关于', exact=True).click()
            expect(panel.get_by_text('提供者未报告版本', exact=True)).to_be_visible()
            expect(panel.get_by_role('button', name='检查更新', exact=True)).to_be_disabled()
            page.goto(URL + '/#/skills/mcp/legacy?section=features', wait_until='networkidle')
            for tool_name in ('search_documents', 'Search'):
                legacy_tool = panel.locator(f'[data-feature-id="{tool_name}"]')
                expect(legacy_tool.get_by_role('heading', name=tool_name, exact=True)).to_be_visible()
                expect(legacy_tool.locator('summary')).to_have_count(0)
                expect(legacy_tool.get_by_role('switch')).to_be_visible()
            expect(panel.get_by_text('技术信息', exact=True)).to_have_count(0)
            page.goto(URL + '/#/skills/plugins/stella.bills?section=features', wait_until='networkidle')
            expect(panel.locator('[data-feature-group]')).to_have_count(2)
            expect(panel.get_by_role('heading', name='页面入口', exact=True)).to_be_visible()
            expect(panel.get_by_text('管理账单', exact=True)).to_be_visible()
            expect(panel.get_by_text('stella.bills@1', exact=True)).not_to_be_visible()
            page.screenshot(path='/tmp/stella-features-bills-summary.png', full_page=True)
            user_tool = panel.locator('[data-feature-id="bills"]')
            expect(user_tool).not_to_contain_text("action=")
            expect(user_tool.locator('pre')).to_have_count(0)
            expect(panel.get_by_label('输入参数 JSON')).to_have_count(0)
            expect(user_tool).to_contain_text('账单')
            expect(user_tool.locator('summary')).to_have_count(0)
            user_tool.get_by_role('switch').focus()
            expect(user_tool.get_by_role('switch')).to_be_focused()
            expect(panel.get_by_text('技术信息', exact=True)).to_have_count(0)
            expect(panel.get_by_label('输入参数 JSON')).to_have_count(0)
            page.get_by_role('tab', name='关于', exact=True).click()
            expect(panel.get_by_text('1.0.0', exact=True)).to_be_visible()
            expect(panel.get_by_text('stella.bills@1', exact=True)).to_have_count(0)
            page.get_by_role('tab', name='配置', exact=True).click()
            expect(panel.get_by_text('stella.bills', exact=True)).to_have_count(0)
            expect(panel.get_by_text('1.0.0', exact=True)).to_have_count(0)
            page.get_by_role('tab', name='功能', exact=True).click()
            page.get_by_role('link', name='打开账单页面', exact=True).click()
            expect(page).to_have_url(URL + '/#/bills')
            page.goto(URL + '/#/skills/plugins/example.viewer?section=features', wait_until='networkidle')
            expect(panel.locator('[data-feature-group]')).to_have_count(1)
            expect(panel.get_by_role('heading', name='提供的工具', exact=True)).to_have_count(0)
            page.get_by_role('link', name='打开资料查看器页面', exact=True).click()
            expect(page.frame_locator('iframe').get_by_role('heading', name='提供者原生页面')).to_be_visible()
            expect(page.frame_locator('iframe').get_by_text('example.viewer', exact=True)).to_be_visible()
            page.evaluate("location.hash='/plugins/example.second/pages/viewer.main'")
            expect(page.frame_locator('iframe').get_by_text('example.second', exact=True)).to_be_visible()
            expect(page.frame_locator('iframe').get_by_text('example.viewer', exact=True)).to_have_count(0)
            previous_opens = len(opened)
            page.evaluate("location.hash='/plugins/example.viewer/pages/undeclared'")
            expect(page.get_by_role('alert')).to_contain_text('未登记')
            assert len(opened) == previous_opens
            page.get_by_role('link', name='返回能力详情', exact=True).click()
            expect(panel.get_by_role('heading', name='页面入口', exact=True)).to_be_visible()
            previous_opens = len(opened)
            page.goto(URL + '/#/plugins/example.viewer/pages/undeclared', wait_until='networkidle')
            expect(page.get_by_role('alert')).to_contain_text('未登记')
            assert len(opened) == previous_opens
            page.goto(URL + '/#/skills/plugins/example.multiple?section=features', wait_until='networkidle')
            expect(panel.locator('[data-feature-group]')).to_have_count(4)
            page.goto(URL + '/#/skills/plugins/example.empty?section=features', wait_until='networkidle')
            expect(panel).to_contain_text('暂无已登记的功能')
            expect(panel.locator('[data-feature-group]')).to_have_count(0)
            page.goto(URL + '/#/skills/mcp/offline?section=features', wait_until='networkidle')
            expect(panel).to_contain_text('工具发现失败')
            expect(panel.locator('[data-feature-group]')).to_have_count(0)
            disabled[0] = True
            previous_opens = len(opened)
            page.goto(URL + '/#/plugins/example.viewer/pages/viewer.main', wait_until='networkidle')
            expect(page.get_by_role('alert')).to_contain_text('不可用')
            expect(page.locator('iframe')).to_have_count(0)
            assert len(opened) == previous_opens
            page.goto(URL + '/#/skills/plugins/stella.bills?section=features', wait_until='networkidle')
            expect(panel.get_by_role('link', name='打开账单页面', exact=True)).to_have_count(0)
            expect(panel.get_by_text('管理账单', exact=True)).to_be_visible()
            page.screenshot(path='/tmp/stella-features-bills-disabled.png', full_page=True)
            for width in (390, 620):
                page.set_viewport_size({'width': width, 'height': 900})
                expect(panel.get_by_role('heading', name='提供的工具', exact=True)).to_be_visible()
                expect(panel.get_by_text('管理账单', exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                if width == 390:
                    user_tool = panel.locator('[data-feature-id="bills"]')
                    expect(user_tool.locator('pre')).to_have_count(0)
                    assert user_tool.bounding_box()['height'] < 500
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path='/tmp/stella-features-mobile.png', full_page=True)
            page.evaluate("localStorage.setItem('cow_theme','dark')")
            page.reload(wait_until='networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='/tmp/stella-features-mobile-dark.png', full_page=True)
            assert not errors, errors
            print('PASS: real eLearning tools, Bills page/tool separation, generic page hosting, dynamic groups, no technical details, keyboard, unavailable states, tab boundaries and narrow/dark layout')
        except Exception:
            page.screenshot(path='/tmp/stella-features-failure.png', full_page=True)
            print('Failure diagnostics:', page.url, page.locator('body').inner_text()[-2500:], errors, opened)
            raise
        finally:
            browser.close()

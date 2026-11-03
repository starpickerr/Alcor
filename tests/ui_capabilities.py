"""Capabilities page: horizontal tabs, concise cards, skill toggle, and load errors."""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


test_url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
request_counts = {}
fail_tools = [False]
fail_mcp = [False]
empty_mcp = [False]
plugin_enabled = [True]
plugin_posts = []
tool_enabled = {'read': True, 'memory_search': True}
mcp_enabled = {'Documents': True, 'Offline': True}
tool_posts = []
mcp_posts = []
fail_tool_save = [False]
fail_mcp_save = [False]
fail_plugin_save = [False]
hold_tool = [False]
hold_mcp = [False]
hold_plugin = [False]
pending_tool_routes = []
pending_mcp_routes = []
pending_plugin_routes = []
skill_posts = []
skill_enabled = [True]
hold_skill = [False]
fail_skill_save = [False]
pending_skill_routes = []
plan_requests = []
updated = [False]
fail_update = [False]
fail_plan = [False]
AGENT_PACKAGE = Path(__file__).resolve().parents[1] / 'stella-plugins-workspace/plugins/agent-hub'
AGENT_MANIFEST = json.loads((AGENT_PACKAGE / 'plugin.json').read_text())
INTRO = '检索 Agent 的长期记忆；启用知识库时也可检索知识内容。这份介绍需要完整显示，不能被两行省略号截断。'
DETAIL = '只在带有记忆能力的对话中可用。它可以查找记忆和已启用的知识内容。'


def mcp_row(name):
    return {'name': name, 'transport': 'stdio' if name == 'Documents' else 'sse',
            'enabled': mcp_enabled[name],
            'state': ('ready' if name == 'Documents' else 'failed') if mcp_enabled[name] else 'disabled',
            'tools': [{'name':'search_docs','description':'搜索文档内容'}] if name == 'Documents' and mcp_enabled[name] else []}


def backend(route):
    path = urlparse(route.request.url).path
    if path == '/api/capabilities':
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from capability_ui_fixtures import fulfill_capabilities
        return fulfill_capabilities(route, backend, known_plugins=(), known_mcp=())
    request_counts[path] = request_counts.get(path, 0) + 1
    method = route.request.method
    body = route.request.post_data_json if method == 'POST' else {}
    data = {'status': 'success'}
    if path == '/api/plugins/pages/open':
        data = {'status':'ok','result':{'contentUrl':'/api/plugins/pages/agent-collaboration/index.html',
            'pluginId':'stella.agent-hub','pageId':'agent-hub.main','generation':1,'channelNonce':'test-nonce'}}
    elif path == '/api/plugins/pages/agent-collaboration/index.html':
        route.fulfill(status=200,content_type='text/html',body=(AGENT_PACKAGE / 'ui/pages/agent-hub.main/index.html').read_text())
        return
    elif path == '/api/plugins/pages/agent-collaboration/app.js':
        route.fulfill(status=200,content_type='application/javascript',body='')
        return
    elif path == '/api/health':
        data = {'status': 'ok'}
    elif path == '/auth/check':
        data.update(auth_required=False, authenticated=True)
    elif path == '/api/models':
        data.update(providers=[{'id': 'test', 'configured': True, 'label': 'Test'}],
                    capabilities={'chat': {'current_provider': 'test', 'current_model': 'test'}})
    elif path == '/api/tools' and method == 'POST':
        tool_posts.append(body)
        if fail_tool_save[0]:
            data = {'status':'error','message':'工具开关保存失败'}
        else:
            tool_enabled[body['id']] = body['enabled']
            if hold_tool[0]:
                pending_tool_routes.append(route)
                return
    elif path == '/api/tools':
        if fail_tools[0]:
            data = {'status': 'error', 'message': '工具目录加载失败'}
        else:
            data['tools'] = [{
                'id': 'read',
                'source': {'type':'builtin','plugin_ids':[]},
                'title': {'zh': '读取文件', 'en': 'Read Files'},
                'intro': {'zh': '读取工作区中的文件内容。', 'en': 'Read workspace files.'},
                'detail': {'zh': '只能读取当前工作区里已有的文件。', 'en': 'Reads files in the workspace.'},
                'available': True,
                'enabled': tool_enabled['read'],
                'reason': {'zh': '', 'en': ''},
            }, {
                'id': 'memory_search',
                'source': {'type':'plugin','plugin_ids':['stella.memory']},
                'title': {'zh': '搜索记忆', 'en': 'Search Memory'},
                'intro': {'zh': INTRO, 'en': 'Search the agent memory. This introduction must stay complete.'},
                'detail': {'zh': DETAIL, 'en': 'Available only in a conversation with memory.'},
                'available': False,
                'enabled': tool_enabled['memory_search'],
                'reason': {'zh': '仅在启用记忆的对话中可用。', 'en': 'Available only in a conversation with memory.'},
            }]
    elif path == '/api/briefings/elearning':
        data['settings'] = {'enabled': False, 'configured': False, 'base_url': '', 'username': '',
                            'courses': [], 'course_options': [], 'account_id': '', 'password_set': False,
                            'password_masked': '', 'file_sync_root': ''}
    elif path == '/api/briefings/preferences':
        data['preferences'] = {'show_low_priority': True, 'show_ongoing': True, 'important_types': [],
                               'reminder_frequency': 'daily', 'summary_detail': 'brief'}
    elif path == '/api/email/accounts' or path.startswith('/api/briefings'):
        data.update(rules=[], default_text='', sources=[], items=[], settings={'mailboxes': []},
                    actions={}, authorizations=[])
    elif path in ('/api/briefings/elearning','/api/elearning/file-sync'):
        data['settings']={'enabled':False,'configured':False,'base_url':'','username':'','courses':[],'course_options':[],'account_id':'','password_set':False,'password_masked':'','file_sync_root':''}
    elif path == '/api/mcp' and method == 'POST':
        mcp_posts.append(body)
        if fail_mcp_save[0]:
            data = {'status':'error','message':'MCP 开关保存失败'}
        else:
            mcp_enabled[body['name']] = body['enabled']
            data['server'] = mcp_row(body['name'])
            if hold_mcp[0]:
                pending_mcp_routes.append(route)
                return
    elif path == '/api/mcp' and fail_mcp[0]:
        data = {'status': 'error', 'message': 'MCP 目录加载失败'}
    elif path == '/api/mcp' and empty_mcp[0]:
        data['servers'] = []
    elif path == '/api/mcp':
        data['servers'] = [mcp_row('Documents'), mcp_row('Offline'), {'name':'elearning','transport':'stdio','enabled':True,'state':'ready','tools':[],'settings_url':'/elearning'}]
    elif path == '/api/plugins/manage':
        plugin_posts.append(body)
        if body['action'] == 'update':
            if fail_update[0]:
                data = {'status': 'error', 'message': '更新失败，请重试'}
            else:
                updated[0] = True
        else:
            if fail_plugin_save[0]:
                data = {'status':'error','message':'插件开关保存失败'}
            else:
                plugin_enabled[0] = body['action'] == 'enable'
                if hold_plugin[0]:
                    pending_plugin_routes.append(route)
                    return
        data['result'] = {}
    elif path == '/api/plugins/runtime':
        data['plugins'] = [{'pluginId': 'stella.briefing', 'userId': 'test', 'name': '简报',
                            'version': '1.0.0', 'intent': 'enabled' if plugin_enabled[0] else 'disabled', 'state': 'stopped',
                            'sourceLabel': 'official', 'generation': 1, 'services': []},
                           {'pluginId':'stella.agent-hub','userId':'test','name':AGENT_MANIFEST['name'],
                            'version':AGENT_MANIFEST['version'],'intent':'enabled','state':'stopped',
                            'firstParty':True,'hideUninstall':True,'sourceLabel':'official','generation':1,'services':[]}]
    elif path == '/api/plugins/dual-update-plan':
        plan_requests.append(path)
        if fail_plan[0]:
            data = {'status': 'error', 'message': '更新目录加载失败'}
        else:
            data.update(core={'from': '1.0'}, updates=[] if updated[0] else [
                {'pluginId': 'stella.briefing', 'fromVersion': '1.0.0', 'toVersion': '1.1.0'},
                {'pluginId': 'stella.blocked', 'fromVersion': '1.0.0', 'toVersion': '2.0.0'}],
                blocked=[{'pluginId': 'stella.blocked', 'reason': '需要更新 Core'}])
    elif path == '/api/plugins/settings':
        data['result'] = []
    elif path == '/api/plugins/authorization':
        data['result'] = []
    elif path == '/api/skills' and method == 'POST':
        skill_posts.append(body)
        if fail_skill_save[0]:
            data = {'status':'error','message':'技能开关保存失败'}
        else:
            skill_enabled[0] = body['action'] == 'open'
            if hold_skill[0]:
                pending_skill_routes.append(route)
                return
    elif path == '/api/skills':
        data['skills'] = [{
            'name': 'demo-skill', 'display_name': '演示技能', 'enabled': skill_enabled[0], 'source': 'custom',
            'description': '技能介绍需要完整显示。' * 8,
        }, {
            'name': 'short-skill', 'display_name': '短技能', 'enabled': True, 'source': 'custom',
            'description': '一行介绍。',
        }, {
            'name': 'mid-skill', 'display_name': '中技能', 'enabled': True, 'source': 'custom',
            'description': '中等长度的介绍。' * 2,
        }, {
            'name': 'tiny-skill', 'display_name': '更短技能', 'enabled': True, 'source': 'custom',
            'description': '短。',
        }]
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                  headers={'Access-Control-Allow-Origin': test_url, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script("""if (window === window.top) { localStorage.setItem('stella_lang','zh'); window.electronAPI = {platform: 'darwin',
            getBackendPort: async () => 9876, onBackendStatus: () => () => {}, getBackendError: async () => null
        }; }""")
        page.route('http://127.0.0.1:9876/**', backend)
        page.goto(test_url + '/#/skills', wait_until='networkidle')
        nav = page.get_by_role('navigation')
        expect(nav.get_by_role('button', name='能力', exact=True)).to_be_visible()
        expect(nav.get_by_role('button', name='Agent 协作', exact=True)).to_be_visible()
        expect(nav.get_by_role('button', name='智能体', exact=True)).to_have_count(0)
        labels = [text.strip() for text in nav.get_by_role('button').all_text_contents()]
        assert labels.index('Agent 协作') + 1 == labels.index('事务'), labels
        expect(page.get_by_role('link', name='Agent 协作', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='能力', exact=True)).to_be_visible()
        expect(page.get_by_role('tab')).to_have_text(['全部', '插件', 'MCP', 'Skill', '内置工具'])
        expect(page.get_by_role('tab', name='全部', exact=True)).to_have_attribute('aria-selected', 'true')
        page.get_by_role('tab', name='内置工具', exact=True).click()
        expect(page.get_by_role('link', name='读取文件', exact=True)).to_be_visible()
        expect(page.get_by_role('link', name='搜索记忆', exact=True)).to_have_count(0)
        assert plan_requests == [], 'Capabilities must not check for updates'

        def check_toggle(kind, identifier, title, holds, pending, failures, posts, expected):
            page.goto(test_url + f'/#/skills/{kind}/{identifier}', wait_until='networkidle')
            expect(page.get_by_role('heading', name=title, exact=True)).to_be_visible()
            holds[0] = True
            page.get_by_role('button', name='停用', exact=True).click()
            expect(page.get_by_role('button', name='处理中…', exact=True)).to_be_disabled()
            response = {'status':'ok','result':{}} if kind == 'plugins' else {'status':'success','server':mcp_row(identifier)} if kind == 'mcp' else {'status':'success'}
            pending.pop().fulfill(status=200, content_type='application/json', body=json.dumps(response), headers={'Access-Control-Allow-Origin':test_url,'Access-Control-Allow-Credentials':'true'})
            holds[0] = False
            expect(page.get_by_role('button', name='启用', exact=True)).to_be_enabled()
            assert posts[-1] == expected
            page.reload(wait_until='networkidle')
            expect(page.get_by_role('button', name='启用', exact=True)).to_be_enabled()
            failures[0] = True
            page.get_by_role('button', name='启用', exact=True).click()
            expect(page.get_by_role('alert')).to_be_visible()
            expect(page.get_by_role('button', name='启用', exact=True)).to_be_enabled()
            failures[0] = False
            page.get_by_role('button', name='启用', exact=True).focus()
            page.keyboard.press('Space')
            expect(page.get_by_role('button', name='停用', exact=True)).to_be_enabled()

        check_toggle('tools','read','读取文件',hold_tool,pending_tool_routes,fail_tool_save,tool_posts,{'id':'read','enabled':False})
        check_toggle('skills','demo-skill','演示技能',hold_skill,pending_skill_routes,fail_skill_save,skill_posts,{'name':'demo-skill','action':'close'})
        check_toggle('plugins','stella.briefing','简报',hold_plugin,pending_plugin_routes,fail_plugin_save,plugin_posts,{'pluginId':'stella.briefing','action':'disable'})
        check_toggle('mcp','Documents','Documents',hold_mcp,pending_mcp_routes,fail_mcp_save,mcp_posts,{'name':'Documents','enabled':False})
        page.goto(test_url + '/#/skills/tools/memory_search', wait_until='networkidle')
        expect(page.get_by_role('heading', name='搜索记忆', exact=True)).to_be_visible()
        expect(page.get_by_text(DETAIL, exact=True)).to_be_visible()
        expect(page.get_by_text('插件提供', exact=False)).to_be_visible()
        expect(page.get_by_role('tab')).to_have_count(0)
        expect(page.get_by_role('tabpanel')).to_contain_text(DETAIL)
        fail_tools[0] = True
        page.goto(test_url + '/#/skills', wait_until='networkidle')
        expect(page.get_by_role('alert')).to_contain_text('部分目录暂不可用')
        expect(page.get_by_role('link', name='Documents', exact=True)).to_be_visible()
        fail_tools[0] = False
        page.get_by_role('button', name='重试', exact=True).click()
        expect(page.get_by_role('alert')).to_have_count(0)
        page.get_by_role('tab', name='插件', exact=True).focus()
        page.keyboard.press('ArrowRight')
        expect(page.get_by_role('tab', name='MCP', exact=True)).to_be_focused()
        page.keyboard.press('End')
        expect(page.get_by_role('tab', name='内置工具', exact=True)).to_be_focused()
        page.set_viewport_size({'width':620,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path='/tmp/stella-capabilities-narrow.png',full_page=True)
        page.set_viewport_size({'width':1280,'height':900})
        nav.get_by_role('button', name='Agent 协作', exact=True).click()
        frame = page.frame_locator('iframe[title="Agent 协作"]')
        expect(frame.get_by_role('heading', name='Agent 协作', exact=True)).to_be_visible()
        assert page.url.endswith('/#/agent-hub')
        page.screenshot(path='/tmp/stella-agent-collaboration-desktop.png', full_page=True)
        page.set_viewport_size({'width':620,'height':900})
        expect(nav.get_by_role('button', name='Agent 协作', exact=True)).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='/tmp/stella-agent-collaboration-narrow.png', full_page=True)
        page.set_viewport_size({'width':1280,'height':900})
        page.goto(test_url + '/#/settings?tab=about', wait_until='networkidle')
        expect(page.get_by_role('heading', name='Core', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='Plugins', exact=True)).to_be_visible()
        expect(page.get_by_text('1.0.0 → 1.1.0', exact=True)).to_be_visible()
        expect(page.get_by_text('需要更新 Core', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='更新 stella.blocked')).to_be_disabled()
        fail_update[0] = True
        page.get_by_role('button', name='更新 stella.briefing').click()
        expect(page.get_by_role('alert')).to_contain_text('更新失败，请重试')
        fail_update[0] = False
        page.get_by_role('button', name='更新 stella.briefing').click()
        expect(page.get_by_role('button', name='更新 stella.briefing')).to_have_count(0)
        expect(page.get_by_role('status').filter(has_text='更新完成')).to_be_visible()
        fail_plan[0] = True
        page.get_by_role('button', name='检查插件更新', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('更新目录加载失败')
        expect(page.get_by_text('插件已是最新版本')).to_have_count(0)
        fail_plan[0] = False
        page.get_by_role('button', name='检查插件更新', exact=True).click()
        expect(page.get_by_text('需要更新 Core', exact=True)).to_be_visible()
        page.screenshot(path='/tmp/stella-about-updates.png', full_page=True)
        assert errors == [], errors
        print('PASS: uniform plugin/tool/skill/MCP switches, persisted intent, save errors, busy guards, keyboard, desktop/narrow layouts')
    finally:
        browser.close()

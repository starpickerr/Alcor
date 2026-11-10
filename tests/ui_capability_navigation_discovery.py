"""Source ownership, About and async MCP discovery use the production projection."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
import sys

from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.capabilities.catalog import CapabilityCatalog

URL = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
servers = [dict(name=name, transport='stdio', enabled=True, state='not_started', tools=[], settings_url='/'+name) for name in ['elearning', 'other-school']]
plugins = [dict(pluginId='example.owner', name='示例插件', version='1.2.3', intent='enabled', state='stopped', manifest={})]
sources = [dict(id=key, name=key, provider='elearning', kind='course', enabled=True, available=True, account_id=key, connection=owner) for key, owner in [('a', 'mcp:elearning'), ('b', 'mcp:elearning'), ('c', 'mcp:other-school')]]
sources += [dict(id='plugin', name='Plugin source', provider='custom', kind='data', enabled=True, available=True, connection='plugin:example.owner'), dict(id='unmapped', name='Unmapped source', provider='unknown', kind='data', enabled=True, available=True, connection='invalid:owner')]
settings = dict(enabled=True, configured=True, base_url='https://example.test', username='synthetic-user', courses=['42'], course_options=[dict(id='42', name='测试课程')], account_id='synthetic', password_set=True, password_masked='********', file_sync_root='/tmp/files')
requests, saved = [], []
phase = ['success']
reads = [0]
update_error = [False]
update_available = [True]
catalog = CapabilityCatalog('/unused', plugin_manager=SimpleNamespace(inventory=lambda user: plugins), mcp_rows=lambda: servers, tool_rows=lambda: [], skill_manager=SimpleNamespace(get_skills_config=lambda: {}))


def backend(route):
    parsed = urlparse(route.request.url)
    path, method = parsed.path, route.request.method
    requests.append((path, method))
    data = dict(status='success', sessions=[], providers=[], history=[], projects=[])
    if path == '/auth/check':
        data.update(auth_required=False, authenticated=True)
    elif path == '/api/models':
        data.update(providers=[dict(id='test', configured=True)], capabilities=dict(chat=dict(current_provider='test', current_model='test')))
    elif path == '/api/capabilities':
        q = parse_qs(parsed.query)
        if 'id' in q:
            if q['id'] == ['elearning'] and servers[0]['state'] == 'pending':
                reads[0] += 1
                if phase[0] != 'hold' and reads[0] >= 3:
                    servers[0].update(state='failed' if phase[0] == 'failure' else 'ready', version='2.3.4')
                    servers[0]['tools'] = [] if phase[0] in {'failure', 'empty'} else [dict(name='list_courses', title='查询课程', description='查询课程列表', inputSchema={})]
            data['detail'] = catalog.detail(q['kind'][0], q['id'][0])
        else:
            data = catalog.list()
    elif path == '/api/data':
        data.update(sources=sources, usages=[], usage_consumers=[], records=[], storage=dict(available=True, categories=[]))
    elif path == '/api/briefings/elearning':
        if method == 'POST':
            body = route.request.post_data_json
            saved.append(body)
            settings.update({k: v for k, v in body.items() if k != 'password'})
        data['settings'] = settings
    elif path == '/api/briefings/elearning/test':
        data['courses'] = settings['course_options']
    elif path == '/api/elearning/file-sync':
        data['settings'] = settings
    elif path == '/api/mcp' and method == 'POST':
        body = route.request.post_data_json
        assert body == dict(name='elearning', action='discover')
        servers[0].update(state='pending', tools=[])
        reads[0] = 0
        data['server'] = servers[0]
    elif path == '/api/plugins/dual-update-plan':
        data = dict(status='error', message='检查服务暂不可用') if update_error[0] else dict(status='success', updates=[*([dict(pluginId='example.owner', fromVersion='1.2.3', toVersion='1.3.0')] if update_available[0] else []), dict(pluginId='other', fromVersion='1', toVersion='99')], blocked=[])
    elif path == '/api/plugins/runtime':
        data['plugins'] = plugins
    elif path == '/api/plugins/settings':
        data.update(status='ok', result=[])
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data), headers={'Access-Control-Allow-Origin': URL, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport=dict(width=1280, height=950))
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.add_init_script("localStorage.setItem('stella_lang','zh'); window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null};")
        page.route('http://127.0.0.1:9876/**', backend)
        origin = URL + '/#/data?section=sources&filter=kept'
        page.goto(origin, wait_until='networkidle')
        school = page.get_by_test_id('data-source-elearning:mcp:elearning')
        expect(school).to_have_attribute('href', '#/skills/mcp/elearning?section=configuration')
        expect(page.get_by_test_id('data-source-elearning:mcp:other-school')).to_have_attribute('href', '#/skills/mcp/other-school?section=configuration')
        expect(page.get_by_test_id('data-source-custom')).to_have_attribute('href', '#/skills/plugins/example.owner?section=configuration')
        expect(page.get_by_test_id('data-source-unknown')).to_have_attribute('aria-disabled', 'true')
        expect(page.get_by_test_id('data-source-unknown').get_by_role('link')).to_have_count(0)
        expect(page.locator('[data-testid^="data-source-"]')).to_have_count(4)
        expect(page.get_by_text('账号与连接配置', exact=True)).to_have_count(0)
        expect(page.get_by_text('访问权限', exact=True)).to_have_count(0)
        expect(page.get_by_label('学工号')).to_have_count(0)
        # Source origin is recorded before navigation, including scroll within its scroller.
        page.set_viewport_size(dict(width=700, height=430))
        school.scroll_into_view_if_needed()
        page.locator('[data-stella-scroll]').evaluate('el => el.scrollTop += 25')
        scroll = page.locator('[data-stella-scroll]').evaluate('el => el.scrollTop')
        school.click()
        panel = page.get_by_role('tabpanel')
        expect(panel.get_by_label('学工号')).to_have_value('synthetic-user')
        panel.get_by_label('学工号').fill('changed-user')
        panel.get_by_role('button', name='测试连接并读取课程').click()
        expect(panel.get_by_role('status').filter(has_text='连接成功')).to_be_visible()
        page.reload(wait_until='networkidle')
        panel.get_by_label('校园密码').fill('synthetic-password')
        panel.get_by_role('button', name='保存', exact=True).first.click()
        expect(panel.get_by_role('status').filter(has_text='已保存')).to_be_visible()
        assert saved[-1]['password'] == 'synthetic-password'
        page.get_by_role('link', name='返回原页面').click()
        expect(page).to_have_url(origin)
        assert abs(page.locator('[data-stella-scroll]').evaluate('el => el.scrollTop') - scroll) < 5
        assert not any(path == '/api/data/access' for path, _ in requests)
        page.set_viewport_size(dict(width=1280, height=950))
        page.goto(URL + '/#/skills/mcp/elearning', wait_until='networkidle')
        expect(page.get_by_role('tab')).to_have_text(['功能', '配置', '关于'])
        expect(page.get_by_role('tab', name='功能')).to_have_attribute('aria-selected', 'true')
        expect(panel.get_by_text('技术信息', exact=True)).to_have_count(0)
        page.get_by_role('tab', name='关于').click()
        expect(panel).to_contain_text('提供者未报告版本')
        expect(panel.get_by_role('button', name='检查更新')).to_be_disabled()
        page.get_by_role('tab', name='功能').click()
        panel.get_by_role('button', name='发现工具').click()
        expect(panel).to_contain_text('正在连接并发现工具')
        expect(panel.get_by_role('heading', name='查询课程')).to_be_visible()
        page.get_by_role('tab', name='关于').click()
        expect(panel.get_by_text('2.3.4', exact=True)).to_be_visible()
        for value, expected in [('failure', '工具发现失败'), ('empty', '服务未提供可用的公开工具')]:
            phase[0] = value
            page.get_by_role('tab', name='功能').click()
            panel.get_by_role('button', name='重试' if servers[0]['state'] == 'failed' else '发现工具', exact=True).click()
            expect(panel).to_contain_text(expected)
        phase[0] = 'hold'
        panel.get_by_role('button', name='发现工具').click()
        expect(panel).to_contain_text('正在连接并发现工具')
        page.get_by_role('tab', name='关于').click()
        count = reads[0]
        page.wait_for_timeout(1600)
        assert reads[0] == count, 'Leaving Features must stop discovery polling'
        servers[0].update(enabled=False, state='disabled')
        page.goto(URL + '/#/skills/mcp/elearning', wait_until='networkidle')
        expect(panel.get_by_role('button', name='发现工具')).to_be_disabled()
        page.goto(URL + '/#/skills/plugins/example.owner?section=about', wait_until='networkidle')
        expect(panel.get_by_text('1.2.3', exact=True)).to_be_visible()
        panel.get_by_role('button', name='检查更新').click()
        expect(panel.get_by_role('status')).to_have_text('发现新版本：1.3.0')
        expect(panel).not_to_contain_text('99')
        assert not any(path == '/api/plugins/manage' and method == 'POST' for path, method in requests)
        update_available[0] = False
        panel.get_by_role('button', name='检查更新').click()
        expect(panel.get_by_role('status')).to_have_text('未发现可用更新。')
        update_error[0] = True
        panel.get_by_role('button', name='检查更新').click()
        expect(panel.get_by_role('alert')).to_contain_text('检查服务暂不可用')
        for width in [1280, 390]:
            page.set_viewport_size(dict(width=width, height=900))
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f'/tmp/stella-capability-about-{width}.png', full_page=True)
        assert not errors, errors
        print('PASS: source owners/grouping/unmapped, shared config/back scroll/query, tabs, discovery success/failure/empty/disabled/poll cleanup, version and scoped update check, narrow layout')
    finally:
        browser.close()

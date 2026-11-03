"""Feature switches and configuration use existing API state."""
import json
import os
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright, expect

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
intent = dict(fetch=True, list_courses=True, list_files=True, sync_files=True)
names = dict(fetch='获取课程数据', list_courses='查询课程', list_files='查询文件', sync_files='同步课件')
saved = []
settings = dict(enabled=True, configured=True, base_url='https://example.test', username='synthetic-user', courses=['42'], course_options=[dict(id='42', name='测试课程')], account_id='synthetic', password_set=True, password_masked='********', file_sync_root='/tmp/course-files')


def backend(route):
    path = urlparse(route.request.url).path
    data = dict(status='success')
    if path == '/api/capabilities':
        q = parse_qs(urlparse(route.request.url).query)
        kind, provider = q.get('kind', ['mcp'])[0], q.get('id', ['elearning'])[0]
        data['detail'] = dict(kind=kind, id=provider, name='eLearning', description='原有介绍', overview='原有概览', enabled=True, state='ready', source='configured', version=None, tabs=['features','configuration','about'], runtime=dict(state='ready'), configuration=dict(settingsUrl='/elearning') if provider == 'elearning' else {}, features=[dict(id=k, type='tools', name=v, userSummary='原有简介', userDetails='旧展开详情', enabled=intent[k], toggleId='canonical_'+k) for k,v in names.items()])
    elif path == '/api/tools' and route.request.method == 'POST':
        body = json.loads(route.request.post_data)
        saved.append(body)
        intent[body['id']] = body['enabled']
    elif path == '/api/briefings/elearning':
        if route.request.method == 'POST':
            body = json.loads(route.request.post_data)
            saved.append(body)
        data['settings'] = settings
    elif path == '/api/elearning/file-sync':
        data['settings'] = settings
    elif path == '/api/data':
        data.update(sources=[], records=[])
    elif path == '/api/plugins/runtime':
        data['plugins'] = [dict(pluginId='example.plugin', name='示例插件', intent='enabled', state='active')]
    elif path == '/api/plugins/settings':
        data = dict(status='ok', result=[])
    elif path == '/api/models':
        data.update(providers=[dict(id='test', configured=True)], capabilities=dict(chat=dict(current_provider='test', current_model='test')))
    elif path == '/api/sessions':
        data.update(sessions=[], total=0, has_more=False)
    elif path == '/auth/check':
        data.update(authenticated=True, auth_required=False)
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data), headers={'Access-Control-Allow-Origin':url,'Access-Control-Allow-Credentials':'true'})


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport=dict(width=1280, height=950))
        page.add_init_script("localStorage.setItem('stella_lang','zh'); window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null};")
        page.route('http://127.0.0.1:9876/**', backend)
        page.goto(url+'/#/skills/mcp/elearning?section=features', wait_until='networkidle')
        panel = page.get_by_role('tabpanel')
        row = panel.locator('[data-feature-id="list_courses"]')
        expect(row.locator('summary')).to_have_count(0)
        switch = row.get_by_role('switch', name='查询课程')
        expect(switch).to_have_attribute('aria-checked','true')
        switch.click()
        expect(switch).to_have_attribute('aria-checked','false')
        expect(panel.locator('[data-feature-id="fetch"]').get_by_role('switch')).to_have_attribute('aria-checked','true')
        page.get_by_role('tab',name='关于',exact=True).click()
        page.get_by_role('tab',name='功能',exact=True).click()
        expect(row.get_by_role('switch')).to_have_attribute('aria-checked','false')
        page.reload(wait_until='networkidle')
        expect(row.get_by_role('switch')).to_have_attribute('aria-checked','false')
        assert saved[-1] == dict(id='list_courses',enabled=False,kind='mcp',provider='elearning')
        expect(panel.get_by_text('旧展开详情')).to_have_count(0)
        page.get_by_role('tab',name='配置',exact=True).click()
        expect(panel.get_by_text('访问权限',exact=True)).to_have_count(0)
        expect(panel.get_by_label('学工号')).to_have_value('synthetic-user')
        panel.get_by_label('校园密码').fill('synthetic-password')
        panel.get_by_role('button',name='保存',exact=True).first.click()
        expect(panel.get_by_role('status').filter(has_text='已保存')).to_be_visible()
        assert saved[-1]['password'] == 'synthetic-password'
        expect(panel.get_by_role('switch')).to_have_count(0)
        page.reload(wait_until='networkidle')
        expect(panel.get_by_label('学工号')).to_have_value('synthetic-user')
        page.set_viewport_size(dict(width=440,height=900))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='/tmp/stella-capability-config.png',full_page=True)
        page.goto(url+'/#/skills/plugins/example.plugin?section=features',wait_until='networkidle')
        expect(panel.get_by_role('switch')).to_have_count(4)
        panel.locator('[data-feature-id="fetch"]').get_by_role('switch').click()
        expect(panel.locator('[data-feature-id="fetch"]').get_by_role('switch')).to_have_attribute('aria-checked','false')
        assert saved[-1]['kind'] == 'plugins'
        print('PASS: independent switches, persistence, shared account config, plugin parity, narrow layout')
    finally:
        browser.close()

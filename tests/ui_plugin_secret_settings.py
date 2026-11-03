"""Original plugin configuration supports empty drafts, repair, and retained credential cleanup."""
import json
import os
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
removed, fail_save = False, True
conflict_remove = True
saves = []
snapshot = dict(revision=1, values={}, secretFields=['password', 'token'],
                secretStates={'password': 'repair_required', 'token': 'unavailable'})
detail = dict(kind='plugins', id='stella.sample.alpha', name='测试插件', enabled=True, state='stopped',
              source='installed', version='1.0.0', description='插件设置测试', overview='插件设置测试',
              tabs=['features', 'configuration', 'about'], runtime={}, features=[])
descriptor = dict(id='preferences', categories=[dict(id='general', title='连接设置')], fields=[
    dict(key=key, title=title, category='general', type='string', secret=True)
    for key, title in [('password', '密码'), ('token', '令牌')]])
retained = [dict(settingsId='retired', category='old', field='old-token', title='旧令牌', state='configured', revision=4, retired=True)]


def backend(route):
    global fail_save, conflict_remove
    path = urlparse(route.request.url).path
    data = dict(status='success', sessions=[], history=[], messages=[], projects=[], agents=[], total=0,
                channels=[], skills=[], tools=[], servers=[], items=[], errors={})
    if path == '/api/health':
        data['status'] = 'ok'
    elif path == '/auth/check':
        data.update(auth_required=False, authenticated=True)
    elif path == '/api/capabilities':
        data.update(detail={**detail, **({'retained': True, 'enabled': False, 'state': 'removed'} if removed else {})}, items=[detail])
    elif path == '/api/models':
        blank = {'current_provider': '', 'current_model': '', 'providers': []}
        data.update(providers=[{'id': 'test', 'configured': True, 'label': 'Test'}], capabilities={
            'chat': {'current_provider': 'test', 'current_model': 'test', 'providers': ['test']},
            'vision': blank, 'image': blank, 'asr': blank, 'embedding': blank,
            'tts': {**blank, 'current_voice': '', 'reply_mode': 'off'},
            'search': {'strategy': 'auto', 'providers': [], 'fixed_provider': '', 'current_provider': ''},
        })
    elif path == '/api/plugins/runtime':
        data = dict(status='ok', plugins=[] if removed else [dict(pluginId='stella.sample.alpha', userId='alice',
            name='测试插件', version='1.0.0', intent='enabled', state='stopped')])
    elif path == '/api/plugins/settings':
        body = route.request.post_data_json
        assert body['pluginId'] == 'stella.sample.alpha'
        action = body['action']
        result = None
        if action == 'describe':
            if removed:
                data = dict(status='error', message='插件已卸载')
            else:
                result = [descriptor]
        elif action == 'credentials':
            result = retained.copy()
        elif action == 'read':
            result = snapshot.copy()
        elif action == 'save':
            saves.append(body.copy())
            assert body['changes'].keys() == {'password'}
            if fail_save:
                fail_save = False
                data = dict(status='error', message='安全存储暂不可用')
            else:
                assert body['expectedRevision'] == snapshot['revision']
                snapshot['revision'] += 1
                snapshot['secretStates']['password'] = 'configured' if body['changes']['password'] else 'not_configured'
                result = snapshot.copy()
        elif action == 'remove-secret':
            assert body == dict(action=action, pluginId='stella.sample.alpha', settingsId='retired', category='old', field='old-token', expectedRevision=4 if conflict_remove else 5)
            if conflict_remove:
                conflict_remove = False
                retained[0]['revision'] = 5
                data = dict(status='error', message='凭据已变化，请刷新')
            else:
                retained.clear()
                result = []
        else:
            raise AssertionError(action)
        if data.get('status') != 'error':
            data = dict(status='ok', result=result)
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                  headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('stella_lang','zh'); window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null,getAppVersion:async()=>'1.0.0'}")
        page.route('http://127.0.0.1:9876/**', backend)
        page.goto(url + '/#/skills/plugins/stella.sample.alpha?section=configuration', wait_until='networkidle')
        password = page.get_by_label('密码', exact=True)
        expect(password).to_have_attribute('type', 'password')
        expect(password).to_have_value('')
        expect(page.get_by_label('令牌', exact=True)).to_have_value('')
        expect(page.get_by_text('需要修复', exact=True)).to_be_visible()
        expect(page.get_by_text('暂不可用', exact=True)).to_be_visible()
        password.fill('SYNTHETIC_BROWSER_SECRET')
        page.get_by_role('button', name='保存本类别', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('安全存储暂不可用')
        expect(password).to_have_value('SYNTHETIC_BROWSER_SECRET')
        page.get_by_role('button', name='保存本类别', exact=True).click()
        expect(password).to_have_value('')
        expect(page.get_by_text('已配置', exact=True).first).to_be_visible()
        page.screenshot(path='/tmp/stella-plugin-secret-desktop.png', full_page=True)
        page.get_by_role('button', name='删除密码', exact=True).click()
        page.get_by_role('button', name='保存本类别', exact=True).click()
        expect(password).to_have_value('')
        expect(page.get_by_text('未配置', exact=True)).to_be_visible()
        assert [item['changes'] for item in saves] == [{'password': 'SYNTHETIC_BROWSER_SECRET'}, {'password': 'SYNTHETIC_BROWSER_SECRET'}, {'password': ''}]
        assert [item['expectedRevision'] for item in saves] == [1, 1, 2]
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('button', name='收起侧栏', exact=True).click()
        page.screenshot(path='/tmp/stella-plugin-secret-narrow.png', full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        removed = True
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('button', name='删除旧令牌', exact=True)).to_be_enabled()
        page.get_by_role('button', name='删除旧令牌', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('凭据已变化')
        page.get_by_role('button', name='刷新凭据状态', exact=True).click()
        page.get_by_role('button', name='删除旧令牌', exact=True).click()
        expect(page.get_by_role('button', name='删除旧令牌', exact=True)).to_have_count(0)
        assert not errors, errors
        print('PASS: original plugin secret repair, failed-save draft, empty-after-save, deletion and removed-package cleanup; desktop/390px')
    finally:
        browser.close()

"""Original eLearning page repairs one credential and preserves unrelated drafts."""
import json
import os
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
from capability_ui_fixtures import fulfill_capabilities

URL = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
settings = dict(enabled=True, configured=True, base_url='https://elearning.fudan.edu.cn', username='alice',
    courses=['42'], course_options=[{'id': '42', 'name': '课程甲'}, {'id': '43', 'name': '课程乙'}],
    account_id='synthetic-account', password_set=True, password_masked='********', file_sync_root='/keep/courseware',
    credential_states={'password': 'repair_required', 'token': 'configured', 'session': 'unavailable'},
    credential_revision='revision-1', session_revision='session-1')
saved, errors = [], []
fail = [False]


def backend(route):
    path = urlparse(route.request.url).path
    if path == '/api/capabilities':
        return fulfill_capabilities(route, backend, known_mcp=('email', 'elearning'))
    result = dict(status='success', tools=[], servers=[], skills=[], sources=[], plugins=[], result=[], updates=[], blocked=[])
    if path == '/api/health':
        result = {'status': 'ok'}
    elif path == '/auth/check':
        result.update(auth_required=False, authenticated=True)
    elif path == '/api/models':
        result.update(providers=[dict(id='test', configured=True, label='Test')],
                      capabilities={'chat': {'current_provider': 'test', 'current_model': 'test'}})
    elif path == '/api/data':
        result.update(sources=[{'provider': 'elearning', 'enabled': True, 'available': True}], errors=[])
    elif path in {'/api/briefings/elearning', '/api/elearning/file-sync'}:
        if route.request.method == 'POST':
            payload = route.request.post_data_json
            saved.append(payload)
            if fail[0]:
                result.update(status='error', message='保存失败，请刷新后重试')
            elif 'credential_field' in payload:
                field = payload['credential_field']
                settings['credential_states'][field] = 'configured' if payload['credential_value'] else 'not_configured'
                if field == 'password':
                    settings.update(credential_revision='revision-2', session_revision='session-2')
                elif field == 'token':
                    settings.update(credential_revision='revision-3', session_revision='session-3')
                else:
                    settings['session_revision'] = 'session-cleared'
            else:
                settings.update({key: value for key, value in payload.items() if key not in ('password', 'token')})
        result['settings'] = settings
    elif path == '/api/briefings/elearning/test':
        result.update(connected=True, courses=settings['course_options'])
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result),
                  headers={'Access-Control-Allow-Origin': URL, 'Access-Control-Allow-Credentials': 'true',
                           'Access-Control-Allow-Headers': 'Content-Type,Authorization'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("""localStorage.setItem('stella_lang','zh'); window.electronAPI = {
        platform:'darwin', getBackendPort:async()=>9876, onBackendStatus:()=>()=>{},
        getBackendError:async()=>null, getAppVersion:async()=>'9.9.9'};""")
    page.route('http://127.0.0.1:9876/**', backend)
    try:
        page.goto(URL + '/#/elearning', wait_until='networkidle')
        password = page.get_by_label('校园密码', exact=True)
        expect(password).to_have_value('')
        token = page.get_by_label('API Token（可选）', exact=True)
        expect(token).to_have_value('')
        expect(page.get_by_text('需要修复', exact=True)).to_be_visible()
        expect(page.get_by_text('已保存登录状态 · 不可用', exact=True)).to_be_visible()
        password.fill('SYNTHETIC_REPAIRED')
        token.fill('SYNTHETIC_OTHER_DRAFT')
        page.get_by_role('checkbox', name='课程乙', exact=True).check()
        fail[0] = True
        page.get_by_role('button', name='更新密码', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('保存失败')
        expect(password).to_have_value('SYNTHETIC_REPAIRED')
        fail[0] = False
        page.get_by_role('button', name='更新密码', exact=True).click()
        expect(password).to_have_value('')
        expect(token).to_have_value('SYNTHETIC_OTHER_DRAFT')
        expect(page.get_by_role('checkbox', name='课程乙', exact=True)).to_be_checked()
        assert saved[-1] == {'credential_field': 'password', 'credential_value': 'SYNTHETIC_REPAIRED',
                             'credential_revision': 'revision-1'}
        page.get_by_role('button', name='删除 Token', exact=True).click()
        expect(token).to_have_value('')
        assert saved[-1] == {'credential_field': 'token', 'credential_value': '', 'credential_revision': 'revision-2'}
        page.get_by_role('button', name='清除登录状态', exact=True).click()
        expect(page.get_by_role('button', name='清除登录状态', exact=True)).to_be_disabled()
        assert saved[-1] == {'credential_field': 'session', 'credential_value': '', 'credential_revision': 'session-3'}
        settings['credential_states']['password'] = 'unavailable'
        page.get_by_role('button', name='刷新凭据状态', exact=True).click()
        expect(page.get_by_text('不可用', exact=True)).to_be_visible()
        page.get_by_label('学工号', exact=True).fill('another-account')
        expect(page.get_by_role('button', name='删除密码', exact=True)).to_be_disabled()
        page.screenshot(path='/tmp/stella-elearning-credentials-desktop.png', full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('button', name='收起侧栏', exact=True).click()
        password.scroll_into_view_if_needed()
        expect(password).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='/tmp/stella-elearning-credentials-narrow.png', full_page=True)
        assert not errors, errors
        print('PASS: original eLearning credential states, repair, per-field removal, failed save, drafts and 390px')
    finally:
        browser.close()

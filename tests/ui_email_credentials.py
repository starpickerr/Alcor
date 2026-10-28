"""Original mailbox entry: four states, empty drafts, recovery and narrow view."""
import json
import os
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
from capability_ui_fixtures import fulfill_capabilities

URL = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
rows = [dict(id='alice', enabled=True, host='imap.example.test', user='alice@example.test', port=993,
             folder='INBOX', configured=True, missing=[], password_set=True, password_masked='********',
             credential_status='repair_required', credential_revision='revision-1')]
saved = []
fail = [False]


def backend(route):
    path = urlparse(route.request.url).path
    if path == '/api/capabilities':
        return fulfill_capabilities(route, backend, known_mcp=('email',))
    result = dict(status='success', tools=[], servers=[], skills=[], sources=[], plugins=[],
                  result=[], updates=[], blocked=[])
    if path == '/api/health':
        result = {'status': 'ok'}
    elif path == '/auth/check':
        result.update(auth_required=False, authenticated=True)
    elif path == '/api/models':
        result.update(providers=[dict(id='test', configured=True, label='Test')],
                      capabilities={'chat': {'current_provider': 'test', 'current_model': 'test'}})
    elif path == '/api/email/accounts':
        if route.request.method == 'POST':
            payload = route.request.post_data_json
            saved.append(payload)
            if fail[0]:
                result.update(status='error', message='保存失败，请刷新后重试')
            elif payload.get('action') == 'delete':
                rows.clear()
            else:
                rows[0].update(credential_status='configured', credential_revision='revision-2')
                result['mailbox'] = rows[0]
        result['settings'] = dict(mailboxes=rows, configured=bool(rows), missing=[])
    elif path == '/api/email/accounts/test':
        result.update(connected=True, folder_accessible=True, folders=[dict(name='INBOX', label='收件箱')])
    route.fulfill(status=200, content_type='application/json', body=json.dumps(result),
                  headers={'Access-Control-Allow-Origin': URL, 'Access-Control-Allow-Credentials': 'true',
                           'Access-Control-Allow-Headers': 'Content-Type,Authorization'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 1000})
    page.add_init_script("""localStorage.setItem('stella_lang','zh'); window.electronAPI = {
        platform:'darwin', getBackendPort:async()=>9876, onBackendStatus:()=>()=>{},
        getBackendError:async()=>null, getAppVersion:async()=>'9.9.9'};""")
    page.route('http://127.0.0.1:9876/**', backend)
    try:
        page.goto(URL + '/#/email', wait_until='networkidle')
        password = page.locator('input[autocomplete="new-password"]')
        expect(password).to_have_value('')
        expect(page.get_by_text('需要修复', exact=True)).to_be_visible()
        password.fill('SYNTHETIC_REPAIRED')
        fail[0] = True
        page.get_by_role('button', name='保存', exact=True).click()
        expect(page.get_by_role('alert')).to_contain_text('保存失败')
        expect(password).to_have_value('SYNTHETIC_REPAIRED')
        fail[0] = False
        page.get_by_role('button', name='保存', exact=True).click()
        expect(password).to_have_value('')
        assert saved[-1]['password'] == 'SYNTHETIC_REPAIRED'
        assert saved[-1]['credential_revision'] == 'revision-1'
        expect(page.get_by_text('已配置', exact=True)).to_be_visible()
        rows[0]['credential_status'] = 'unavailable'
        page.get_by_role('button', name='刷新凭据状态', exact=True).click()
        expect(page.get_by_text('不可用', exact=True)).to_be_visible()
        expect(password).to_have_value('')
        rows[0]['credential_status'] = 'configured'
        page.get_by_role('button', name='刷新凭据状态', exact=True).click()
        expect(page.get_by_text('已配置', exact=True)).to_be_visible()
        page.get_by_role('button', name='测试连接', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='连接成功')).to_be_visible()
        page.screenshot(path='/tmp/stella-email-credentials-desktop.png', full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('button', name='收起侧栏', exact=True).click()
        expect(password).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        password.scroll_into_view_if_needed()
        page.screenshot(path='/tmp/stella-email-credentials-narrow.png', full_page=True)
        rows[0].update(credential_status='not_configured', password_set=False, configured=False)
        page.get_by_role('button', name='刷新凭据状态', exact=True).click()
        expect(page.get_by_text('未配置', exact=True)).to_be_visible()
        page.get_by_role('button', name='删除', exact=True).click()
        expect(page.get_by_text('alice@example.test', exact=True)).to_have_count(0)
        expect(password).to_have_value('')
        print('PASS: original email credential repair/delete, four states, empty drafts, failure recovery and 390px')
    finally:
        browser.close()

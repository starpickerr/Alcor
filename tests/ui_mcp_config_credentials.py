"""Static authentication stays in the original MCP configuration with per-field repair."""
import json
import os
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
state = dict(revision='v1', transport='streamable-http', fields=[
    dict(area='headers', key='Authorization', state='repair_required'),
    dict(area='headers', key='X-API-Key', state='unavailable')])
detail = dict(kind='mcp', id='Documents', name='Documents', enabled=True, state='ready', source='streamable-http',
              version=None, description='测试文档服务', overview='测试文档服务', tabs=['features', 'configuration', 'about'], runtime={}, features=[])
fail_save = True
saves = []


def backend(route):
    global fail_save
    path = urlparse(route.request.url).path
    data = dict(status='success', sessions=[], history=[], messages=[], projects=[], agents=[], total=0,
                channels=[], skills=[], tools=[], servers=[], items=[], errors={})
    if path == '/api/health':
        data['status'] = 'ok'
    elif path == '/auth/check':
        data.update(auth_required=False, authenticated=True)
    elif path == '/api/capabilities':
        data.update(detail=detail, items=[detail])
    elif path == '/api/models':
        blank = {'current_provider': '', 'current_model': '', 'providers': []}
        data.update(providers=[{'id': 'test', 'configured': True, 'label': 'Test'}], capabilities={
            'chat': {'current_provider': 'test', 'current_model': 'test', 'providers': ['test']},
            'vision': blank, 'image': blank, 'asr': blank, 'embedding': blank,
            'tts': {**blank, 'current_voice': '', 'reply_mode': 'off'},
            'search': {'strategy': 'auto', 'providers': [], 'fixed_provider': '', 'current_provider': ''},
        })
    elif path == '/api/mcp':
        body = route.request.post_data_json
        assert body['name'] == 'Documents'
        if body['action'] == 'oauth-status':
            data['oauth'] = dict(state='not-configured', configured=False, saved=False, requires_reset=False,
                enabled=True, can_authorize=True, authorizing=False, authorization_url=None, expires_at=None, reason='')
        elif body['action'] == 'credential-status':
            data['credentials'] = state.copy()
        elif body['action'] == 'credential-save':
            assert set(body) == {'name', 'action', 'area', 'key', 'value', 'expectedRevision'}
            assert body['area'] == 'headers' and body['key'] == 'Authorization'
            saves.append(body.copy())
            if fail_save:
                fail_save = False
                state['revision'] = 'v2'
                data = dict(status='error', message='配置已变化，请刷新后保存')
            else:
                assert body['expectedRevision'] == state['revision']
                state['revision'] = 'v3' if body['value'] else 'v4'
                state['fields'][0]['state'] = 'configured' if body['value'] else 'not_configured'
                data['credentials'] = state.copy()
        else:
            raise AssertionError(body)
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
        page.goto(url + '/#/skills/mcp/Documents?section=configuration', wait_until='networkidle')
        panel = page.get_by_role('region', name='认证凭据', exact=True)
        expect(panel).to_be_visible()
        authorization = panel.get_by_label('Authorization', exact=True)
        other = panel.get_by_label('X-API-Key', exact=True)
        expect(authorization).to_have_attribute('type', 'password')
        expect(authorization).to_have_value('')
        expect(other).to_have_value('')
        expect(panel.get_by_text('需要修复', exact=True)).to_be_visible()
        expect(panel.get_by_text('暂不可用', exact=True)).to_be_visible()
        authorization.fill('Bearer SYNTHETIC_BROWSER_MCP')
        other.fill('SYNTHETIC_UNSAVED_OTHER')
        panel.get_by_role('button', name='保存Authorization', exact=True).click()
        expect(panel.get_by_role('alert')).to_contain_text('配置已变化')
        expect(authorization).to_have_value('Bearer SYNTHETIC_BROWSER_MCP')
        panel.get_by_role('button', name='刷新凭据状态', exact=True).click()
        expect(authorization).to_have_value('Bearer SYNTHETIC_BROWSER_MCP')
        panel.get_by_role('button', name='保存Authorization', exact=True).click()
        expect(authorization).to_have_value('')
        expect(other).to_have_value('SYNTHETIC_UNSAVED_OTHER')
        expect(panel.get_by_text('已配置', exact=True)).to_be_visible()
        expect(page.get_by_role('heading', name='OAuth 授权', exact=True)).to_have_count(1)
        other.fill('')
        page.screenshot(path='/tmp/stella-mcp-static-desktop.png', full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('button', name='收起侧栏', exact=True).click()
        panel.get_by_role('button', name='删除Authorization', exact=True).click()
        expect(authorization).to_have_value('')
        expect(panel.get_by_text('未配置', exact=True)).to_be_visible()
        expect(panel.get_by_text('暂不可用', exact=True)).to_be_visible()
        panel.scroll_into_view_if_needed()
        page.screenshot(path='/tmp/stella-mcp-static-narrow.png', full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert [item['expectedRevision'] for item in saves] == ['v1', 'v2', 'v3']
        assert [item['value'] for item in saves] == ['Bearer SYNTHETIC_BROWSER_MCP', 'Bearer SYNTHETIC_BROWSER_MCP', '']
        assert not errors, errors
        print('PASS: original MCP static credential repair, per-field drafts, CAS refresh, empty inputs and deletion; desktop/390px')
    finally:
        browser.close()

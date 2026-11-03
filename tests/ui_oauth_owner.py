"""Original MCP configuration exposes OAuth repair/removal at desktop and narrow sizes."""
import json
import os
import time
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
actions = []
state = dict(state='repair-required', configured=False, saved=True, requires_reset=False, enabled=True,
             can_authorize=True, authorizing=False, authorization_url=None, expires_at=None, reason='Saved token needs repair.')
detail = dict(kind='mcp', id='Documents', name='Documents', enabled=True, state='needs_auth',
              source='streamable-http', version=None, description='Synthetic documents', overview='Synthetic documents',
              tabs=['features', 'configuration', 'about'], runtime={'state': 'needs_auth'}, features=[])


def backend(route):
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
        assert body['name'] == 'Documents' and set(body) == {'name', 'action'}
        action = body['action']
        actions.append(action)
        if action == 'credential-status':
            data['credentials'] = dict(revision='synthetic', transport='streamable-http', fields=[])
            route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                          headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})
            return
        if action == 'oauth-authorize':
            state.update(authorization_url='https://auth.example.invalid/authorize?state=synthetic-state', expires_at=time.time() + 600)
        elif action == 'oauth-cancel':
            state.update(authorization_url=None, expires_at=None)
        elif action == 'oauth-remove':
            state.update(state='not-configured', configured=False, saved=False, requires_reset=False,
                         can_authorize=True, authorization_url=None, expires_at=None, reason='')
        data['oauth'] = dict(state)
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                  headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_init_script("localStorage.setItem('stella_lang','zh'); window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null,getAppVersion:async()=>'1.0.0'}")
        page.route('http://127.0.0.1:9876/**', backend)
        page.goto(url + '/#/skills/mcp/Documents?section=configuration', wait_until='networkidle')
        authorization_panel = page.get_by_role('region', name='OAuth 授权', exact=True)
        expect(page.get_by_role('heading', name='OAuth 授权', exact=True)).to_be_visible()
        expect(authorization_panel.get_by_text('需要修复', exact=True)).to_be_visible()
        page.get_by_role('button', name='重新授权', exact=True).click()
        link = page.get_by_role('link', name='前往授权页面', exact=True)
        expect(link).to_have_attribute('href', 'https://auth.example.invalid/authorize?state=synthetic-state')
        expect(link).to_have_attribute('rel', 'noopener noreferrer')
        page.get_by_role('button', name='取消授权流程', exact=True).click()
        expect(link).to_have_count(0)
        expect(authorization_panel.get_by_text('需要修复', exact=True)).to_be_visible()
        page.screenshot(path='/tmp/stella-oauth-owner-desktop.png', full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_role('button', name='收起侧栏', exact=True).click()
        state.update(state='unavailable', configured=None, can_authorize=False, reason='Unlock the credential store and refresh.')
        page.get_by_role('button', name='刷新授权状态', exact=True).click()
        expect(authorization_panel.get_by_text('暂不可用', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='重新授权', exact=True)).to_be_disabled()
        page.get_by_role('button', name='删除已保存授权', exact=True).click()
        expect(authorization_panel.get_by_text('未配置', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='开始授权', exact=True)).to_be_enabled()
        page.get_by_role('button', name='开始授权', exact=True).click()
        expect(link).to_be_visible()
        state.update(authorization_url=None, authorizing=True)
        expect(authorization_panel.get_by_text('正在完成授权', exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='开始授权', exact=True)).to_be_disabled()
        state.update(state='configured', configured=True, saved=True, authorizing=False, expires_at=None, reason='')
        expect(authorization_panel.get_by_text('已配置', exact=True)).to_be_visible()
        expect(link).to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        expect(authorization_panel.locator('input[type=password]')).to_have_count(0)
        page.screenshot(path='/tmp/stella-oauth-owner-narrow.png', full_page=True)
        assert actions.count('oauth-authorize') == 2 and actions.count('oauth-remove') == 1
        assert not errors, errors
        print('PASS: original MCP OAuth repair/cancel/remove/configured/unavailable; desktop and 390px; no credential fields')
    finally:
        browser.close()

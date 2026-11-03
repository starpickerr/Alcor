"""Owner confirms retained MCP data in the original execution environment page."""
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
sources = [{'id': 'legacy', 'path': '/private/retained/legacy'},
           {'id': 'previous', 'path': '/private/retained/previous-version'}]
row = {'kind': 'mcp', 'id': 'retained-example', 'mode': 'unavailable',
       'reason': '请确认私有数据归属', 'storage_migration': {'token': 'current', 'sources': sources}}
posts = []
fail = True


def backend(route):
    global fail
    path = urlparse(route.request.url).path
    data = {'status': 'success', 'sessions': [], 'history': [], 'messages': [], 'projects': [],
            'agents': [], 'items': [], 'servers': [], 'tools': [], 'channels': [], 'skills': []}
    if path == '/api/health':
        data['status'] = 'ok'
    elif path == '/auth/check':
        data.update(auth_required=False, authenticated=True)
    elif path == '/api/models':
        blank = {'current_provider': '', 'current_model': '', 'providers': []}
        data.update(providers=[{'id': 'test', 'configured': True, 'label': 'Test'}], capabilities={
            'chat': {'current_provider': 'test', 'current_model': 'test', 'providers': ['test']},
            'vision': blank, 'image': blank, 'asr': blank, 'embedding': blank,
            'tts': {**blank, 'current_voice': '', 'reply_mode': 'off'},
            'search': {'strategy': 'auto', 'providers': [], 'fixed_provider': '', 'current_provider': ''},
        })
    elif path == '/api/security/runtime':
        if route.request.method == 'POST':
            body = route.request.post_data_json
            posts.append(body)
            assert body == {'action': 'adopt-storage', 'kind': 'mcp', 'id': row['id'],
                            'token': row['storage_migration']['token'], 'sourceId': 'previous'}
            if fail:
                fail = False
                row['storage_migration']['token'] = 'refreshed'
                route.fulfill(json={'status': 'error', 'message': '数据已变化，请刷新后重新确认'})
                return
            row.pop('storage_migration')
            row.update(mode='stopped', reason='数据已保留，等待启动')
        data.update(isolation={'status': 'enabled', 'reason': ''}, runtimes=[row])
    elif path == '/api/security/center':
        data['summary'] = {'activity_counts': None}
    elif path == '/api/security/records':
        data['records'] = []
    elif path == '/api/security/confirmations':
        data['pending'] = []
    route.fulfill(json=data, headers={'Access-Control-Allow-Origin': url,
                                     'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 850})
    page.add_init_script("localStorage.setItem('stella_lang','zh'); window.electronAPI = {platform:'darwin', getBackendPort:async()=>9876, onBackendStatus:()=>()=>{}, getBackendError:async()=>null};")
    page.route('http://127.0.0.1:9876/**', backend)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url + '/#/security?tab=runtime')
    entry = page.get_by_role('button', name='确认数据归属', exact=True)
    expect(entry).to_be_visible()
    entry.click()
    source = page.get_by_label('保留数据来源')
    confirm = page.get_by_role('button', name='确认并复制', exact=True)
    expect(confirm).to_be_disabled()
    source.select_option('previous')
    page.get_by_role('button', name='取消', exact=True).click()
    assert not posts
    expect(source).to_have_count(0)
    entry.click()
    expect(confirm).to_be_disabled()
    source.select_option('previous')
    screenshots = Path(os.environ.get('STELLA_UI_SCREENSHOTS', '/tmp/stella-final-ui'))
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / 'mcp-storage-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    sidebar = page.get_by_role('button', name='收起侧栏', exact=True)
    if sidebar.is_visible():
        sidebar.click()
    confirm.click()
    expect(page.get_by_role('alert')).to_contain_text('数据已变化')
    expect(source).to_have_value('previous')
    page.get_by_role('button', name='刷新', exact=True).last.click()
    expect(page.get_by_role('alert')).to_have_count(0)
    expect(source).to_have_value('previous')
    page.screenshot(path=str(screenshots / 'mcp-storage-narrow.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    expect(confirm).to_be_enabled()
    confirm.click()
    expect(entry).to_have_count(0)
    expect(page.get_by_role('status')).to_contain_text('数据归属已确认')
    assert len(posts) == 2 and not errors, errors
    browser.close()
    print('PASS: MCP ownership selection, cancel, conflict, refresh, copy and desktop/narrow layout')

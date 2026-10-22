"""Log workbench interactions use deterministic API fixtures, never user logs."""
import json
import os
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

URL = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5186')
requests = []
fail = [False]
rows = [
    dict(id='one', timestamp='2026-10-22T08:32:11Z', level='ERROR', source='briefing', component='plugin',
         location='actions.py:291', message='简报事务同步失败', detail='简报事务同步失败\nPermissionError: scoped grant denied\n  request_id=req-42'),
    dict(id='two', timestamp='2026-10-22T08:32:10Z', level='INFO', source='email', component='mcp',
         location='source.py:18', message='邮箱采集完成，新增 12 条记录', detail='邮箱采集完成，新增 12 条记录'),
    dict(id='three', timestamp='2026-10-22T08:31:09Z', level='WARNING', source='tasks', component='plugin',
         location='service.py:167', message='提醒同步等待重试', detail='提醒同步等待重试'),
]


def backend(route):
    path = urlparse(route.request.url).path
    data = {'status': 'success'}
    if path == '/api/health':
        data['status'] = 'ok'
    elif path == '/auth/check':
        data.update(auth_required=False, authenticated=True)
    elif path == '/api/models':
        data.update(providers=[{'id': 'test', 'configured': True}], capabilities={'chat': {'current_provider': 'test', 'current_model': 'test'}})
    elif path == '/api/logs/entries':
        query = parse_qs(urlparse(route.request.url).query)
        requests.append(query)
        if fail[0]:
            data.update(status='error', message='日志读取失败，请重试')
        else:
            selected = rows[:]
            if query.get('q'):
                selected = [row for row in selected if query['q'][0] in row['detail']]
            if query.get('source'):
                selected = [row for row in selected if row['source'] == query['source'][0]]
            if query.get('levels'):
                selected = [row for row in selected if row['level'] in query['levels'][0].split(',')]
            if query.get('cursor'):
                selected = [{**rows[1], 'id': 'older', 'message': '更早的邮箱日志'}]
            data.update(entries=selected, next_cursor=None if query.get('cursor') else 'history',
                        sources={'briefing': 1, 'email': 1, 'tasks': 1}, truncated=False, scanned_bytes=1024)
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                  headers={'Access-Control-Allow-Origin': URL, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 950}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("localStorage.setItem('stella_lang','zh');window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null};")
    page.route('http://127.0.0.1:9876/**', backend)
    page.goto(URL + '/#/logs', wait_until='networkidle')
    expect(page.get_by_role('heading', name='日志', exact=True)).to_be_visible()
    expect(page.get_by_label('搜索日志')).to_be_visible()
    expect(page.get_by_text('简报事务同步失败', exact=True)).to_be_visible()
    page.screenshot(path='/tmp/stella-logs-desktop.png', full_page=True)
    page.get_by_label('搜索日志').fill('采集')
    expect(page.get_by_text('简报事务同步失败', exact=True)).to_have_count(0)
    expect(page.get_by_text('邮箱采集完成，新增 12 条记录', exact=True)).to_be_visible()
    page.get_by_label('搜索日志').fill('')
    page.get_by_label('日志级别').select_option('ERROR,CRITICAL')
    expect(page.get_by_text('提醒同步等待重试', exact=True)).to_have_count(0)
    page.get_by_role('button', name='查看日志详情').first.click()
    dialog = page.get_by_role('dialog', name='日志详情')
    expect(dialog).to_contain_text('request_id=req-42')
    page.screenshot(path='/tmp/stella-logs-details.png', full_page=True)
    page.keyboard.press('Escape')
    page.get_by_label('日志级别').select_option('')
    with page.expect_response(lambda response: '/api/logs/entries' in response.url):
        page.get_by_role('button', name='恢复实时更新').click()
    expect(page.get_by_text('邮箱采集完成，新增 12 条记录', exact=True)).to_be_visible()
    expect(page.locator('[aria-busy="true"]')).to_have_count(0)
    page.get_by_role('button', name='暂停实时更新').click()
    before = len(requests)
    page.wait_for_timeout(3300)
    assert len(requests) == before
    page.get_by_role('button', name='加载更早日志').click()
    expect(page.get_by_text('更早的邮箱日志', exact=True)).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role('button', name='导出当前结果').click()
    assert download.value.suggested_filename.endswith('.jsonl')
    exported = open(download.value.path()).read()
    assert len(exported.splitlines()) == 4 and '更早的邮箱日志' in exported
    fail[0] = True
    page.get_by_role('button', name='刷新日志').click()
    expect(page.get_by_role('alert')).to_contain_text('日志读取失败')
    fail[0] = False
    page.get_by_role('button', name='刷新日志').click()
    expect(page.get_by_role('alert')).to_have_count(0)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='收起侧栏', exact=True).click()
    page.get_by_label('功能来源').select_option('briefing')
    expect(page.get_by_text('简报事务同步失败', exact=True)).to_be_visible()
    page.screenshot(path='/tmp/stella-logs-mobile-list.png', full_page=True)
    page.get_by_role('button', name='查看日志详情').first.click()
    expect(page.get_by_role('dialog', name='日志详情')).to_be_visible()
    assert page.get_by_role('dialog').evaluate('el => getComputedStyle(el.querySelector("h2")).color === getComputedStyle(el).color')
    page.screenshot(path='/tmp/stella-logs-mobile.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.keyboard.press('Escape')
    dark = browser.new_page(viewport={'width': 1440, 'height': 950}, color_scheme='dark')
    dark.on('pageerror', lambda error: errors.append(str(error)))
    dark.add_init_script("localStorage.setItem('stella_lang','en');localStorage.setItem('cow_theme','dark');window.electronAPI={platform:'darwin',getBackendPort:async()=>9876,onBackendStatus:()=>()=>{},getBackendError:async()=>null};")
    dark.route('http://127.0.0.1:9876/**', backend)
    dark.goto(URL + '/#/logs', wait_until='networkidle')
    expect(dark.get_by_label('Search logs')).to_be_visible()
    assert dark.evaluate("document.documentElement.classList.contains('dark')")
    dark.screenshot(path='/tmp/stella-logs-dark.png', full_page=True)
    dark.clock.install()
    dark.get_by_label('Time range').select_option('15')
    expect(dark.get_by_text('简报事务同步失败', exact=True)).to_be_visible()
    initial_since = requests[-1]['since'][0]
    dark.clock.fast_forward(60_000)
    dark.wait_for_function('!document.querySelector("[aria-busy=true]")')
    current_since = requests[-1]['since'][0]
    assert (datetime.fromisoformat(current_since) - datetime.fromisoformat(initial_since)).total_seconds() >= 59
    dark.get_by_role('button', name='Pause live updates').click()
    dark.clock.fast_forward(60_000)
    dark.get_by_role('button', name='Load older logs').click()
    expect(dark.get_by_text('更早的邮箱日志', exact=True)).to_be_visible()
    assert requests[-1]['since'][0] == current_since
    dark.close()
    assert not errors, errors
    print('PASS logs: search, severity/source, detail, pause, history, export, failure/recovery, desktop/mobile')
    browser.close()

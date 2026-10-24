"""Shared editor binds every request to its business context, including CAS errors."""
import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

URL = 'http://127.0.0.1:5173'
saved = []
context = {}
errors = []
rejected = [False]


def backend(route):
    parsed = urlparse(route.request.url)
    data = dict(status='success', sessions=[], history=[], projects=[], models=[], providers=[])
    if parsed.path == '/api/security/runtime-trust':
        data.update(runtimes=[], isolation=dict(status='enabled', reason='OS isolation'))
    if parsed.path.endswith('/access'):
        module = parsed.path.split('/')[2]
        if route.request.method == 'POST':
            body = route.request.post_data_json
            assert body['id'] == context['id'] and module == context['module']
            saved.append(body)
            if rejected[0]:
                data = dict(status='error', message='Authorization revision changed; refresh and retry')
        else:
            assert parse_qs(parsed.query)['id'] == [context['id']]
        subject = 'task:default:daily' if module == 'tasks' else 'plugin:viewer' if context.get('kind') == 'plugins' else 'session:default:chat'
        data.update(grants=[], inherited=[], subjects=[dict(id=subject, kind='task' if module == 'tasks' else 'plugin' if context.get('kind') == 'plugins' else 'session', subject=subject)],
                    available=[dict(kind='connection' if context.get('kind') == 'mcp' else 'folder', id='mcp:docs' if context.get('kind') == 'mcp' else 'school', name='School', operations=['read'])])
        if context.get('kind') == 'mcp':
            data['connection'] = dict(id='mcp:docs', allowed=True, scopes=['read'], revision=3)
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data), headers={'Access-Control-Allow-Origin': URL, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 950})
        page.add_init_script("localStorage.setItem('stella_lang','zh')")
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('http://127.0.0.1:9876/**', backend)
        for value in [dict(module='data', id='school'), dict(module='sessions', id='chat', agent='default')]:
            context.clear()
            context.update(value)
            page.goto(URL, wait_until='networkidle')
            page.evaluate("""async context => {
                const module = await import('/src/__test__/SecurityAuthorizationHarness.tsx');
                module.mountSecurityAuthorizationTest(context)
            }""", context)
            expect(page.get_by_role('heading', name='访问权限', exact=True)).to_be_visible()
            try:
                expect(page.get_by_label('特定资源')).to_be_visible(timeout=5000)
            except Exception:
                print('Context diagnostics:', context, page.locator('body').inner_text(), errors)
                raise
            page.get_by_label('特定资源').select_option('connection:mcp:docs' if value.get('kind') == 'mcp' else 'folder:school')
            page.get_by_role('button', name='授予资源访问', exact=True).click()
            expect(page.get_by_role('status').filter(has_text='资源授权已保存')).to_be_visible()
            assert saved[-1]['purposes'] == (['background'] if value['module'] == 'tasks' else ['interactive'])
            if value.get('kind') == 'mcp':
                rejected[0] = True
                page.get_by_role('button', name='保存连接访问范围', exact=True).click()
                expect(page.get_by_role('alert')).to_contain_text('revision changed')
                assert saved[-1]['revision'] == 3 and saved[-1]['scopes'] == ['read']
                rejected[0] = False
            page.set_viewport_size({'width': 390, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=f"/tmp/stella-access-{value['module']}-{value.get('kind', 'source')}.png", full_page=True)
            page.set_viewport_size({'width': 1280, 'height': 950})
        assert not errors, errors
        print('PASS: source/task/plugin/MCP contexts, fixed subjects, independent purposes, scoped writes, revision rejection and narrow layout')
    finally:
        browser.close()

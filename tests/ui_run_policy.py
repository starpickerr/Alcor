"""Three immediate permission choices, truthful errors and no security management UI."""
import json
import os
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
policies = {name: {'filesystem_mode': 'workspace-write', 'policy_revision': 1} for name in ('one', 'two')}
saved = []
reject = [False]
requests = []


def backend(route):
    request = route.request
    path = urlparse(request.url).path
    requests.append(path)
    assert not path.startswith('/api/security/'), path
    assert path not in ('/api/sessions/access', '/api/capabilities/access')
    data = {'status': 'success'}
    if path == '/api/sessions':
        data.update(sessions=[], total=0, page=1, page_size=50, has_more=False)
    elif path == '/api/run-policy':
        if request.method == 'POST':
            body = json.loads(request.post_data)
            subject = body['subject']
            saved.append(body)
            assert set(body['policy']) <= {'filesystem_mode', 'permission_mode', 'policy_revision'}
            if reject[0]:
                data = {'status': 'error', 'message': '权限版本已变化，请重试'}
            else:
                assert body['policy']['policy_revision'] == policies[subject]['policy_revision']
                policies[subject] = {**body['policy'], 'policy_revision': policies[subject]['policy_revision'] + 1}
        else:
            subject = parse_qs(urlparse(request.url).query)['subject'][0]
        if data['status'] == 'success':
            data.update(policy=policies[subject], migration_status='complete')
    elif path.endswith('/settings') and '/sessions/' in path:
        subject = path.split('/')[-2]
        policy = policies.get(subject, policies['two'])
        data.update(model={'model': '', 'provider': '', 'source': 'global', 'global': {'model': '', 'provider': ''}, 'providers': []},
                    permission={'mode': policy['filesystem_mode'], 'source': 'independent', 'policy': policy,
                                'mode_definitions': [], 'presets': ['read-only', 'workspace-write', 'full-access']})
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                  headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("localStorage.setItem('stella_lang', 'zh')")
    page.route('http://127.0.0.1:9876/**', backend)
    page.goto(url)
    page.wait_for_load_state('networkidle')
    expect(page.locator('a[href="#/security"]')).to_have_count(0)
    page.route(url + '/policy-test', lambda route: route.fulfill(content_type='text/html', body='''
      <!doctype html><html><body><script type="module">
      import '/@vite/client';
      import RefreshRuntime from '/@react-refresh';
      RefreshRuntime.injectIntoGlobalHook(window);
      window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
      window.__vite_plugin_react_preamble_installed__ = true;
      await import('/src/index.css');
      const {mountRunPolicyTest} = await import('/src/__test__/RunPolicyHarness.tsx');
      mountRunPolicyTest();
      </script></body></html>'''))
    page.goto(url + '/policy-test')
    one, two = page.get_by_test_id('policy-one'), page.get_by_test_id('policy-two')
    expect(one.get_by_role('radiogroup', name='权限模式')).to_be_visible()
    assert one.get_by_role('radio').count() == 3
    for text in ('保存权限', '刷新', '自定义', '权限版本', '安全中心'):
        expect(page.get_by_text(text, exact=True)).to_have_count(0)
    one.get_by_role('radio', name='只读', exact=True).click()
    expect(one.get_by_role('status')).to_contain_text('已保存')
    assert saved[-1]['policy'] == {'filesystem_mode': 'read-only', 'permission_mode': 'read-only', 'policy_revision': 1}
    expect(two.get_by_role('radio', name='工作区写入', exact=True)).to_be_checked()
    reject[0] = True
    one.get_by_role('radio', name='完全访问', exact=True).click()
    expect(one.get_by_role('alert')).to_contain_text('权限版本已变化')
    expect(one.get_by_role('radio', name='只读', exact=True)).to_be_checked()
    expect(one.get_by_text('权限已保存。', exact=True)).to_have_count(0)
    reject[0] = False
    one.get_by_role('button', name='重试', exact=True).click()
    one.get_by_role('radio', name='完全访问', exact=True).click()
    expect(one.get_by_role('status')).to_contain_text('已保存')
    expect(one.get_by_role('radio', name='完全访问', exact=True)).to_be_checked()
    page.screenshot(path='/tmp/stella-run-policy-desktop.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path='/tmp/stella-run-policy-narrow.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.evaluate("""async () => {
      const module = await import('/src/__test__/PermissionSelectorHarness.tsx'); await module.mountPermissionSelector('two')
    }""")
    page.locator('#permission-test-root').evaluate("node => { node.style.zIndex = '100000' }")
    chip = page.locator('#permission-selector-chip')
    chip.get_by_role('button').first.click()
    expect(chip.get_by_role('button', name='自定义', exact=True)).to_have_count(0)
    expect(chip.get_by_role('button', name='设置对话权限', exact=True)).to_have_count(0)
    expect(chip.get_by_role('link')).to_have_count(0)
    chip.get_by_role('button', name='只读', exact=True).click()
    expect(chip.get_by_role('button').first).to_contain_text('只读')
    assert policies['two']['filesystem_mode'] == 'read-only'
    assert policies['one']['filesystem_mode'] == 'full-access'
    assert not errors, errors
    browser.close()
print('PASS: three immediate modes, independent subjects, revision failure/retry, no management UI, desktop and narrow layout')

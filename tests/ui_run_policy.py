"""Browser acceptance for independent subject controls and conservative operation choices."""
import json
import os
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

url = os.environ.get('STELLA_TEST_URL', 'http://127.0.0.1:5173')
policies = {name: {'filesystem_mode': 'workspace-write', 'capabilities': {
    'plugin:stella.tasks': {'read': 'allow', 'write': 'deny', 'delete': 'confirm'}}, 'policy_revision': 1}
    for name in ('one', 'two')}
saved = []
reject = [False]
grants = []
legacy_grant = {'id': 'legacy-course', 'server': 'email', 'consumer': 'stella.briefing', 'capabilities': ['email.read'],
    'methods': ['fetch_messages'], 'resources': [{'kind': 'folder', 'id': 'school'}], 'purposes': ['interactive'],
    'valid_until': None, 'revision': 1, 'revoked': True, 'scoped': True}


def backend(route):
    request = route.request
    path = urlparse(request.url).path
    data = {'status': 'success'}
    if path == '/api/security/run-policy':
        if request.method == 'POST':
            body = json.loads(request.post_data)
            subject = body['subject']
            saved.append(body)
            if reject[0]:
                data = {'status': 'error', 'message': '权限版本已变化，请刷新重试'}
            else:
                policies[subject] = {**body['policy'], 'policy_revision': policies[subject]['policy_revision'] + 1}
        else:
            subject = parse_qs(urlparse(request.url).query)['subject'][0]
        if data['status'] == 'success':
            data.update(policy=policies[subject], migration_status='complete', compatibility_restrictions={}, capabilities=[
                {'id': 'plugin:stella.tasks', 'name': '事务', 'source': 'plugin', 'installed': True, 'enabled': True, 'runtime_state': 'active'}])
    elif path == '/api/sessions/access':
        if request.method == 'POST':
            body = json.loads(request.post_data)
            assert body['id'] == 'one'
            saved.append(body)
            if body['action'] == 'connection':
                assert body['connection_id'] == 'browser' and body['revision'] == 7 and body['allowed'] is True
            elif body['action'] == 'issue':
                grants.append({**body, 'grant_id': 'grant-one', 'revision': 1, 'effective': True, 'revoked': False})
            elif body['action'] == 'revoke':
                grants[0].update(revoked=True, effective=False, revision=2)
            elif body['action'] == 'legacy':
                legacy_grant.update({key: value for key, value in body.items() if key not in {'action', 'id', 'legacy_id'}}, revision=legacy_grant['revision'] + 1)
        data.update(grants=grants, subjects=[dict(id='session:default:one', kind='session', subject='one')],
                    available=[dict(kind='folder', id='school', name='测试邮箱', operations=['read'])], inherited=[legacy_grant],
                    restrictions=[dict(id='browser', allowed=request.method == 'POST', scopes=['read'], revision=8 if request.method == 'POST' else 7)])
    elif path == '/api/security/runtime':
        data.update(isolation={'status': 'enabled', 'reason': 'OS isolation enabled'}, runtimes=[
            {'kind': 'plugin', 'id': 'example.plugin', 'digest': 'a' * 64, 'trusted': False, 'running': True,
             'mode': 'isolated', 'reason': 'OS file and network isolation enabled.'}])
    elif path.endswith('/settings') and '/sessions/' in path:
        subject = path.split('/')[-2]
        policy = policies.get(subject, policies['two'])
        definitions = [{'id': key, 'name': {'zh': zh, 'en': key}, 'description': {'zh': zh, 'en': key}}
                       for key, zh in [('read-only', '只读'), ('workspace-write', '工作区写入'), ('full-access', '完全访问'), ('custom', '自定义')]]
        data.update(model={'model': '', 'provider': '', 'source': 'global', 'global': {'model': '', 'provider': ''}, 'providers': []},
            permission={'mode': policy['filesystem_mode'], 'source': 'project', 'project': '/project/shared', 'policy': policy,
                        'mode_definitions': definitions, 'authorized_roots': ['/project/shared'], 'rules': {}})
    route.fulfill(status=200, content_type='application/json', body=json.dumps(data),
                  headers={'Access-Control-Allow-Origin': url, 'Access-Control-Allow-Credentials': 'true'})


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.add_init_script("localStorage.setItem('stella_lang', 'zh')")
    page.route('http://127.0.0.1:9876/**', backend)
    page.goto(url)
    page.wait_for_load_state('networkidle')
    page.evaluate("""async () => {
      const module = await import('/src/__test__/RunPolicyHarness.tsx'); module.mountRunPolicyTest()
    }""")
    one = page.get_by_test_id('policy-one')
    two = page.get_by_test_id('policy-two')
    expect(one.get_by_role('radiogroup', name='权限模式')).to_be_visible()
    assert one.get_by_role('radio').count() == 4
    expect(one.get_by_text('Agent 可用能力', exact=True)).to_have_count(0)
    one.get_by_role('radio', name='只读', exact=True).check()
    one.get_by_role('button', name='保存权限', exact=True).click()
    expect(one.get_by_role('status')).to_contain_text('已保存')
    assert saved[-1]['policy'] == {'filesystem_mode': 'read-only', 'permission_mode': 'read-only', 'policy_revision': 1}
    expect(two.get_by_role('radio', name='工作区写入', exact=True)).to_be_checked()
    one.get_by_text('保留的连接访问范围', exact=True).click()
    expect(one.get_by_label('允许访问此连接')).not_to_be_checked()
    one.get_by_label('允许访问此连接').check()
    one.get_by_role('button', name='保存连接访问范围', exact=True).click()
    expect(one.get_by_role('status').filter(has_text='连接访问范围已保存')).to_be_visible()
    one.get_by_text('保留的连接访问范围', exact=True).click()
    reject[0] = True
    one.get_by_role('radio', name='完全访问', exact=True).check()
    one.get_by_role('button', name='保存权限', exact=True).click()
    expect(one.get_by_role('alert')).to_contain_text('权限版本已变化')
    page.screenshot(path='/tmp/stella-run-policy-desktop.png', full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.screenshot(path='/tmp/stella-run-policy-narrow.png', full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.evaluate("""async () => {
      const module = await import('/src/__test__/PermissionSelectorHarness.tsx'); await module.mountPermissionSelector('one')
    }""")
    page.locator('#permission-test-root').evaluate("node => { node.style.zIndex = '100000' }")
    chip = page.locator('#permission-selector-chip')
    chip.get_by_role('button').first.click()
    expect(chip.get_by_role('button', name='自定义', exact=True)).to_be_visible()
    expect(chip.get_by_text('切换将同步此项目的其他对话。')).to_have_count(0)
    chip.get_by_role('button', name='设置对话权限', exact=True).click()
    expect(chip.get_by_role('radiogroup', name='权限模式')).to_be_visible()
    page.evaluate("""async () => {
      const module = await import('/src/__test__/SecurityAuthorizationHarness.tsx'); module.mountSecurityAuthorizationTest({module:'sessions', id:'one', agent:'default'})
    }""")
    page.get_by_label('授权主体').select_option('session:default:one')
    page.get_by_label('特定资源').select_option('folder:school')
    page.get_by_label('使用场景', exact=True).select_option('background')
    page.get_by_role('button', name='授予资源访问', exact=True).click()
    expect(page.get_by_text('有效', exact=True)).to_be_visible()
    assert saved[-1]['purposes'] == ['background'] and saved[-1]['operations'] == ['read']
    page.get_by_role('button', name='撤销授权', exact=True).click()
    expect(page.get_by_text('已撤销', exact=True)).to_be_visible()
    page.get_by_text('保留的连接授权', exact=True).click()
    page.get_by_label('恢复旧授权 legacy-course').check()
    page.get_by_label('旧授权使用场景 legacy-course').select_option('background')
    page.get_by_role('button', name='保存旧授权', exact=True).click()
    expect(page.get_by_role('status').filter(has_text='旧授权已保存')).to_be_visible()
    assert saved[-1]['purposes'] == ['background'] and saved[-1]['revoked'] is False
    expect(page.get_by_role('checkbox', name='受信任运行 example.plugin')).to_have_count(0)
    expect(page.get_by_role('button', name='保存运行选择', exact=True)).to_have_count(0)
    expect(page.get_by_text('example.plugin', exact=True)).to_be_visible()
    browser.close()
print('PASS: independent policies, four permission modes, revision error, desktop and narrow UI')

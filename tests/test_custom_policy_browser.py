"""Real React interactions with transport fixtures; backend policy is tested separately."""
import re
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

REPO = Path(__file__).resolve().parents[1]


def test_custom_rules_are_edited_only_in_security_and_saved_with_revision(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(port), '--strictPort'],
        cwd=REPO / 'desktop', stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(origin, timeout=.2).close()
                break
            except OSError:
                time.sleep(.05)
        else:
            raise AssertionError('Vite did not start')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1080, 'height': 900})
            page.set_default_timeout(5000)
            errors, saves = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("localStorage.setItem('stella_lang', 'zh')")
            policy = dict(filesystem_mode='full-access', permission_mode='custom', rules={'network': 'deny'}, policy_revision=7)
            def backend(route):
                if route.request.method == 'POST':
                    body = route.request.post_data_json
                    saves.append(body)
                    policy.update(body['policy'], policy_revision=policy['policy_revision'] + 1)
                route.fulfill(json=dict(status='success', policy=policy, migration_status='complete', compatibility_restrictions={}, capabilities=[]))
            page.route('**/api/security/run-policy*', backend)
            compiled = urllib.request.urlopen(origin + '/src/main.tsx').read().decode()
            react_url = re.search(r'from "([^"]*/deps/react\.js(?:\?[^"]*)?)"', compiled).group(1)
            dom_url = re.search(r'from "([^"]*/deps/react-dom_client\.js(?:\?[^"]*)?)"', compiled).group(1)
            router_source = urllib.request.urlopen(origin + '/src/App.tsx').read().decode()
            router_url = re.search(r'from "([^"]*/deps/react-router-dom\.js(?:\?[^"]*)?)"', router_source).group(1)
            html = """<!doctype html><html><body><div id="root"></div><script type="module">
import '/@vite/client';
import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window); window.$RefreshReg$=()=>{}; window.$RefreshSig$=()=>(type)=>type; window.__vite_plugin_react_preamble_installed__=true;
const React = (await import('REACT_URL')).default;
const ReactDOM = (await import('DOM_URL')).default;
const Editor = (await import('/src/components/RunPolicyEditor.tsx')).default;
const {MemoryRouter} = await import('ROUTER_URL');
await import('/src/index.css');
const detailed = new URLSearchParams(location.search).get('detailed') === '1';
ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter, null,
  React.createElement(Editor, {kind: detailed ? 'template' : 'task', subject: detailed ? 'default' : 'task-one', detailed})));
</script></body></html>""".replace('REACT_URL', react_url).replace('DOM_URL', dom_url).replace('ROUTER_URL', router_url)
            page.route('**/policy-test*', lambda route: route.fulfill(body=html, content_type='text/html'))
            page.goto(origin + '/policy-test?detailed=1')
            expect(page.get_by_role('radio', name='自定义', exact=True)).to_be_checked()
            expect(page.get_by_label('访问网络', exact=True)).to_have_value('deny')
            page.get_by_label('访问网络', exact=True).select_option('confirm')
            page.get_by_role('button', name='保存权限', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('权限已保存')
            assert saves[-1]['kind'] == 'template'
            assert saves[-1]['policy']['rules']['network'] == 'confirm'
            assert saves[-1]['policy']['permission_mode'] == 'custom'
            assert saves[-1]['policy']['policy_revision'] == 7
            page.goto(origin + '/policy-test')
            expect(page.get_by_role('radio', name='自定义', exact=True)).to_be_checked()
            expect(page.get_by_label('访问网络', exact=True)).to_have_count(0)
            expect(page.get_by_role('link', name='在安全中心编辑自定义规则')).to_have_attribute('href', re.compile('kind=task.*subject=task-one'))
            page.get_by_role('radio', name='只读', exact=True).check()
            page.get_by_role('button', name='保存权限', exact=True).click()
            expect(page.get_by_role('status')).to_contain_text('权限已保存')
            assert saves[-1]['policy']['permission_mode'] == 'read-only'
            assert 'rules' not in saves[-1]['policy']
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        process.communicate(timeout=10)

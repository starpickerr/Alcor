"""Verify the packed bundle on the unpatched official DSH distribution."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tarfile
import threading
import time
from http.server import ThreadingHTTPServer

from playwright.sync_api import expect, sync_playwright
from web_check import Provider, requests, rpc, settings, stop, open_secretary, wait_secretary, start_secretary_chat
from apps_check import check_apps

ROOT = Path(__file__).resolve().parents[2]
CLI = Path(os.environ.get('STELLA_CHECK_CLI', str(ROOT / '.local/official-cli/node_modules/@deepseek-ai/dsh/lib/bin.js')))
CLIENT_COMPAT = os.environ.get('STELLA_CHECK_CLIENT_COMPAT') == '1'
VERSION = json.loads((ROOT / 'packages/alcor-secretary/package.json').read_text())['version']
PACK = ROOT / f'.local/artifacts/alcor-secretary-{VERSION}.tgz'


class NativeProvider(Provider):
    shared_tools = frozenset({'working_directory'})

    def do_POST(self):
        size = int(self.headers['Content-Length'])
        raw = self.rfile.read(size)
        payload = json.loads(raw)
        structured = next((tool['function'] for tool in payload.get('tools', []) if tool['function']['name'] == 'structured_output'), None)
        if structured is None:
            import io
            self.rfile = io.BytesIO(raw)
            return super().do_POST()
        requests.append(payload)
        assert self.headers.get('Authorization') == 'Bearer synthetic-test-key'
        def content_text(content):
            return content if isinstance(content, str) else '\n'.join(block.get('text', '') for block in content or [])
        prompt = next(content_text(message['content']) for message in payload['messages'] if '可阅读的原文数据：' in content_text(message.get('content')))
        entries = json.loads(prompt.split('可阅读的原文数据：\n')[1])
        if 'decisions' in structured['parameters']['properties']:
            value = {'decisions': [{'id': item['id'], 'selected': True, 'reason': 'Synthetic validation', 'section': 'action', 'score': 80, 'summary': item['title']} for item in entries]}
        else:
            expected = json.loads(prompt[prompt.index('{"date":'):prompt.index('\n\n可阅读的原文数据：')])['items']
            ids = [item['id'] for item in expected]
            value = {'body': 'Synthetic structured briefing saved through DSH.', 'source_item_ids': ids, 'reports': [{'id': item, 'content': '## Synthetic source report\n\n- **Important** preview item\n\n[Reference](https://example.com/reference)\n\n```python\nprint(1)\n```\n\n<img src=x onerror="window.__briefingUnsafe=true">'} for item in ids]}
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        delta = {'tool_calls': [{'index': 0, 'id': 'structured-call', 'type': 'function', 'function': {'name': 'structured_output', 'arguments': json.dumps(value)}}]}
        for choice in [{'delta': {'role': 'assistant'}, 'finish_reason': None}, {'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': 'tool_calls'}]:
            data = {'id': 'structured-synthetic', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'stella-mock', 'choices': [{'index': 0, **choice}]}
            self.wfile.write(('data: ' + json.dumps(data) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def launch(environment, log, port=0, profile='compatibility'):
    directory = Path(environment['DSH_HOME']) / 'profiles' / profile
    manifest = json.loads((directory / 'package.json').read_text())
    if '@alcor/secretary' in manifest.get('dependencies', {}):
        module = CLI.parents[2] / 'dsh-app-boot/lib/index.js'
        script = f'''import {{resolveBundleDir}} from {json.dumps(module.as_uri())};
          console.log(resolveBundleDir('dsh', '@alcor/secretary', {json.dumps(str(CLI.parents[1] / 'package.json'))}, {json.dumps(str(directory))}));'''
        resolved = Path(subprocess.check_output(['node', '--input-type=module', '-e', script], cwd=directory, env=environment, text=True).strip()).resolve()
        assert resolved.is_relative_to(directory.resolve()), 'DSH resolved Stella outside the test profile; use a standalone official CLI outside any Stella workspace'
    process = subprocess.Popen(['node', str(CLI), '--profile', profile, '--no-open', '--port', str(port)],
        cwd=environment['DSH_HOME'], env=environment, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if match := re.search(r'dsh web: (http://\S+)', Path(log.name).read_text()):
            return process, match.group(1)
        if process.poll() is not None:
            raise AssertionError('Official DSH failed to start')
        time.sleep(0.1)
    stop(process)
    print(re.sub(r'token=\S+', 'token=[redacted]', Path(log.name).read_text())[-12000:])
    raise AssertionError('Official DSH startup timed out')


def configuration(value):
    return json.loads(subprocess.check_output(['node', '-e',
        'process.stdout.write(JSON.stringify(require("yaml").parse(require("node:fs").readFileSync(0,"utf8"))))'],
        input=value, text=True, cwd=ROOT))


def main():
    assert CLI.exists() and PACK.exists(), 'Install the pinned official CLI and pack the bundle first; see product/README.md'
    stock = CLI.parents[2] / 'dsh-client-ui-layout/lib/client.js'
    assert ('shell.top' in stock.read_text()) == CLIENT_COMPAT, 'Client patch must match the explicitly selected check scope'
    server = ThreadingHTTPServer(('127.0.0.1', 0), NativeProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-official-check-') as temporary, sync_playwright() as playwright:
            home = Path(temporary)
            environment = {key: value for key, value in os.environ.items() if key in ['PATH', 'HOME', 'USER', 'LOGNAME', 'TMPDIR']}
            environment.update(HOME=str(home), DSH_HOME=str(home), DSH_TELEMETRY_DISABLED='1', PATH=str(ROOT / 'node_modules/.bin') + os.pathsep + environment['PATH'])
            profile = home / 'profiles/compatibility'
            profile.mkdir(parents=True)
            (home / 'documents').mkdir()
            (profile / 'package.json').write_text(json.dumps({'name': 'stella-stock-fixture', 'private': True,
                'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}) + '\n')
            owner_config = f'''- id: workspace-controller
  config:
    documentsDirectory: {home / 'documents'}
- id: agent-default-model
  config:
    provider: stella-test
    model: stella-mock
- id: llm-pi-ai
  config:
    providers:
      stella-test:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: stella-mock
            name: Synthetic model
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
- id: agent-preset-registry
  config:
    default: owner-default
- id: sandbox-policy
  config:
    mode: read-only
- id: session-telemetry-otel
  disabled: true
- insert:
    - id: owner-preset
      name: '@deepseek-ai/dsh-agent-preset'
      config:
        id: owner-default
        name: Owner workspace
        plugins:
          - id: persona
            name: '@deepseek-ai/dsh-persona'
            config:
              prefix: Owner workspace configuration
              includeRuntimeContext: false
          - id: todo
            name: '@deepseek-ai/dsh-tool-todo'
            config:
              allowParallelInProgress: true
          - id: ask
            name: '@deepseek-ai/dsh-tool-ask-user'
'''
            (profile / 'cordis.patch.yml').write_text(owner_config)
            (home / '.credentials.yaml').write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n')
            os.chmod(home / '.credentials.yaml', 0o600)
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1280, 'height': 850})
            with (home / 'before-install.log').open('w') as log:
                process, url = launch(environment, log)
                try:
                    page = context.new_page()
                    page.goto(url)
                    page.get_by_role('button', name='Account menu', exact=True).wait_for()
                    page.get_by_role('button', name='Continue', exact=True).click()
                    assert not any(preset.get('broken') for preset in rpc(page, url, 'agentPresets/list')['presets']), 'Fixture presets must be active'
                    page.get_by_role('button', name='New session', exact=True).last.click()
                    page.get_by_role('button', name='Choose workspace', exact=True).click()
                    page.get_by_role('menuitem', name=re.compile('Default workspace')).click()
                    composer = page.locator('[data-composer-input][contenteditable="true"]')
                    try:
                        composer.fill('Owner conversation before installing Stella')
                    except Exception:
                        print('Preset catalog:', rpc(page, url, 'agentPresets/list'))
                        print('Page:', page.locator('body').inner_text()[-6000:])
                        print('Host:', re.sub(r'token=\S+', 'token=[redacted]', Path(log.name).read_text())[-6000:])
                        if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                            Path(directory).mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(Path(directory) / 'before-install-failure.png'))
                        raise
                    composer.press('Enter')
                    page.get_by_text('DSH synthetic conversation passed.', exact=True).wait_for(timeout=25000)
                    composer.fill('Owner unsent draft')
                    assert next(preset['id'] for preset in rpc(page, url, 'agentPresets/list')['presets'] if preset['isDefault']) == 'owner-default'
                    assert page.get_by_role('button', name='事务', exact=True).count() == 0
                    port = int(re.search(r':(\d+)/', url).group(1))
                    page.close()
                finally:
                    stop(process)
            baseline_config = configuration((profile / 'cordis.patch.yml').read_text())
            assert all(entry in baseline_config for entry in configuration(owner_config)), baseline_config
            with (home / 'install.log').open('w') as log:
                subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'add', str(PACK), '--ignore-scripts'], cwd=home, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
            installed_config = configuration((profile / 'cordis.patch.yml').read_text())
            assert installed_config == baseline_config, installed_config
            with (home / 'web.log').open('w') as log:
                process, url = launch(environment, log, port)
                try:
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(url)
                    wait_secretary(page)
                    expect(page.get_by_role('button', name='事务', exact=True)).to_have_count(0)
                    if page.get_by_role('button', name='Continue', exact=True).count():
                        page.get_by_role('button', name='Continue', exact=True).click()
                    assert page.get_by_role('button', name=re.compile('切换使用模式')).count() == 1
                    page.get_by_text('DSH synthetic conversation passed.', exact=True).wait_for()
                    composer = page.locator('[data-composer-input][contenteditable="true"]')
                    expect(composer).to_have_text('Owner unsent draft')
                    assert next(preset['id'] for preset in rpc(page, url, 'agentPresets/list')['presets'] if preset['isDefault']) == 'owner-default'
                    requests_before = len(requests)
                    page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
                    page.get_by_role('menuitem', name='Alcor', exact=True).click()
                    page.get_by_role('heading', name='事务', exact=True).wait_for()
                    limitation = page.get_by_text('当前版本可使用秘书页面与对话，顶部模式切换尚待 DSH 支持。原有对话与设置保持不变。', exact=True)
                    expect(limitation).to_have_count(0)
                    assert len(requests) == requests_before
                    expect(page.locator('.alcor-sidebar')).to_be_visible()
                    expect(page.get_by_role('button', name='今天', exact=True)).to_have_count(0)
                    expect(page.get_by_role('button', name='返回 DSH 工作台', exact=True)).to_have_count(0)
                    expect(page.locator('.alcor-sidebar-chat')).to_have_text('新对话')
                    expect(page.locator('.alcor-sidebar-workspaces')).to_be_visible()
                    expect(page.locator('.alcor-sidebar-workspaces').get_by_role('button', name='View options', exact=True)).to_be_visible()
                    assert page.locator('.alcor-sidebar nav').bounding_box()['y'] < page.locator('.alcor-sidebar-workspaces').bounding_box()['y']
                    expect(page.locator('.alcor-nav-group > p')).to_have_text(['应用', '系统'])
                    expect(page.get_by_role('group',name='系统',exact=True).get_by_role('button')).to_have_text(['通道','人设','插件'])
                    expect(page.locator('.alcor-sidebar nav button[aria-current="page"]')).to_have_accessible_name('事务')
                    expect(page.locator('.alcor-sidebar nav button > svg')).to_have_count(8)
                    expect(page.get_by_role('button', name='Account menu', exact=True)).to_be_in_viewport()
                    page.locator('[data-sidebar-toggle]:visible').click()
                    expect(page.locator('.alcor-sidebar')).to_have_attribute('data-collapsed', 'true')
                    expect(page.get_by_role('button', name='设置', exact=True)).to_have_count(0)
                    expect(page.get_by_role('button', name='Account menu', exact=True)).to_be_in_viewport()
                    rail = page.locator('.alcor-sidebar').bounding_box()
                    assert rail['width'] == 56
                    for button in page.locator('.alcor-sidebar > .alcor-sidebar-brand button, .alcor-sidebar-chat, .alcor-sidebar nav button, .alcor-sidebar-workspaces button, .alcor-sidebar footer button').all():
                        if not button.is_visible(): continue
                        rect = button.bounding_box()
                        assert abs(rect['x'] + rect['width'] / 2 - (rail['x'] + rail['width'] / 2)) < 2, rect
                        assert rect['x'] >= rail['x'] and rect['x'] + rect['width'] <= rail['x'] + rail['width'], rect
                    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                        Path(directory).mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(Path(directory) / 'stella-native-rail.png'), animations='disabled')
                    settings(page)
                    expect(page.get_by_text('使用 DSH 访问模式', exact=True)).to_be_visible()
                    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                        page.screenshot(path=str(Path(directory) / 'stella-native-settings.png'), animations='disabled')
                    page.get_by_role('button', name='Close', exact=True).click()
                    page.get_by_role('button', name='账单', exact=True).click()
                    page.get_by_role('heading', name='账单', exact=True).wait_for()
                    page.locator('[data-sidebar-toggle]:visible').click()
                    expect(page.locator('.alcor-sidebar')).not_to_have_attribute('data-collapsed', 'true')
                    selected_before_plugins = page.evaluate('localStorage.getItem("dsh.sessions.current")')
                    page.get_by_role('button', name='插件', exact=True).click()
                    page.get_by_role('heading', name='Plugins', exact=True).wait_for()
                    expect(page.locator('.alcor-sidebar nav button[aria-current="page"]')).to_have_accessible_name('插件')
                    expect(page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True)).to_be_visible()
                    expect(page.get_by_role('button', name='Refresh', exact=True)).to_be_enabled()
                    expect(page.locator('[data-plugin-body]')).to_be_visible()
                    assert page.evaluate('localStorage.getItem("dsh.sessions.current")') == selected_before_plugins
                    page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True).click()
                    page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                    page.get_by_role('button', name='Plugins', exact=True).click()
                    page.get_by_role('heading', name='Plugins', exact=True).wait_for()
                    owner_session_id = json.loads(selected_before_plugins)['sessionId']
                    page.locator(f'[data-row-key="session:{owner_session_id}"]').click()
                    expect(page.locator('[data-composer-input][contenteditable="true"]')).to_have_text('Owner unsent draft')
                    page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
                    page.get_by_role('menuitem', name='Alcor', exact=True).click()
                    page.get_by_role('heading', name='Plugins', exact=True).wait_for()
                    expect(page.locator('.alcor-sidebar nav button[aria-current="page"]')).to_have_accessible_name('插件')
                    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                        Path(directory).mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(Path(directory) / 'stella-native-plugins-desktop.png'), animations='disabled')
                    open_secretary(page)
                    expect(page.get_by_text('跟进', exact=True)).to_have_count(0)
                    expect(page.get_by_role('button', name='秘书对话', exact=True)).to_have_count(0)
                    expect(page.get_by_role('button', name='待你确认', exact=True)).to_have_count(0)
                    settings(page)
                    expect(page.get_by_text('Alcor 对话工具', exact=True)).to_have_count(0)
                    expect(page.get_by_text('Stella 对话工具', exact=True)).to_have_count(0)
                    expect(page.get_by_text('使用 DSH 访问模式', exact=True)).to_be_visible()
                    expect(page.get_by_label('学工号', exact=True)).to_have_count(0)
                    assert rpc(page, url, 'alcorSecretary/checkElearning', {'request': {}}) == {'status': 'disconnected'}
                    page.get_by_role('dialog', name='Settings', exact=True).get_by_role('button', name='Close', exact=True).click()
                    open_secretary(page)
                    installed_at = rpc(page, url, 'alcorSecretary/overview')['installedAt']
                    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                        Path(directory).mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(Path(directory) / 'native-secretary-desktop.png'))
                        page.set_viewport_size({'width': 390, 'height': 844})
                        expect(page.locator('[data-sidebar-opener]')).to_be_visible()
                        expect(page.locator('.alcor-bear')).to_have_count(0)
                        page.screenshot(path=str(Path(directory) / 'native-secretary-mobile.png'), animations='disabled')
                        expect(page.get_by_role('heading', name='事务', exact=True)).to_be_in_viewport(ratio=1)
                        assert page.locator('body').evaluate('element => element.scrollWidth <= element.clientWidth')
                        page.locator('[data-sidebar-opener]').click()
                        expect(page.get_by_role('dialog', name='Alcor 秘书导航', exact=True)).to_be_visible()
                        expect(page.get_by_role('button', name='Account menu', exact=True)).to_be_in_viewport()
                        page.screenshot(path=str(Path(directory) / 'native-secretary-drawer.png'), animations='disabled')
                        page.get_by_role('button', name='插件', exact=True).click()
                        page.get_by_role('heading', name='Plugins', exact=True).wait_for()
                        expect(page.locator('[data-sidebar-drawer]')).to_have_attribute('data-sidebar-collapsed', 'true')
                        page.screenshot(path=str(Path(directory) / 'stella-native-plugins-mobile.png'), animations='disabled')
                        page.locator('[data-sidebar-opener]').click()
                        settings(page)
                        page.get_by_role('dialog', name='Settings', exact=True).wait_for()
                        page.keyboard.press('Escape')
                        expect(page.get_by_role('dialog', name='Settings', exact=True)).to_have_count(0)
                        expect(page.get_by_role('button', name='Account menu', exact=True)).to_be_focused()
                        page.keyboard.press('Escape')
                        expect(page.locator('[data-sidebar-opener]')).to_be_focused()
                        page.locator('[data-sidebar-opener]').click()
                        settings(page)
                        expect(page.get_by_role('heading', name='当前空间的项目', exact=True)).to_be_visible()
                        page.get_by_role('dialog').get_by_role('button',name='Close',exact=True).click()
                        if page.locator('[data-sidebar-opener]').is_visible():
                            page.locator('[data-sidebar-opener]').click()
                        page.locator('.alcor-sidebar nav').get_by_role('button',name='插件',exact=True).click()
                        expect(page.get_by_role('heading', name='Plugins', exact=True)).to_be_in_viewport()
                        expect(page.locator('[data-sidebar-drawer]')).to_have_attribute('data-sidebar-collapsed', 'true')
                        page.locator('[data-sidebar-opener]').click()
                        open_secretary(page)
                        page.set_viewport_size({'width': 1280, 'height': 850})
                    apps = check_apps(page, url, True)
                    assert (profile / 'alcor/business/tasks.sqlite3').is_file(), 'Business data must belong to the selected profile'
                    assert not (home / 'alcor/business/tasks.sqlite3').exists(), 'Business data must not fall back to a global owner'
                    page.close()
                    stop(process)
                    with (home / 'apps-restart.log').open('w') as restart_log:
                        process, url = launch(environment, restart_log, port)
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(url)
                    wait_secretary(page)
                    persisted = rpc(page, url, 'alcorOrganizer/read', {'request': {}})
                    assert any(record['id'] == apps['recordId'] for record in persisted['records'])
                    assert any(item['id'] == apps['scheduleId'] and item['status'] == 'active' for item in persisted['schedules']), persisted['schedules']
                    open_secretary(page)
                    page.locator('.alcor-sidebar nav').get_by_role('button',name='人设',exact=True).click()
                    page.get_by_label('助手名称', exact=True).fill('Packed secretary')
                    page.get_by_role('button', name='保存人设', exact=True).click()
                    page.get_by_text('已保存', exact=True).wait_for()
                    assert rpc(page, url, 'alcorSecretary/overview')['displayName'] == 'Packed secretary', (
                        [entry for entry in rpc(page, url, 'settings/describe')['namespaces'] if entry['ns'] == 'alcor-secretary'],
                        (profile / 'cordis.patch.yml').read_text()[-800:],
                        re.sub(r'token=\S+', 'token=[redacted]', Path(log.name).read_text())[-2500:])
                    start_secretary_chat(page)
                    composer = page.locator('[data-composer-input][contenteditable="true"]')
                    composer.fill('Call alcor_overview')
                    composer.press('Enter')
                    page.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=25000)
                    composer.fill('Secretary unsent draft')
                    page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True).click()
                    page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                    expect(composer).to_have_text('Owner unsent draft')
                    page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
                    page.get_by_role('menuitem', name='Alcor', exact=True).click()
                    page.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for()
                    expect(composer).to_have_text('Secretary unsent draft')
                    page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True).click()
                    page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                    expect(page.locator('.alcor-sidebar')).to_have_count(0)
                    expect(page.get_by_role('button', name='事务', exact=True)).to_have_count(0)
                    expect(composer).to_have_text('Owner unsent draft')
                    previous = page.evaluate('localStorage.getItem("dsh.sessions.current")')
                    page.get_by_role('button', name='New session', exact=True).last.click()
                    page.wait_for_function('previous => localStorage.getItem("dsh.sessions.current") !== previous', arg=previous)
                    expect(page.get_by_text('Stella synthetic tool round trip passed.', exact=True)).to_have_count(0)
                    composer.fill('Owner configuration after using Stella')
                    composer.press('Enter')
                    page.get_by_text('DSH synthetic conversation passed.', exact=True).wait_for(timeout=25000)
                    owner_requests = [request for request in requests if any(message.get('content') == 'Owner configuration after using Stella' for message in request.get('messages', []))]
                    assert owner_requests and 'Owner workspace configuration' in json.dumps(owner_requests, ensure_ascii=False)
                    assert '你是 Stella' not in json.dumps(owner_requests, ensure_ascii=False)
                    assert '你是 Alcor' not in json.dumps(owner_requests, ensure_ascii=False)
                    assert 'WORKSPACE_USER_SENTINEL' not in json.dumps(owner_requests)
                    owner_automation = rpc(page, url, 'schedule/create', {'request': {'title': 'Unrelated owner task', 'prompt': 'Owner task', 'timing': {'kind': 'every', 'every_seconds': 86400}, 'target': {'kind': 'new_once'}}})
                    result = rpc(page, url, 'pluginManager/setBundleEnabled', {'name': '@alcor/secretary', 'enabled': False})
                    native_tasks = rpc(page, url, 'schedule/catalog')
                    assert next(item for item in native_tasks if item['id'] == apps['scheduleId'])['status'] == 'paused'
                    assert next(item for item in native_tasks if item['id'] == owner_automation['id'])['status'] == 'active'
                    assert result['application'] == 'applied', result
                    expect(page.get_by_role('button', name='事务', exact=True)).to_have_count(0)
                    assert not any(preset['id'] == 'stella' for preset in rpc(page, url, 'agentPresets/list')['presets'])
                    unavailable = page.request.post(url.split('/?')[0] + '/api/alcorSecretary/overview', data={})
                    assert unavailable.status == 404 or not unavailable.json().get('result', {}).get('ok', False)
                    assert not errors, errors
                    page.close()
                finally:
                    stop(process)
            with (home / 'restart.log').open('w') as log:
                process, url = launch(environment, log, port)
                try:
                    page = context.new_page()
                    page.goto(url)
                    page.get_by_role('button', name='Account menu', exact=True).wait_for()
                    assert page.get_by_role('button', name='事务', exact=True).count() == 0
                    result = rpc(page, url, 'pluginManager/setBundleEnabled', {'name': '@alcor/secretary', 'enabled': True})
                    assert result['application'] == 'applied', result
                    # Official client modules may require a reload after activation.
                    page.reload()
                    wait_secretary(page)
                    value = rpc(page, url, 'alcorSecretary/overview')
                    assert value['displayName'] == 'Packed secretary' and value['installedAt'] == installed_at, value
                    assert next(item for item in rpc(page, url, 'schedule/catalog') if item['id'] == owner_automation['id'])['status'] == 'active'
                    business = rpc(page, url, 'alcorOrganizer/read', {'request': {}})
                    assert any(record['id'] == apps['recordId'] for record in business['records'])
                    assert any(item['id'] == apps['scheduleId'] and item['status'] == 'paused' for item in business['schedules']), business['schedules']
                    result = rpc(page, url, 'pluginManager/removeBundle', {'name': '@alcor/secretary'})
                    assert result['application'] == 'applied', result
                    expect(page.get_by_role('button', name='事务', exact=True)).to_have_count(0)
                    manifest = json.loads((profile / 'package.json').read_text())
                    for name in ('@alcor/secretary', '@stella/secretary'):
                        assert name not in manifest.get('dependencies', {})
                        assert name not in manifest['dsh']['profile']['bundles']
                    assert next(preset['id'] for preset in rpc(page, url, 'agentPresets/list')['presets'] if preset['isDefault']) == 'owner-default'
                    previous = page.evaluate('localStorage.getItem("dsh.sessions.current")')
                    page.get_by_role('button', name='New session', exact=True).last.click()
                    page.wait_for_function('previous => localStorage.getItem("dsh.sessions.current") !== previous', arg=previous)
                    composer = page.locator('[data-composer-input][contenteditable="true"]')
                    composer.fill('DSH remains usable after uninstall')
                    composer.press('Enter')
                    page.get_by_text('DSH synthetic conversation passed.', exact=True).wait_for(timeout=25000)
                    page.close()
                finally:
                    stop(process)
            with (home / 'reinstall.log').open('w') as log:
                subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'add', str(PACK), '--ignore-scripts'], cwd=home, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
            with (home / 'reinstalled-web.log').open('w') as log:
                process, url = launch(environment, log, port)
                try:
                    page = context.new_page()
                    page.goto(url)
                    wait_secretary(page)
                    value = rpc(page, url, 'alcorSecretary/overview')
                    assert value['displayName'] == 'Packed secretary' and value['installedAt'] == installed_at, value
                    assert any(record['id'] == apps['recordId'] for record in rpc(page, url, 'alcorOrganizer/read', {'request': {}})['records'])
                    assert next(preset['id'] for preset in rpc(page, url, 'agentPresets/list')['presets'] if preset['isDefault']) == 'owner-default'
                    page.close()
                finally:
                    stop(process)
            browser.close()
        scope = 'DSH with the local client compatibility patch' if CLIENT_COMPAT else 'stock DSH'
        print('PASS: native package on ' + scope + '; existing conversation/draft/default preset/model preserved, explicit secretary tool round trip, disable/restart/enable/uninstall/reinstall, retained data, DSH usable after removal; synthetic provider only')
    finally:
        server.shutdown()


def pack_briefing_fixture(home):
    plugin = home / 'synthetic-plugin'
    plugin.mkdir()
    (plugin / 'package.json').write_text(json.dumps({'name': '@stella-test/briefing-source', 'version': '0.0.1', 'type': 'module', 'exports': './index.mjs', 'dsh': {'bundle': {'patch': './cordis.patch.yml'}}, 'engines': {'dsh': '0.2.2-alpha.3 || 0.2.2-rc.1'}}))
    (plugin / 'index.mjs').write_text((ROOT / 'product/tests/fixtures/briefing-plugin.mjs').read_text())
    (plugin / 'cordis.patch.yml').write_text('- insert:\n    - id: synthetic-briefing-source\n      name: "@stella-test/briefing-source"\n')
    plugin_pack = home / 'synthetic-plugin.tgz'
    with tarfile.open(plugin_pack, 'w:gz') as archive:
        archive.add(plugin, arcname='package')
    return plugin_pack


if __name__ == '__main__':
    main()

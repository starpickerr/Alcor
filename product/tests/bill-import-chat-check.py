"""Model -> dedicated import tool -> ledger page; isolated files/provider only."""
import json
import os
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
import official_desktop_check as desktop
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import expect, sync_playwright
from plugin_check import ROOT, CLI, PACK, launch
from web_check import rpc, stop, open_secretary


class Provider(BaseHTTPRequestHandler):
    paths = []
    calls = 0
    completed = 0

    def log_message(self, *_args): pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        names = {tool['function']['name'] for tool in payload.get('tools', [])}
        delta, finish = {'content': 'Synthetic title'}, 'stop'
        if 'stella_overview' in names:
            assert 'stella_import_bills' in names, names
            if payload['messages'][-1]['role'] == 'tool':
                result = json.loads(payload['messages'][-1]['content'])
                assert result['imported'] == (3 if self.calls == 1 else 0), result
                type(self).completed = self.calls
                delta = {'content': '账单导入完成。'}
            else:
                type(self).calls += 1
                delta = {'tool_calls': [{'index': 0, 'id': 'import-bills', 'type': 'function', 'function': {'name': 'stella_import_bills', 'arguments': json.dumps({'paths': self.paths})}}]}
                finish = 'tool_calls'
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        for choice in [{'delta': {'role': 'assistant'}, 'finish_reason': None}, {'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': finish}]:
            event = {'id': 'synthetic-bills', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'synthetic', 'choices': [{'index': 0, **choice}]}
            self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()


@contextmanager
def surface(playwright, home, env, artifacts, native):
    if native:
        with desktop.desktop(playwright, home, 'bill-import') as (page, errors):
            yield page, lambda endpoint, args: desktop.rpc(page, endpoint, args), errors
        return
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_locator_handler(page.get_by_role('button', name='Continue', exact=True), lambda button: button.click())
    page.add_locator_handler(page.get_by_role('dialog', name='Get started', exact=True), lambda dialog: dialog.get_by_role('button', name='Close', exact=True).click())
    with (artifacts / 'host.log').open('w') as log:
        process, url = launch(env, log)
        try:
            page.goto(url); page.get_by_role('button', name='Account menu', exact=True).wait_for()
            yield page, lambda endpoint, args: rpc(page, url, endpoint, args), errors
        finally:
            page.screenshot(path=str(artifacts / 'final.png'))
            (artifacts / 'final.txt').write_text(page.locator('body').inner_text())
            stop(process)
    browser.close()


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-bill-chat'))
    artifacts.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-bill-chat-') as temporary, sync_playwright() as playwright:
            home = Path(temporary); docs = home / 'documents'; docs.mkdir()
            files = json.loads(subprocess.check_output(['python3', str(ROOT / 'product/tests/bill-import-fixtures.py')], text=True))
            import base64
            for file in files: (docs / file['name']).write_bytes(base64.b64decode(file['content']))
            Provider.paths = [str(docs / file['name']) for file in files]
            native = os.environ.get('STELLA_CHECK_NATIVE_DESKTOP') == '1'
            env = {key: value for key, value in os.environ.items() if key in ('PATH', 'USER', 'LOGNAME', 'TMPDIR')}
            env.update(HOME=str(home), DSH_HOME=str(home), DSH_TELEMETRY_DISABLED='1')
            if native:
                desktop.verify_release(); desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
                env = desktop.environment(home)
            profile = Path(env['DSH_HOME']) / ('profiles/desktop' if native else 'profiles/compatibility'); profile.mkdir(parents=True)
            (profile / 'package.json').write_text(json.dumps({'name': 'bill-chat', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
            (profile / 'cordis.patch.yml').write_text(f'''- id: workspace-controller
  config:
    documentsDirectory: {docs}
- id: agent-default-model
  config:
    provider: synthetic
    model: synthetic
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: synthetic
            name: Synthetic
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
''')
            credentials = Path(env['DSH_HOME']) / '.credentials.yaml'
            credentials.write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n'); credentials.chmod(0o600)
            if native:
                desktop.native_cli(home, 'bill-install', 'add', str(PACK), '--ignore-scripts')
            else:
                with (artifacts / 'install.log').open('w') as log:
                    subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'add', str(PACK), '--ignore-scripts'], cwd=home, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
            with surface(playwright, home, env, artifacts, native) as (page, query, errors):
                open_secretary(page)
                for expected in (1, 2):
                    if expected == 1:
                        previous_sessions = set(page.locator('[data-row-key^="session:"]').evaluate_all('rows => rows.map(row => row.dataset.rowKey)'))
                        page.locator('.stella-sidebar-chat').click()
                    composer = page.locator('[data-composer-input][contenteditable="true"]')
                    composer.fill('请导入账单：' + '，'.join(Provider.paths)); composer.press('Enter')
                    if expected == 1:
                        deadline = time.monotonic() + 10
                        new_sessions = []
                        while not new_sessions and time.monotonic() < deadline:
                            new_sessions = [key for key in page.locator('[data-row-key^="session:"]').evaluate_all('rows => rows.map(row => row.dataset.rowKey)') if key not in previous_sessions]
                            page.wait_for_timeout(100)
                        assert new_sessions, 'Native DSH did not create the requested conversation'
                        page.locator('[data-row-key=' + json.dumps(new_sessions[0]) + ']').click()
                    deadline = time.monotonic() + 30
                    while Provider.completed < expected and time.monotonic() < deadline: page.wait_for_timeout(100)
                    assert Provider.completed == expected, f'Model result missing: turn={expected}, calls={Provider.calls}, completed={Provider.completed}'
                    expect(page.get_by_text('账单导入完成。', exact=True)).to_have_count(expected, timeout=10000)
                    assert Provider.calls == expected
                    rows = query('stellaOrganizer/business', {'request': {'domain': 'bills', 'method': 'list_ledger', 'arguments': {'year': 2026, 'month': 11}}})
                    assert len(rows) == 3
                page.get_by_role('button', name='账单', exact=True).click()
                page.get_by_label('账单年份', exact=True).select_option('2026')
                page.get_by_label('账单月份', exact=True).select_option('11')
                expect(page.locator('.stella-ledger-list > li')).to_have_count(3)
                expect(page.locator('.stella-bills-content')).to_contain_text('车票,返程')
                page.screenshot(path=str(artifacts / 'imported-ledger.png'))
                assert not errors, errors
        print('PASS: model calls bill tool, reads both files via DSH, imports directly without page confirmation, retries without duplicates, and ledger page shows all records')
    finally: server.shutdown()


if __name__ == '__main__': main()

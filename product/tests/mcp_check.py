"""Full DSH model/tool round trip and DSH-owned tool policy, synthetic data only."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright, expect
from web_check import ROOT, start, stop, rpc, open_page, settings, open_secretary


class McpProvider(BaseHTTPRequestHandler):
    observed = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.headers.get('Authorization') == 'Bearer synthetic-test-key'
        names = {tool['function']['name'] for tool in payload.get('tools', [])}
        delta, reason = {'content': 'Synthetic title'}, 'stop'
        if 'stella_overview' in names:
            self.observed.append(payload)
            assert {'stella_overview', 'stella_business', 'stella_generate_briefing', 'stella_review_tasks', 'stella_import_bills', 'stella_import_dates', 'stella_export_bills', 'mcp__elearning__list_courses'} <= names, names
            completed = any(message['role'] == 'tool' for message in payload['messages'])
            if completed:
                assert 'synthetic-course' in json.dumps(payload)
                delta = {'content': 'Stella MCP round trip passed.'}
            else:
                delta = {'tool_calls': [{'index': 0, 'id': 'call_courses', 'type': 'function', 'function': {'name': 'mcp__elearning__list_courses', 'arguments': '{}'}}]}
                reason = 'tool_calls'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for choice in [{'delta': {'role': 'assistant'}, 'finish_reason': None}, {'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': reason}]:
            value = {'id': 'synthetic-mcp', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'stella-mock', 'choices': [{'index': 0, **choice}]}
            self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), McpProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-mcp-check-') as temporary, sync_playwright() as playwright:
            home = Path(temporary)
            (home / '.stella-product.json').write_text('{"version":1,"kind":"web"}\n')
            profile = home / 'profiles/stella'
            profile.mkdir(parents=True)
            patch = profile / 'cordis.patch.yml'
            patch.write_text(f'''- id: workspace-controller
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
- insert:
    - id: synthetic-elearning
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        transport: stdio
        serverName: elearning
        command: {json.dumps(shutil.which('node'))}
        args: [{json.dumps(str(ROOT / 'product/tests/fixtures/mcp-server.mjs'))}]
        failOnStartupError: true
''')
            (home / 'documents').mkdir()
            credentials = home / '.credentials.yaml'
            credentials.write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n')
            credentials.chmod(0o600)
            legacy_grants = home / 'storages/stella_mcp.json'
            legacy_grants.parent.mkdir()
            legacy_grants.write_text(json.dumps({'unit': {'name':'stella_mcp','version':1},'global':None,'tables':{'grants':{'current':{'revision':3,'allowedTools':[]}}}}))
            legacy_before = legacy_grants.read_bytes()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1280, 'height': 850})
            with (home / 'web.log').open('w') as log:
                process, url = start(home, log)
                try:
                    page, errors = open_page(context, url)
                    assert rpc(page, url, 'stellaSecretary/checkElearning', {'request': {}}) == {'status': 'ready', 'courseCount': 1}
                    open_secretary(page)
                    settings(page)
                    dialog = page.get_by_role('dialog', name='Settings', exact=True)
                    expect(dialog.get_by_text('Stella 对话工具', exact=True)).to_have_count(0)
                    expect(dialog.get_by_text('权限与安全', exact=True)).to_have_count(0)
                    dialog.get_by_role('button', name='Close', exact=True).click()
                    page.locator('.stella-sidebar-chat').click()
                    composer = page.locator('[data-composer-input][contenteditable="true"]')
                    composer.fill('请使用 eLearning 查询我可以访问的课程。')
                    composer.press('Enter')
                    page.get_by_text('Stella MCP round trip passed.', exact=True).wait_for(timeout=25000)
                    assert len(McpProvider.observed) >= 2
                    assert legacy_grants.read_bytes() == legacy_before
                    assert not errors, errors
                    page.close()
                finally:
                    stop(process)
            with (home / 'restart.log').open('w') as log:
                process, url = start(home, log)
                try:
                    page, errors = open_page(context, url)
                    assert rpc(page, url, 'stellaSecretary/checkElearning', {'request': {}}) == {'status': 'ready', 'courseCount': 1}
                    assert legacy_grants.read_bytes() == legacy_before
                    assert not errors, errors
                finally:
                    stop(process)
            browser.close()
        print('PASS: native DSH model → MCP → model round trip, inherited native tools, compact settings, legacy-grant preservation and restart; synthetic data only')
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()

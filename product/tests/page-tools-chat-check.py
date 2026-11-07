"""Six business pages share the services called by natural-language tools."""
import io
import json
import os
import runpy
import subprocess
import tempfile
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
from plugin_check import ROOT, CLI, PACK, NativeProvider
from web_check import open_secretary
import official_desktop_check as desktop
PACK = Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK', str(PACK)))
surface = runpy.run_path(str(ROOT / 'product/tests/bill-import-chat-check.py'))['surface']


class Provider(BaseHTTPRequestHandler):
    phase = 0
    state = {}
    docs = None
    start = None
    end = None
    completed = False

    def log_message(self, *_args): pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        data = json.loads(raw)
        names = {tool['function']['name'] for tool in data.get('tools', [])}
        if 'structured_output' in names:
            self.rfile = io.BytesIO(raw)
            return NativeProvider.do_POST(self)
        texts = [message.get('content', '') for message in data['messages'] if message['role'] == 'user']
        content = '\n'.join(text if isinstance(text, str) else str(text) for text in texts)
        subtask = any(isinstance(text, str) and text.startswith('任务内人设：') for text in texts)
        delta, finish = {'content': 'Synthetic task result' if subtask else 'Synthetic title'}, 'stop'
        if 'PAGE_TOOL_INTEGRATION' in content and not subtask and 'stella_business' in names:
            phase = type(self).phase
            if data['messages'][-1]['role'] == 'tool':
                try:
                    result = json.loads(data['messages'][-1]['content'])
                except json.JSONDecodeError as error:
                    raise AssertionError(f'Invalid tool response at phase {phase}: {data["messages"][-1]["content"]}') from error
                assert not result.get('isError'), (phase, result)
                value = result.get('result', result)
                if phase in (1, 4, 11): type(self).state[{1: 'task', 4: 'event', 11: 'schedule'}[phase]] = value
                if phase == 13:
                    assert any(row['id'] == self.state['schedule']['id'] and row['status'] == 'paused' for row in value), value
                phase += 1
                type(self).phase = phase
            state = self.state
            calls = [
                ('tasks', 'catalog', {}),
                ('tasks', 'create_todo', {'payload': {'title': 'Synthetic page task'}}),
                ('tasks', 'complete_todo', {'todo_id': state.get('task', {}).get('id', ''), 'completed': True}),
                ('calendar', 'catalog', {}),
                ('calendar', 'create_event', {'title': 'Synthetic page event', 'start_at': self.start, 'end_at': self.end}),
                ('calendar', 'update_event', {'id': state.get('event', {}).get('id', ''), 'patch': {'title': 'Updated page event'}}),
                ('bills', 'catalog', {}),
                ('bills', 'create_ledger', {'name': '=SUM(1,2)', 'amount_minor': 1234, 'currency': 'CNY', 'entry_type': 'expense', 'occurred_at': self.start}),
                (None, 'stella_export_bills', {'path': str(self.docs / 'result.csv'), 'start': '2026-01-01', 'end': '2099-12-31'}),
                (None, 'stella_import_dates', {'path': str(self.docs / 'dates.csv'), 'timezone': 'Asia/Shanghai'}),
                ('automation', 'catalog', {}),
                ('automation', 'create', {'title': 'Synthetic page schedule', 'prompt': '回顾事务', 'timing': {'kind': 'at', 'at': '2099-11-06T00:00:00Z'}}),
                ('automation', 'edit', {'id': state.get('schedule', {}).get('id', ''), 'action': 'update', 'patch': {'active': False}}),
                ('automation', 'list', {}),
                (None, 'stella_generate_briefing', {}),
                ('automation', 'history', {'id': state.get('schedule', {}).get('id', ''), 'limit': 10}),
            ]
            if phase < len(calls):
                domain, method, args = calls[phase]
                tool = method if domain is None else 'stella_business'
                arguments = args if domain is None else {'domain': domain, 'method': method, 'arguments': args}
                delta = {'tool_calls': [{'index': 0, 'id': f'page-tools-{phase}', 'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(arguments)}}]}
                finish = 'tool_calls'
            else:
                type(self).completed = True
                delta = {'content': 'All page tools completed.'}
        self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
        for choice in [{'delta': {'role': 'assistant'}, 'finish_reason': None}, {'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': finish}]:
            self.wfile.write(('data: ' + json.dumps({'id': 'page-tools', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'synthetic', 'choices': [{'index': 0, **choice}]}) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-page-chat')); artifacts.mkdir(parents=True, exist_ok=True)
    native = os.environ.get('STELLA_CHECK_NATIVE_DESKTOP') == '1'
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider); threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-page-chat-') as temporary, sync_playwright() as playwright:
            home = Path(temporary); docs = home / 'documents'; docs.mkdir(); Provider.docs = docs
            Provider.start = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(); Provider.end = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
            (docs / 'dates.csv').write_text('title,date,annual,uid\nSynthetic special date,2026-11-08,false,special-1\n')
            env = desktop.environment(home) if native else {**{key: value for key, value in os.environ.items() if key in ('PATH', 'USER', 'LOGNAME', 'TMPDIR')}, 'HOME': str(home), 'DSH_HOME': str(home), 'DSH_TELEMETRY_DISABLED': '1'}
            profile = Path(env['DSH_HOME']) / ('profiles/desktop' if native else 'profiles/compatibility'); profile.mkdir(parents=True)
            (profile / 'package.json').write_text(json.dumps({'name': 'page-tool-test', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
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
            contextWindow: 64000
            maxTokens: 4096
            input: [text]
''')
            credential = Path(env['DSH_HOME']) / '.credentials.yaml'; credential.write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n'); credential.chmod(0o600)
            if native:
                desktop.verify_release(); desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True); desktop.native_cli(home, 'page-install', 'add', str(PACK), '--ignore-scripts')
            else:
                with (artifacts / 'install.log').open('w') as log: subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'add', str(PACK), '--ignore-scripts'], env=env, cwd=home, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            with surface(playwright, home, env, artifacts, native) as (page, query, errors):
                open_secretary(page)
                query('stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': 'configure_sources', 'arguments': {'sources': [{'source': 'stella_tasks', 'accountId': 'local'}]}}})
                before = set(page.locator('[data-row-key^="session:"]').evaluate_all('rows => rows.map(row => row.dataset.rowKey)'))
                page.locator('.stella-sidebar-chat').click()
                composer = page.locator('[data-composer-input][contenteditable="true"]'); composer.fill('PAGE_TOOL_INTEGRATION 请按要求操作事务、日程、账单、简报和自动化。'); composer.press('Enter')
                import time
                deadline = time.monotonic() + 15; new = []
                while not new and time.monotonic() < deadline:
                    new = [key for key in page.locator('[data-row-key^="session:"]').evaluate_all('rows => rows.map(row => row.dataset.rowKey)') if key not in before]; page.wait_for_timeout(100)
                assert new
                page.locator('[data-row-key=' + json.dumps(new[0]) + ']').click()
                deadline = time.monotonic() + 90
                while not Provider.completed and time.monotonic() < deadline: page.wait_for_timeout(100)
                assert Provider.completed, f'Failed at phase {Provider.phase}, state={list(Provider.state)}'
                expect(page.get_by_text('All page tools completed.', exact=True)).to_be_visible(timeout=10000)
                saved = query('stellaOrganizer/read', {'request': {}})
                assert any(row['title'] == 'Synthetic page task' and row['status'] == 'done' for row in saved['records'])
                assert any(row['title'] == 'Updated page event' for row in saved['records'])
                assert any(row['title'] == 'Synthetic special date' for row in saved['records'])
                assert any(row['title'] == '=SUM(1,2)' for row in saved['records'])
                assert saved['jobs'] == [], 'Stella no longer owns AgentHub jobs'
                assert (docs / 'result.csv').is_file() and "'=SUM(1,2)" in (docs / 'result.csv').read_text()
                for title in ('事务', '日程', '账单', '简报', '自动化'):
                    page.get_by_role('button', name=title, exact=True).click(); page.screenshot(path=str(artifacts / f'page-{title}.png'))
                assert not errors, errors
        print('PASS: model directly invokes five page domains, imports dates and exports bills under DSH policy; shared saved records and automation persist')
    finally: server.shutdown()


if __name__ == '__main__': main()

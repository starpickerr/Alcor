"""Independent briefing lifecycle on the unchanged official Desktop; synthetic model/data only."""
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from plugin_check import NativeProvider, pack_briefing_fixture
from web_check import open_secretary


class Provider(NativeProvider):
    batches = []
    waiting = threading.Event()
    release = threading.Event()
    hold = True
    failure = None

    def do_POST(self):
        import io
        raw = self.rfile.read(int(self.headers['Content-Length']))
        payload = json.loads(raw)
        tools = payload.get('tools', [])
        submit = next((tool['function'] for tool in tools if tool['function']['name'] == 'briefing_submit'), None)
        if not submit:
            self.rfile = io.BytesIO(raw)
            return super().do_POST()
        try:
            names = {tool['function']['name'] for tool in tools}
            assert names == {'briefing_submit', 'briefing_read'}, 'Unexpected briefing tools: ' + ','.join(sorted(names))
            text = lambda content: content if isinstance(content, str) else '\n'.join(part.get('text', '') for part in content or [])
            prompt = next(text(m['content']) for m in reversed(payload['messages']) if '可阅读的原文数据：' in text(m.get('content')))
            entries = json.loads(prompt.split('可阅读的原文数据：\n')[1])
            schema = submit['parameters']['properties']
            kind = 'global' if 'decisions' in schema and 'reports' in schema else 'screen' if 'decisions' in schema else 'body'
            Provider.batches.append(kind)
            if Provider.hold:
                Provider.waiting.set()
                Provider.release.wait(55)
            decisions = [{'id': row['id'], 'selected': True, 'reason': 'Synthetic validation', 'section': 'action', 'score': 80, 'summary': row['title']} for row in entries]
            if kind == 'screen': value = {'decisions': decisions}
            else:
                ids = [row['id'] for row in entries] if kind == 'global' else schema['source_item_ids']['items']['enum']
                value = {'body': 'Synthetic structured briefing saved through DSH.', 'source_item_ids': ids,
                         'reports': [{'id': identity, 'content': 'Synthetic source report'} for identity in ids]}
                if kind == 'global': value['decisions'] = decisions
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            delta = {'tool_calls': [{'index': 0, 'id': 'briefing-call', 'type': 'function', 'function': {'name': 'briefing_submit', 'arguments': json.dumps(value)}}]}
            for choice in [{'delta': {'role': 'assistant'}, 'finish_reason': None}, {'delta': delta, 'finish_reason': None}, {'delta': {}, 'finish_reason': 'tool_calls'}]:
                data = {'id': 'briefing-synthetic', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'stella-mock', 'choices': [{'index': 0, **choice}]}
                self.wfile.write(('data: ' + json.dumps(data) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            Provider.failure = str(error)
            raise


def business(page, method, arguments=None):
    return desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': method, 'arguments': arguments or {}}})


def main():
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-briefing-independent-') as temporary, sync_playwright() as playwright:
            home = Path(temporary)
            env = desktop.environment(home)
            profile = Path(env['DSH_HOME']) / 'profiles/desktop'
            profile.mkdir(parents=True)
            (profile / 'package.json').write_text(json.dumps({'name': 'briefing-run-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
            (profile / 'cordis.patch.yml').write_text(f'''- id: agent-default-model
  config:
    provider: synthetic
    model: stella-mock
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: stella-mock
            name: Synthetic
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
''')
            credential = Path(env['DSH_HOME']) / '.credentials.yaml'
            credential.write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n'); credential.chmod(0o600)
            fixture = pack_briefing_fixture(home)
            desktop.native_cli(home, 'briefing-runs-install', 'add', str(desktop.PACK), str(fixture), '--ignore-scripts')
            reads_file = profile / 'node_modules/@stella-test/briefing-source/reads.jsonl'
            reads = lambda: [json.loads(line) for line in reads_file.read_text().splitlines() if json.loads(line)['type'] == 'fetch']
            with desktop.desktop(playwright, home, 'briefing-running') as (page, errors):
                open_secretary(page)
                business(page, 'configure_sources', {'sources': [{'source': 'plugin:synthetic:inbox', 'accountId': 'test-account', 'label': 'Synthetic inbox', 'tool': 'synthetic_briefing_fetch', 'method': 'briefing_fetch', 'arguments': {'source_id': 'inbox'}}]})
                page.get_by_role('button', name='简报', exact=True).click()
                page.get_by_role('heading', name='简报', exact=True).wait_for()
                page.get_by_role('button', name='生成今日简报', exact=True).click()
                deadline = time.monotonic() + 35
                while not Provider.waiting.is_set() and time.monotonic() < deadline and not Provider.failure:
                    page.wait_for_timeout(100)
                assert not Provider.failure, Provider.failure
                assert Provider.waiting.is_set(), 'Briefing did not reach model generation'
                run = business(page, 'generation_status')
                assert run['phase'] == 'model' and run['completedBatches'] == 0
                assert run['sessionId'] and 'sources' not in run and 'submissions' not in run
                assert len(reads()) == 2
                page.get_by_role('button', name='事务', exact=True).click()
                page.get_by_role('button', name='简报', exact=True).click()
                expect(page.get_by_role('button', name='取消生成', exact=True)).to_be_visible()
                page.reload()
                page.get_by_role('heading', name='简报', exact=True).wait_for(timeout=30000)
                expect(page.get_by_role('button', name='执行详情', exact=True)).to_be_visible()
                assert business(page, 'generation_status')['requestId'] == run['requestId']
                page.screenshot(path=str(desktop.ARTIFACTS / 'briefing-running.png'))
                assert not errors, errors
            Provider.hold = False
            Provider.release.set()
            with desktop.desktop(playwright, home, 'briefing-resumed') as (page, errors):
                open_secretary(page)
                page.get_by_role('button', name='简报', exact=True).click()
                expect(page.get_by_role('button', name='继续生成', exact=True)).to_be_visible(timeout=15000)
                restored = business(page, 'generation_status')
                assert restored['phase'] == 'interrupted' and restored['completedBatches'] == 0
                assert Provider.batches == ['global'], 'Restart must not replay requests'
                page.get_by_role('button', name='继续生成', exact=True).click()
                expect(page.get_by_role('button', name='查看条目：Plugin item 0', exact=True)).to_be_visible(timeout=35000)
                finished = business(page, 'generation_status')
                assert finished['phase'] == 'completed' and finished['sessionId'] == run['sessionId']
                assert finished['completedBatches'] == 1
                assert Provider.batches == ['global', 'global']
                assert len(reads()) == 2, 'Completed source pages must not be read again'
                page.screenshot(path=str(desktop.ARTIFACTS / 'briefing-completed.png'))
                # Schedule receipt is distinct from completion: use the real DSH scheduler and wait for the saved report.
                business(page, 'configure_sources', {'sources': [{'source': 'stella_tasks', 'accountId': 'local'}]})
                desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'tasks', 'method': 'create_todo', 'arguments': {'payload': {'title': 'Scheduled local task'}}}})
                auto = lambda action, request: desktop.rpc(page, 'stellaOrganizer/automation', {'request': {'action': action, 'scope': 'stella', 'request': request}})
                job = auto('create', {'title': 'Independent scheduled briefing', 'prompt': '整理本机今日事务，生成并保存今日简报。', 'timing': {'kind': 'at', 'at': (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}})
                auto('run', {'id': job['id']})
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    scheduled = business(page, 'generation_status')
                    if scheduled.get('requestId') != run['requestId'] and scheduled['phase'] in ('completed', 'failed'): break
                    page.wait_for_timeout(100)
                assert scheduled['phase'] == 'completed' and scheduled['requestId'] != run['requestId'], scheduled
                assert scheduled['sessionId'] != run['sessionId']
                assert len(reads()) == 2, 'Scheduled local briefings must not read external sources'
                page.set_viewport_size({'width': 430, 'height': 900})
                expect(page.locator('.stella-sidebar [data-sidebar-toggle]')).not_to_be_visible(timeout=5000)
                expect(page.get_by_role('heading', name='简报', exact=True)).to_be_visible()
                page.screenshot(path=str(desktop.ARTIFACTS / 'briefing-completed-narrow.png'))
                assert not errors, errors
            assert not Provider.failure, Provider.failure
        print('PASS: signed official Desktop runs an isolated briefing session with only scoped read/submit tools; navigation/reload preserve work; restart resumes the same session and reuses source checkpoints')
    finally:
        Provider.release.set()
        server.shutdown()


if __name__ == '__main__':
    main()

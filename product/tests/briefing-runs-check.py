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
        structured = next((tool['function'] for tool in tools if tool['function']['name'] == 'structured_output'), None)
        if structured:
            try:
                names = {tool['function']['name'] for tool in tools}
                assert names == {'structured_output'}, 'Briefing must not receive global tools: ' + ','.join(sorted(names))
                kind = 'screen' if 'decisions' in structured['parameters']['properties'] else 'body'
                Provider.batches.append(kind)
                if kind == 'body' and Provider.hold:
                    Provider.waiting.set()
                    Provider.release.wait(55)
            except Exception as error:
                Provider.failure = str(error)
        self.rfile = io.BytesIO(raw)
        try:
            super().do_POST()
        except (BrokenPipeError, ConnectionResetError):
            pass


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
                assert Provider.waiting.is_set(), 'Briefing did not reach body generation'
                run = business(page, 'generation_status')
                assert run['phase'] == 'model' and run['completedBatches'] == 1
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
                assert restored['phase'] == 'interrupted' and restored['completedBatches'] == 1
                assert Provider.batches == ['screen', 'body'], 'Restart must not replay requests'
                page.get_by_role('button', name='继续生成', exact=True).click()
                expect(page.get_by_role('button', name='查看条目：Plugin item 0', exact=True)).to_be_visible(timeout=35000)
                finished = business(page, 'generation_status')
                assert finished['phase'] == 'completed' and finished['sessionId'] == run['sessionId']
                assert finished['completedBatches'] == 2
                assert Provider.batches == ['screen', 'body', 'body']
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
        print('PASS: signed official Desktop runs an isolated briefing session with only structured output; navigation/reload preserve work; restart waits for explicit resume and reuses source/batch checkpoints')
    finally:
        Provider.release.set()
        server.shutdown()


if __name__ == '__main__':
    main()

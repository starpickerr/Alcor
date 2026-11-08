"""Daily briefing workflow in the packed native plugin, isolated data/model only."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer
from playwright.sync_api import expect, sync_playwright
from plugin_check import ROOT, CLI, PACK, NativeProvider, launch, pack_briefing_fixture
from web_check import rpc, stop, open_secretary
from briefing_sources_ui import check_briefing_sources


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-briefing-ui'))
    artifacts.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), NativeProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-briefing-native-') as temporary, sync_playwright() as playwright:
            home = Path(temporary)
            environment = {key: value for key, value in os.environ.items() if key in ('PATH', 'USER', 'LOGNAME', 'TMPDIR')}
            environment.update(HOME=str(home), DSH_HOME=str(home), DSH_TELEMETRY_DISABLED='1')
            profile = home / 'profiles/compatibility'
            profile.mkdir(parents=True)
            (home / 'documents').mkdir()
            (profile / 'package.json').write_text(json.dumps({'name': 'briefing-test', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
            (profile / 'cordis.patch.yml').write_text(f'''- id: workspace-controller
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
- id: session-telemetry-otel
  disabled: true
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
            (home / '.credentials.yaml').write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n')
            os.chmod(home / '.credentials.yaml', 0o600)
            plugin_pack = pack_briefing_fixture(home)
            with (artifacts / 'install.log').open('w') as log:
                subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'add', str(PACK), str(plugin_pack), '--ignore-scripts'], cwd=home, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_locator_handler(page.get_by_role('button', name='Continue', exact=True), lambda button: button.click())
            page.add_locator_handler(page.get_by_role('dialog', name='Get started', exact=True), lambda dialog: dialog.get_by_role('button', name='Close', exact=True).click())
            with (artifacts / 'host.log').open('w') as log:
                process, url = launch(environment, log)
                try:
                    page.goto(url)
                    page.get_by_role('button', name='Account menu', exact=True).wait_for()
                    page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).or_(page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True)).wait_for()
                    open_secretary(page)
                    check_briefing_sources(page, lambda endpoint, args: rpc(page, url, endpoint, args), artifacts)
                    reads_file = profile / 'node_modules/@stella-test/briefing-source/reads.jsonl'
                    reads = lambda: [json.loads(line) for line in reads_file.read_text().splitlines()]
                    assert all(item['type'] == 'catalog' for item in reads()), 'Source discovery must not read content'

                    page.get_by_role('button', name='简报', exact=True).click()
                    page.get_by_role('heading', name='简报', exact=True).wait_for()
                    expect(page.get_by_text('补充材料', exact=True)).to_have_count(0)
                    expect(page.get_by_text('本次来源：', exact=False)).to_have_count(0)
                    page.get_by_role('button', name='简报设置', exact=True).click()
                    dialog = page.locator('[data-briefing-settings]')
                    dialog.get_by_role('button', name='新增规则', exact=True).click()
                    rule = page.get_by_role('dialog', name='新增规则', exact=True)
                    rule.get_by_label('规则内容', exact=True).fill('保留需要处理的事项。')
                    rule.get_by_role('button', name='保存规则', exact=True).click()
                    rule = page.get_by_role('dialog', name='规则详情', exact=True)
                    expect(rule.get_by_role('switch', name='启用规则', exact=True)).to_have_attribute('aria-checked', 'true')
                    rule.get_by_role('button', name='关闭', exact=True).click()
                    dialog.get_by_role('button', name='返回简报', exact=True).click()
                    page.get_by_role('button', name='生成今日简报', exact=True).click()
                    try:
                        expect(page.get_by_role('button', name='查看条目：Plugin item 0', exact=True)).to_be_visible(timeout=30000)
                    except Exception:
                        print(page.locator('body').inner_text()[-5000:], flush=True)
                        raise
                    expect(page.get_by_role('button', name='更新今日简报', exact=True)).to_be_enabled(timeout=10000)
                    fetched = [item for item in reads() if item['type'] == 'fetch']
                    assert [item['offset'] for item in fetched] == [0, 1]
                    assert all(item['source_id'] == 'inbox' for item in fetched)
                    page.get_by_role('button', name='查看条目：Plugin item 0', exact=True).click()
                    expect(page.locator('.alcor-report')).to_contain_text('Synthetic source report')
                    expect(page.locator('.alcor-report h2')).to_have_text('Synthetic source report')
                    expect(page.locator('.alcor-report strong')).to_have_text('Important')
                    expect(page.locator('.alcor-report a')).to_have_attribute('href', 'https://example.com/reference')
                    expect(page.locator('.alcor-report pre')).to_contain_text('print(1)')
                    assert not page.evaluate('Boolean(window.__briefingUnsafe)')
                    expect(page.locator('.alcor-report img')).to_have_count(0)


                    page.get_by_role('button', name='转为事务', exact=True).first.click()
                    decision = page.get_by_role('dialog', name='处理简报事项', exact=True)
                    decision.get_by_role('button', name='确认处理', exact=True).click()
                    expect(decision).not_to_be_visible()
                    page.get_by_role('button', name='返回报告列表', exact=True).click()
                    page.get_by_label('全选', exact=True).check()
                    page.get_by_role('button', name='建立事务', exact=False).click()
                    expect(page.get_by_role('button', name='建立事务', exact=False)).to_be_disabled()
                    page.get_by_text('来源与时间窗口', exact=True).click()
                    groups = page.locator('.alcor-briefing-source-group')
                    expect(groups).not_to_have_count(0)
                    for group in groups.all():
                        expect(group.locator('.alcor-briefing-source-window time')).to_have_count(2)
                        assert group.locator('.alcor-briefing-source-window').evaluate('e => Math.abs(e.getBoundingClientRect().right - e.lastElementChild.getBoundingClientRect().right) < 2'), 'Time must align to the right edge'
                    expect(page.locator('.alcor-source-summary > p')).to_have_count(0)
                    page.screenshot(path=str(artifacts / 'briefing-source-windows.png'))

                    page.get_by_role('button', name='plugin:synthetic:inbox · 候选信息', exact=True).click()
                    expect(page.get_by_role('button', name='返回 AI 报告', exact=True)).to_be_visible()
                    page.get_by_role('button', name='返回 AI 报告', exact=True).click()
                    page.get_by_role('button', name='更新今日简报', exact=True).click()
                    expect(page.locator('.alcor-briefing-revisions [data-revision]')).to_have_count(2, timeout=30000)
                    expect(page.locator('.alcor-briefing-history > button')).to_have_count(1)
                    expect(page.get_by_role('button', name='更新今日简报', exact=True)).to_be_enabled(timeout=10000)
                    page.locator('.alcor-briefing-revisions [data-revision]').last.click()
                    expect(page.get_by_text('历史版本 1', exact=False)).to_be_visible()
                    page.get_by_role('button', name='当前版本', exact=False).click()
                    page.screenshot(path=str(artifacts / 'briefing-desktop.png'))
                    for theme in ('dark', 'light'):
                        page.emulate_media(color_scheme=theme)
                        page.set_viewport_size({'width': 520, 'height': 900})
                        assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
                        page.screenshot(path=str(artifacts / f'briefing-{theme}-narrow.png'))
                    page.reload()
                    expect(page.locator('.alcor-briefing-revisions [data-revision]')).to_have_count(2)
                    result = rpc(page, url, 'alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': 'list_briefings'}})
                    assert len(result) == 1
                    auto = lambda action, request: rpc(page, url, 'alcorOrganizer/automation', {'request': {'scope': 'alcor', 'action': action, 'request': request}})
                    rpc(page, url, 'alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': 'configure_sources', 'arguments': {'sources': [{'source': 'alcor_tasks', 'accountId': 'local'}]}}})
                    job = auto('create', {'title': 'Synthetic daily integration', 'prompt': '整理本机今日事务、日程和待处理账单，生成并保存今日简报。', 'timing': {'kind': 'at', 'at': (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')}})
                    before = len(rpc(page, url, 'alcorOrganizer/read', {'request': {}})['records'])
                    external_before = len([item for item in reads() if item['type'] == 'fetch'])
                    auto('run', {'id': job['id']})
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        records = rpc(page, url, 'alcorOrganizer/read', {'request': {}})['records']
                        if len(records) > before: break
                        page.wait_for_timeout(100)
                    assert len([item for item in reads() if item['type'] == 'fetch']) == external_before, 'Scheduled template must not inherit external source permission'
                    assert len(records) == before + 1, 'Scheduled briefing must use the same model pipeline and save a result'
                    auto('edit', {'id': job['id'], 'action': 'update', 'patch': {'active': False}})
                    # Exercise the no-items body path independently of per-item reports.
                    business = lambda method, args: rpc(page, url, 'alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': method, 'arguments': args}})
                    empty = business('start_briefing', {'briefing_date': '2026-10-01', 'title': 'Preview test'})
                    business('complete_briefing', {'briefing_id': empty['id'], 'payload': {'body': '## Empty briefing preview\n\n- **No pending items**', 'items': []}})
                    page.get_by_role('button', name='刷新简报', exact=True).click()
                    page.locator('.alcor-briefing-history > button').filter(has_text='Preview test').click()
                    expect(page.locator('.alcor-report h2')).to_have_text('Empty briefing preview')
                    expect(page.locator('.alcor-report strong')).to_have_text('No pending items')
                    assert not errors, errors
                finally:
                    page.screenshot(path=str(artifacts / 'final.png'))
                    stop(process)
            browser.close()
        print('PASS: direct model generation without prior chat, rules, daily updates, revisions, task action, reload, narrow light/dark UI, scheduled model briefing')
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()

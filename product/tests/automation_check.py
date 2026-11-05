"""Shared native automation UI, ownership, templates and real synthetic tool execution."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

from playwright.sync_api import expect, sync_playwright
from web_check import Provider, requests, rpc, start, stop, open_page, open_secretary


def check_automation(page, url):
    def auto(action, request=None, scope='stella'):
        return rpc(page, url, 'stellaOrganizer/automation', {'request': {'scope': scope, 'action': action, **({} if request is None else {'request': request})}})

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    owner = auto('create', {'title': 'Owner automation only', 'prompt': 'Owner synthetic instruction', 'timing': {'kind': 'at', 'at': future}}, 'all')
    task = auto('create', {'title': 'Shared Stella automation', 'prompt': 'Stella synthetic instruction', 'timing': {'kind': 'at', 'at': future}})
    assert task['target']['create']['agentPreset'] == 'stella'
    assert {item['id'] for item in auto('catalog')} == {task['id']}
    assert {item['id']: item['sourceLabel'] for item in auto('catalog', scope='all')} == {owner['id']: 'DSH', task['id']: 'Stella'}
    open_secretary(page)
    page.get_by_role('button', name='自动化', exact=True).click()
    manager = page.get_by_test_id('task-manager-page')
    expect(manager).to_be_visible()
    expect(manager.get_by_role('button', name=owner['title'], exact=True)).to_have_count(0)
    manager.get_by_role('button', name=task['title'], exact=True).click()
    page.get_by_label('Automation task name', exact=True).fill('Edited in Stella')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(manager.get_by_role('button', name='Edited in Stella', exact=True)).to_be_visible()
    task = next(item for item in auto('catalog') if item['id'] == task['id'])
    # A stale edit must not overwrite the new title.
    stale = auto('edit', {'id': task['id'], 'action': 'update', 'patch': {'title': 'Stale edit'}, 'expectedUpdatedAt': '2000-01-01T00:00:00.000Z'})
    assert stale['code'] == 'schedule_conflict'
    page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).click()
    page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
    page.get_by_role('button', name='Automation tasks', exact=True).click()
    expect(manager.get_by_role('button', name=owner['title'], exact=True)).to_be_visible()
    expect(manager.get_by_role('button', name='Edited in Stella', exact=True)).to_be_visible()
    expect(manager.get_by_text('Stella', exact=True)).to_be_visible()
    manager.get_by_role('button', name='Edited in Stella', exact=True).click()
    page.get_by_label('Automation task name', exact=True).fill('Edited in DSH')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(manager.get_by_role('button', name='Edited in DSH', exact=True)).to_be_visible()
    # Both new-session modes retain Stella even if a caller supplied a different preset.
    changed = auto('edit', {'id': task['id'], 'action': 'update', 'patch': {'target': {'kind': 'new', 'create': {'agentPreset': 'default'}}}}, 'all')
    assert changed['target']['create']['agentPreset'] == 'stella'
    open_secretary(page)
    page.get_by_role('button', name='自动化', exact=True).click()
    expect(manager.get_by_role('button', name='Edited in DSH', exact=True)).to_be_visible()
    manager.get_by_role('button', name='Edited in DSH', exact=True).click()
    page.get_by_role('button', name='Pause', exact=True).click()
    page.get_by_role('button', name='Paused', exact=True).click()
    expect(manager.get_by_role('button', name='Edited in DSH', exact=True)).to_be_visible()
    page.get_by_role('button', name='Resume', exact=True).click()
    page.get_by_role('button', name='Enabled', exact=True).click()
    expect(manager.get_by_role('button', name='Edited in DSH', exact=True)).to_be_visible()
    page.get_by_role('tab', name='Records', exact=True).click()
    expect(page.get_by_text('No delivery record available', exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    before = len(requests)
    page.get_by_role('button', name='New', exact=True).click()
    expect(page.get_by_label('Automation task name', exact=True)).to_have_value('')
    page.keyboard.press('Escape')
    page.get_by_role('button', name='Choose a creation method', exact=True).click()
    page.get_by_role('menuitem', name='每周事务回顾', exact=True).click()
    expect(page.get_by_label('Automation task name', exact=True)).to_have_value('每周事务回顾')
    expect(page.locator('textarea')).to_have_value(re.compile('回顾本机未完成事务'))
    page.get_by_label('Automation task name', exact=True).fill('Retained weekly draft')
    page.keyboard.press('Escape')
    # Templates are only drafts. Saving through the native form creates one Host record.
    page.get_by_role('button', name='Choose a creation method', exact=True).click()
    page.get_by_role('menuitem', name='每日简报', exact=True).click()
    expect(page.get_by_label('Automation task name', exact=True)).to_have_value('每日简报')
    expect(page.locator('textarea')).to_have_value(re.compile('生成并保存今日简报'))
    page.get_by_label('Automation task name', exact=True).fill('Synthetic daily briefing')
    page.get_by_role('button', name='Create', exact=True).click()
    expect(manager.get_by_role('button', name='Synthetic daily briefing', exact=True)).to_be_visible()
    assert len(requests) == before, 'Viewing, editing and saving must not call a model'
    daily = next(item for item in auto('catalog') if item['title'] == 'Synthetic daily briefing')
    assert daily['target']['create']['agentPreset'] == 'stella'
    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
        Path(directory).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(directory) / 'automation-desktop.png'), animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.locator('textarea')).to_be_visible()
    assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
        page.screenshot(path=str(Path(directory) / 'automation-mobile.png'), animations='disabled')
    page.set_viewport_size({'width': 1280, 'height': 850})
    records_before = len(rpc(page, url, 'stellaOrganizer/read', {'request': {}})['records'])
    auto('run', {'id': daily['id']})
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        data = rpc(page, url, 'stellaOrganizer/read', {'request': {}})
        if len(data['records']) > records_before:
            break
        page.wait_for_timeout(100)
    assert len(data['records']) == records_before + 1, 'Scheduled tool must save a real briefing'
    assert data['records'][-1]['kind'] == 'briefing'
    history = auto('history', {'id': daily['id'], 'limit': 20})
    assert history['records'], history
    page.get_by_role('button', name='Choose a creation method', exact=True).click()
    page.get_by_role('menuitem', name='每周事务回顾', exact=True).click()
    expect(page.get_by_label('Automation task name', exact=True)).to_have_value('Retained weekly draft')
    page.get_by_role('button', name='Create', exact=True).click()
    expect(manager.get_by_role('button', name='Retained weekly draft', exact=True)).to_be_visible()
    weekly = next(item for item in auto('catalog') if item['title'] == 'Retained weekly draft')
    assert 'FREQ=WEEKLY' in weekly['rrule'] and 'BYDAY=MO' in weekly['rrule']
    assert weekly['target']['create']['agentPreset'] == 'stella'
    auto('run', {'id': weekly['id']})
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if any(message.get('role') == 'tool' and 'completed' in str(message.get('content')) for payload in requests if '回顾本机未完成事务' in json.dumps(payload, ensure_ascii=False) for message in payload['messages']):
            break
        page.wait_for_timeout(100)
    else:
        raise AssertionError('Scheduled weekly review must return the local task summary')
    assert len(rpc(page, url, 'stellaOrganizer/read', {'request': {}})['records']) == records_before + 1
    # Removing in DSH removes the same task in Stella; unrelated tasks survive.
    auto('edit', {'id': task['id'], 'action': 'delete'}, 'all')
    assert all(item['id'] != task['id'] for item in auto('catalog'))
    assert any(item['id'] == owner['id'] for item in auto('catalog', scope='all'))
    return daily['id'], owner['id']


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-automation-check-') as temporary, sync_playwright() as playwright:
            home = Path(temporary)
            (home / '.stella-product.json').write_text('{"version":1,"kind":"web"}')
            profile = home / 'profiles/stella'
            profile.mkdir(parents=True)
            (profile / 'cordis.patch.yml').write_text(f'''- id: agent-default-model
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
''')
            (home / '.credentials.yaml').write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n')
            os.chmod(home / '.credentials.yaml', 0o600)
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            with (home / 'host.log').open('w') as log:
                process, url = start(home, log)
                page = None
                try:
                    page, errors = open_page(context, url)
                    check_automation(page, url)
                    assert not errors, errors
                    print('PASS: shared native automation, scoped ownership, bidirectional edits, CAS, identity, template drafts, desktop/mobile and scheduled briefing tool; synthetic provider only')
                except Exception:
                    if page:
                        print('ERRORS:', errors)
                        print('PAGE:', page.locator('body').inner_text()[-9000:])
                        if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                            Path(directory).mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(Path(directory) / 'automation-failure.png'))
                    raise
                finally:
                    stop(process)
                    browser.close()
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()

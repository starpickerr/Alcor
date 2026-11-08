"""Embedded secretary chat on real DSH; isolated data and synthetic model only."""
import json
import re
import os
from pathlib import Path
import tempfile
from fresh_profile_fixture import fresh_test_home, initialize_fresh
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import expect, sync_playwright
from web_check import Provider, open_page, open_secretary, requests, rpc, start, stop, settings


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with fresh_test_home(prefix='alcor-bear-check-') as temporary, sync_playwright() as p:
            home = Path(temporary)
            (home / '.stella-product.json').write_text('{"version":1,"kind":"web"}\n')
            profile = home / 'profiles/stella'
            profile.mkdir(parents=True)
            initialize_fresh(temporary, home, profile)
            (home / 'documents').mkdir()
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
''')
            credentials = home / '.credentials.yaml'
            credentials.write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n')
            credentials.chmod(0o600)
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 1280, 'height': 900}, reduced_motion='reduce')
            with (home / 'host.log').open('w') as log:
                process, url = start(home, log)
                try:
                    page, errors = open_page(context, url)
                    open_secretary(page)
                    bear = page.get_by_role('button', name='Alcor 小熊', exact=True)
                    chat = page.get_by_role('region', name='Alcor 小熊对话', exact=True)
                    composer = chat.get_by_label('给 Alcor 发消息', exact=True)
                    original = page.evaluate('localStorage.getItem("dsh.sessions.current")')
                    calls_before = len(requests)
                    def count_sessions():
                        return len(rpc(page, url, 'session/list', {'_request': {}})['items'])
                    before = count_sessions()
                    bear.hover()
                    expect(composer).to_be_visible()
                    page.mouse.move(10, 80)
                    expect(chat).to_have_count(0)
                    bear.click()
                    expect(composer).to_be_visible()
                    assert count_sessions() == before and len(requests) == calls_before
                    composer.fill('Read alcor_overview in the bear chat')
                    composer.press('Enter')
                    chat.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=30000)
                    first_id = chat.locator('[data-conversation-session]').get_attribute('data-conversation-session')
                    assert first_id and page.evaluate('localStorage.getItem("dsh.sessions.current")') == original
                    assert any('当前页面' in json.dumps(msg, ensure_ascii=False) and '事务' in json.dumps(msg, ensure_ascii=False)
                               for request in requests for msg in request['messages'] if msg['role'] == 'user')
                    composer.fill('日程草稿')
                    page.get_by_role('button', name='日程', exact=True).click()
                    expect(composer).to_have_value('日程草稿')
                    expect(chat.locator('[data-conversation-session]')).to_have_attribute('data-conversation-session', first_id)
                    composer.press('Escape')
                    expect(chat).to_have_count(0)
                    box = bear.bounding_box()
                    page.mouse.move(box['x']+30,box['y']+30)
                    page.mouse.down()
                    page.mouse.move(135,180,steps=6)
                    page.mouse.up()
                    expect(chat).to_have_count(0)
                    moved = bear.bounding_box()
                    assert abs(moved['x']-105) < 5
                    page.reload()
                    expect(bear).to_be_visible()
                    assert abs(bear.bounding_box()['x']-moved['x']) < 2
                    bear.click()
                    expect(composer).to_have_value('日程草稿')
                    expect(chat.locator('[data-conversation-session]')).to_have_attribute('data-conversation-session', first_id)
                    composer.press('Escape')
                    settings(page)
                    page.get_by_label('悬停展开输入框').uncheck()
                    page.get_by_role('button', name='恢复默认位置').click()
                    page.get_by_label('显示 Alcor 小熊').uncheck()
                    page.get_by_role('button', name='Close', exact=True).click()
                    expect(bear).to_have_count(0)
                    settings(page)
                    page.get_by_label('显示 Alcor 小熊').check()
                    page.get_by_role('button', name='Close', exact=True).click()
                    expect(bear).to_be_visible()
                    assert bear.bounding_box()['x'] > moved['x']
                    bear.hover()
                    page.wait_for_timeout(450)
                    expect(chat).to_have_count(0)
                    page.locator('.alcor-sidebar-chat').click()
                    expect(bear).to_have_count(0)
                    chip = page.locator('[data-slot="conversation.hero.agentPreset"] button').first
                    chip.click()
                    menu = page.get_by_role('menu').last
                    expect(menu).to_contain_text('Alcor')
                    assert '兼容' not in menu.inner_text() and '简报执行' not in menu.inner_text()
                    page.keyboard.press('Escape')
                    page.get_by_role('button', name='日程', exact=True).click()
                    expect(bear).to_be_visible()
                    extra = home / 'another-workspace'
                    extra.mkdir()
                    extra_id = rpc(page, url, 'workspace/create', {'request': {'path': str(extra)}})['workspace']['workspaceId']
                    row = page.locator(f'[data-row-key="workspace:{extra_id}"]')
                    row.hover()
                    row.get_by_role('button').last.click()
                    expect(bear).to_have_count(0)
                    second_id = page.locator('[data-conversation-session]').first.get_attribute('data-conversation-session')
                    assert second_id and second_id != first_id
                    page.get_by_role('button', name='日程', exact=True).click()
                    bear.click()
                    expect(composer).to_have_value('')
                    composer.fill('Read alcor_overview for this calendar date')
                    composer.press('Enter')
                    chat.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=30000)
                    expect(chat.locator('[data-conversation-session]')).to_have_attribute('data-conversation-session',second_id)
                    assert any('当前页面：日程 · 日期 ' in str(msg)
                               for request in requests for msg in request['messages'] if msg['role']=='user')
                    if directory := os.environ.get('ALCOR_CHECK_ARTIFACTS'):
                        Path(directory).mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(Path(directory)/'bear-chat.png'))
                    composer.press('Escape')
                    mode = page.get_by_role('button',name=re.compile('切换使用模式'))
                    mode.click()
                    page.get_by_role('menuitem',name='DeepSeek Harness',exact=True).click()
                    expect(bear).to_have_count(0)
                    mode.click()
                    page.get_by_role('menuitem',name='Alcor',exact=True).click()
                    expect(bear).to_be_visible()
                    page.set_viewport_size({'width':390,'height':844})
                    bear.click()
                    expect(composer).to_be_in_viewport()
                    assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
                    assert not errors, errors
                except Exception:
                    print('Browser errors:', errors)
                    print('Navigation:', page.evaluate('localStorage.getItem("alcor.product-mode.v1")'))
                    print('Buttons:', page.get_by_role('button').all_text_contents())
                    print('Page:', page.locator('body').inner_text()[-4000:])
                    raise
                finally:
                    stop(process)
            browser.close()
        print('PASS: bear chat, native tool round trip, cross-page draft, drag, reload, settings, preset menu and narrow window')
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()

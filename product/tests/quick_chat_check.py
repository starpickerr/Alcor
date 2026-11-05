"""Embedded secretary chat on real DSH; isolated data and synthetic model only."""
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import ThreadingHTTPServer

from playwright.sync_api import expect, sync_playwright
from web_check import Provider, open_page, open_secretary, requests, rpc, start, stop


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-quick-chat-') as temporary, sync_playwright() as p:
            home = Path(temporary)
            (home / '.stella-product.json').write_text('{"version":1,"kind":"web"}\n')
            profile = home / 'profiles/stella'
            profile.mkdir(parents=True)
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
                    calls_before = len(requests)
                    page.get_by_role('button', name='展开', exact=True).click()
                    chat = page.get_by_role('region', name='事务快捷对话', exact=True)
                    composer = chat.locator('[data-composer-input][contenteditable="true"]')
                    composer.wait_for(timeout=7000)
                    assert len(requests) == calls_before, 'Opening the inline composer must not call a model'
                    expect(chat.get_by_role('button', name='Synthetic model')).to_be_visible()
                    original = page.evaluate('localStorage.getItem("dsh.sessions.current")')
                    composer.fill('Read stella_overview in the embedded task chat')
                    composer.press('Enter')
                    chat.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=25000)
                    first_id = chat.locator('[data-conversation-session]').get_attribute('data-conversation-session')
                    assert page.evaluate('localStorage.getItem("dsh.sessions.current")') == original
                    expect(page.get_by_role('heading', name='事务', exact=True)).to_be_visible()
                    composer.fill('Unsent task chat draft')
                    page.get_by_role('button', name='收起', exact=True).click()
                    expect(chat).to_have_count(0)
                    page.get_by_role('button', name='展开', exact=True).click()
                    expect(composer).to_have_text('Unsent task chat draft')
                    chat.get_by_role('button', name='新会话', exact=True).click()
                    expect(chat.locator('[data-conversation-session]')).not_to_have_attribute('data-conversation-session', first_id)
                    expect(composer).to_have_text('')
                    chat.get_by_label('历史会话', exact=True).select_option(first_id)
                    expect(composer).to_have_text('Unsent task chat draft')
                    page.reload()
                    page.get_by_role('button', name='展开', exact=True).click()
                    expect(chat.locator('[data-conversation-session]')).to_have_attribute('data-conversation-session', first_id)
                    expect(composer).to_have_text('Unsent task chat draft')
                    chat.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for()
                    extra = home / 'another-workspace'
                    extra.mkdir()
                    extra_id = rpc(page, url, 'workspace/create', {'request': {'path': str(extra)}})['workspace']['workspaceId']
                    chat.get_by_label('快捷对话工作区', exact=True).select_option(extra_id)
                    expect(chat.locator('[data-conversation-session]')).not_to_have_attribute('data-conversation-session', first_id)
                    changed_id = chat.locator('[data-conversation-session]').get_attribute('data-conversation-session')
                    assert changed_id in rpc(page, url, 'workspace/create', {'request': {'path': str(extra)}})['workspace']['sessionIds']
                    assert page.evaluate('localStorage.getItem("dsh.sessions.current")') == original
                    page.get_by_role('button', name='日程', exact=True).click()
                    page.get_by_role('button', name='展开', exact=True).click()
                    calendar = page.get_by_role('region', name='日程快捷对话', exact=True)
                    calendar.locator('[data-composer-input][contenteditable="true"]').fill('Read stella_overview in the calendar chat')
                    calendar.locator('[data-composer-input]').press('Enter')
                    calendar.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=25000)
                    assert calendar.locator('[data-conversation-session]').get_attribute('data-conversation-session') != first_id
                    expect(calendar.get_by_label('历史会话', exact=True).locator(f'option[value="{first_id}"]')).to_have_count(0)
                    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                        Path(directory).mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(Path(directory) / 'quick-chat-desktop.png'), animations='disabled')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    calendar.locator('[data-composer-input]').scroll_into_view_if_needed()
                    expect(calendar.locator('[data-composer-input]')).to_be_in_viewport()
                    assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
                    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                        page.screenshot(path=str(Path(directory) / 'quick-chat-mobile.png'), animations='disabled')
                    assert not errors, errors
                except Exception:
                    print('Browser errors:', errors)
                    print('Navigation:', page.evaluate('localStorage.getItem("stella.product-mode.v1")'))
                    print('Buttons:', page.get_by_role('button').all_text_contents())
                    print('Page:', page.locator('body').inner_text()[-4000:])
                    raise
                finally:
                    stop(process)
            browser.close()
        print('PASS: embedded task/calendar chat, native model/tool round trip, no navigation, history, draft, reload and narrow window')
    finally:
        server.shutdown()


if __name__ == '__main__':
    main()

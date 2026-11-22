"""Check Alcor sidebar disclosures and workspace scrolling in a disposable official Desktop."""
import json
import os
import re
import threading
from http.server import ThreadingHTTPServer
from uuid import uuid4
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from web_check import Provider, open_secretary


def seed_workspace_history(page, home):
    # New workspaces appear first; the oldest workspace contains the target conversation.
    target_id = None
    for index in range(18):
        path = home / f'测试工作区-{index:02}'
        path.mkdir()
        workspace = desktop.rpc(page, 'workspace/create', {'request': {'path': str(path)}})['workspace']
        if index == 0:
            created = desktop.rpc(page, 'session/create', {
                'request': {'workspaceId': workspace['workspaceId'], 'agentPreset': 'alcor'},
            })
            target_id = created['sessionId']
            desktop.rpc(page, 'session/prompt', {'request': {
                'sessionId': target_id, 'requestId': str(uuid4()), 'mode': 'queue', 'clientTimeZone': 'Asia/Shanghai',
                'content': [{'type': 'text', 'text': '独立侧栏验收'}],
            }})
            desktop.rpc(page, 'session/rename', {'request': {'sessionId': target_id, 'title': '归档整理会话-00'}})
    return target_id


def scroll_to(page, header, row, delta=2400):
    # Wheel inside the list, never focus/scroll the target locator programmatically.
    box = header.bounding_box()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] + 28)
    page.mouse.wheel(0, delta)
    expect(row).to_be_in_viewport(timeout=5000)


def check_sidebar(page, home):
    open_secretary(page)
    page.set_viewport_size({'width': 1130, 'height': 690})
    target_id = seed_workspace_history(page, home)
    apps = page.get_by_role('button', name='应用', exact=True)
    system = page.get_by_role('button', name='系统', exact=True)
    work = page.get_by_role('button', name='工作区', exact=True)
    header = page.locator('[data-sidebar-workspace-header]')
    tree = page.get_by_role('tree', name='会话')
    tasks = page.get_by_role('button', name='事务', exact=True)
    logs = page.get_by_role('button', name='日志', exact=True)

    for toggle in [apps, system, work]:
        expect(toggle).to_have_attribute('aria-expanded', 'true')
    list_area = page.locator('[data-sidebar-list-area]')
    def geometry():
        return list_area.evaluate('el=>({height:el.clientHeight, maxScrollTop:el.scrollHeight-el.clientHeight, scrollTop:el.scrollTop})')
    initial_top = header.bounding_box()['y']
    print('690px initial workspace list:', geometry(), flush=True)
    apps.focus()
    apps.press('Space')
    expect(apps).to_have_attribute('aria-expanded', 'false')
    expect(tasks).to_be_hidden()
    expect(logs).to_be_visible()
    assert header.bounding_box()['y'] < initial_top - 30
    print('690px applications folded:', geometry(), flush=True)
    system.press('Enter')
    expect(logs).to_be_hidden()
    expect(tree).to_be_visible()
    work.press('Space')
    expect(work).to_have_attribute('aria-expanded', 'false')
    expect(tree).to_be_hidden()
    for label in ['搜索会话', '视图选项', '添加工作区']:
        expect(header.get_by_role('button', name=label, exact=True)).to_be_in_viewport()

    page.reload()
    for toggle in [apps, system, work]:
        expect(toggle).to_have_attribute('aria-expanded', 'false')
    search = header.get_by_role('button', name='搜索会话', exact=True)
    for folded in (True, False):
        if work.get_attribute('aria-expanded') != str(not folded).lower():
            work.click()
        search.click()
        field = header.get_by_placeholder('搜索会话名称')
        field.fill('归档整理会话-00')
        expect(page.get_by_role('treeitem', name='归档整理会话-00')).to_be_visible()
        field.press('Escape')
    if work.get_attribute('aria-expanded') == 'false':
        work.click()
    expect(tree).to_be_visible()

    page.set_viewport_size({'width': 1130, 'height': 520})
    apps.click()
    system.click()
    expect(tasks).to_be_visible()
    expect(logs).to_be_visible()
    expect(work).to_be_in_viewport()
    footer = page.locator('[class$="_footArea"]').get_by_role('button', name=re.compile('^(更多|账号菜单|More|Account menu)$')).last
    expect(footer).to_be_in_viewport()
    old_workspace = tree.get_by_role('treeitem', name='测试工作区-00', exact=True)
    scroll_to(page, header, old_workspace)
    expect(work).to_be_in_viewport()
    expect(footer).to_be_in_viewport()
    print('520px all sections open after wheel:', geometry(), flush=True)
    page.screenshot(path=str(desktop.ARTIFACTS / 'sidebar-short-open.png'), scale='css')
    scroll_to(page, header, tree.get_by_role('treeitem', name='测试工作区-17', exact=True), delta=-2400)
    expect(work).to_be_in_viewport()
    apps.click()
    system.click()

    scroll_to(page, header, old_workspace)
    if old_workspace.get_attribute('aria-expanded') == 'false':
        old_workspace.click()
    session_row = tree.locator(f'[data-row-key="session:{target_id}"]')
    scroll_to(page, header, session_row)
    expect(work).to_be_in_viewport()
    page.screenshot(path=str(desktop.ARTIFACTS / 'sidebar-short-scrolled.png'), scale='css')
    print('520px after wheel:', geometry(), flush=True)
    session_row.click()
    expect(session_row).to_have_attribute('aria-selected', 'true')

    page.set_viewport_size({'width': 720, 'height': 620})
    opener = page.locator('[data-sidebar-opener]')
    opener.click()
    expect(apps).to_be_in_viewport()
    apps.click()
    expect(tasks).to_be_in_viewport()
    work.click()
    expect(tree).to_be_hidden()
    work.click()
    expect(tree).to_be_visible()
    page.screenshot(path=str(desktop.ARTIFACTS / 'sidebar-narrow-drawer.png'), scale='css')
    page.keyboard.press('Escape')


def main():
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    pack = Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK', desktop.PACK)).resolve(strict=True)
    with ThreadingHTTPServer(('127.0.0.1', 0), Provider) as server, fresh_test_home('alcor-sidebar-sections-') as temporary, sync_playwright() as playwright:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        initialize_fresh(temporary, home / 'dsh', profile)
        (profile / 'package.json').write_text(json.dumps({
            'name': 'alcor-sidebar-check', 'private': True,
            'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}},
        }))
        (profile / 'cordis.patch.yml').write_text(f"""- id: agent-default-model
  config:
    provider: synthetic
    model: sidebar
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: SIDEBAR_TEST_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: sidebar
            name: Sidebar test
            contextWindow: 32000
""")
        credential = home / 'dsh/.credentials.yaml'
        credential.write_text('version: 1\nrefs:\n  SIDEBAR_TEST_KEY: synthetic-test-key\n')
        credential.chmod(0o600)
        desktop.native_cli(home, 'sidebar-install', 'add', str(pack), '--ignore-scripts')
        try:
            with desktop.desktop(playwright, home, 'sidebar-sections') as (page, errors):
                check_sidebar(page, home)
                assert not errors, errors
        finally:
            server.shutdown()
    print('PASS: sidebar disclosure, persistence, workspace search, short-window scrolling and selection')


if __name__ == '__main__':
    main()

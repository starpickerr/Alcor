"""Workspace adoption and private identity round trip with disposable local data."""
import json
import os
from pathlib import Path
from playwright.sync_api import expect
from web_check import rpc, settings, requests, open_secretary, start_secretary_chat


def check_workspace(page, url, home, compatible=False):
    legacy = home / 'legacy'
    legacy.mkdir()
    for name, content in {'AGENT.md': 'WORKSPACE_PERSONA_SENTINEL {{literal}}', 'USER.md': 'WORKSPACE_USER_SENTINEL', 'RULE.md': 'WORKSPACE_RULE_SENTINEL', '.env': 'NEVER_IMPORT_SECRET'}.items():
        (legacy / name).write_text(content)
    (legacy / 'skills').mkdir()
    personal = home / 'personal-files'
    project = home / 'project-files'
    personal.mkdir()
    project.mkdir()
    project_id = rpc(page, url, 'workspace/create', {'request': {'path': str(project)}})['workspace']['workspaceId']
    source = {key: str(legacy) for key in ['application', 'agent', 'user', 'shared']}
    before = len(requests)
    settings(page)
    section = page.locator('.stella-workspace-settings')
    page.get_by_label('接入已有文件目录', exact=True).fill(str(personal))
    previous = page.evaluate('localStorage.getItem("dsh.sessions.current")')
    page.get_by_role('button', name='登记并打开', exact=True).click()
    page.wait_for_function('previous => localStorage.getItem("dsh.sessions.current") !== previous', arg=previous)
    if not section.is_visible(): settings(page)
    repeated = rpc(page, url, 'workspace/create', {'request': {'path': str(personal)}})
    personal_id = repeated['workspace']['workspaceId']
    assert not repeated['created'] and personal_id != project_id
    expect(page.get_by_label('DSH 文件工作区', exact=True)).to_have_value(personal_id)
    page.get_by_label('DSH 文件工作区', exact=True).select_option(project_id)
    previous = page.evaluate('localStorage.getItem("dsh.sessions.current")')
    page.get_by_role('button', name='打开工作区', exact=True).click()
    page.wait_for_function('previous => localStorage.getItem("dsh.sessions.current") !== previous', arg=previous)
    if not section.is_visible(): settings(page)
    state = rpc(page, url, 'stellaSecretary/workspaceState', {'request': {}})
    assert state['personalWorkspaceId'] is None and state['projectWorkspaceId'] is None
    expect(page.get_by_label('Stella 个人工作区', exact=True)).to_have_count(0)
    expect(page.get_by_label('本次项目工作区', exact=True)).to_have_count(0)
    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
        page.get_by_label('DSH 文件工作区', exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(Path(directory) / 'shared-workspace.png'), animations='disabled')
    section.get_by_text('导入原 Stella 身份配置', exact=True).click()
    for label in ['旧应用数据目录', '旧共享资源目录', '旧用户数据目录', '旧 Agent 状态目录']:
        page.get_by_label(label, exact=True).fill(str(legacy))
    page.get_by_role('button', name='只读预览', exact=True).click()
    page.get_by_text('预览完成，旧文件保持原位。', exact=True).wait_for()
    assert 'NEVER_IMPORT_SECRET' not in section.inner_text()
    for name in ['AGENT.md', 'USER.md', 'RULE.md']:
        expect(section.get_by_role('checkbox', name=name, exact=True)).to_be_checked()
    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
        page.get_by_role('button', name='导入所选身份配置', exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(Path(directory) / 'workspace-settings.png'), animations='disabled')
    page.get_by_role('button', name='导入所选身份配置', exact=True).click()
    page.get_by_text('身份配置已导入；下一次 Stella 请求使用此快照。', exact=True).wait_for()
    imported = rpc(page, url, 'stellaSecretary/workspaceState', {'request': {}})
    assert imported['identity']['files'][1]['content'] == 'WORKSPACE_USER_SENTINEL'
    # A stale settings tab cannot overwrite the saved configuration.
    response = page.request.post(url.split('/?')[0] + '/api/stellaSecretary/restoreIdentity', data={'type': 'client-request', 'rpcId': 'stale-workspace', 'method': 'stellaSecretary/restoreIdentity', 'payload': {'args': {'request': {'expectedRevision': state['revision']}}}})
    assert not response.json()['result']['ok']
    page.get_by_role('button', name='恢复导入前配置', exact=True).click()
    page.get_by_text('已恢复导入前的身份配置。', exact=True).wait_for()
    assert rpc(page, url, 'stellaSecretary/workspaceState', {'request': {}})['identity'] is None
    (legacy / 'USER.md').write_text('WORKSPACE_USER_CHANGED')
    page.get_by_role('button', name='导入所选身份配置', exact=True).click()
    page.get_by_text('来源配置已变化，请重新预览后导入。', exact=True).wait_for()
    assert rpc(page, url, 'stellaSecretary/workspaceState', {'request': {}})['identity'] is None
    (legacy / 'USER.md').write_text('WORKSPACE_USER_SENTINEL')
    page.get_by_role('button', name='只读预览', exact=True).click()
    page.get_by_text('预览完成，旧文件保持原位。', exact=True).wait_for()
    page.get_by_role('button', name='导入所选身份配置', exact=True).click()
    page.get_by_text('身份配置已导入；下一次 Stella 请求使用此快照。', exact=True).wait_for()
    if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
        page.get_by_role('button', name='Close', exact=True).click()
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(page.locator('[data-sidebar-opener]')).to_be_visible()
        page.locator('[data-sidebar-opener]').click()
        settings(page)
        expect(page.get_by_label('DSH 文件工作区', exact=True)).to_have_value(project_id)
        page.get_by_label('DSH 文件工作区', exact=True).scroll_into_view_if_needed()
        page.screenshot(path=str(Path(directory) / 'shared-workspace-mobile.png'), animations='disabled')
        section.get_by_text('导入原 Stella 身份配置', exact=True).click()
        page.get_by_role('button', name='只读预览', exact=True).click()
        page.get_by_text('预览完成，旧文件保持原位。', exact=True).wait_for()
        page.get_by_role('button', name='导入所选身份配置', exact=True).scroll_into_view_if_needed()
        expect(page.get_by_role('button', name='导入所选身份配置', exact=True)).to_be_in_viewport()
        assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
        page.screenshot(path=str(Path(directory) / 'workspace-settings-mobile.png'), animations='disabled')
        page.get_by_role('button', name='Close', exact=True).click()
        page.keyboard.press('Escape')
        page.set_viewport_size({'width': 1280, 'height': 850})
        expect(page.locator('[data-sidebar-drawer]')).to_have_count(0)
        settings(page)
    assert len(requests) == before, 'Workspace selection, preview and import must not call a model'
    page.get_by_role('button', name='Close', exact=True).click()
    open_secretary(page)
    start_secretary_chat(page)
    composer = page.locator('[data-composer-input][contenteditable="true"]')
    composer.fill('Check imported workspace identity')
    composer.press('Enter')
    page.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=25000)
    main = [request for request in requests[before:] if any(message.get('content') == 'Check imported workspace identity' for message in request.get('messages', []))]
    assert main and 'WORKSPACE_USER_SENTINEL' in json.dumps(main) and '{{literal}}' in json.dumps(main)
    assert 'NEVER_IMPORT_SECRET' not in json.dumps(main)
    current_session = page.evaluate('JSON.parse(localStorage.getItem("dsh.sessions.current")).sessionId')
    sessions = rpc(page, url, 'session/list', {'_request': {}})['items']
    assert next(item for item in sessions if item['sessionId'] == current_session)['cwd'] == str(project.resolve())
    if compatible:
        count = len(requests)
        if page.locator('[data-sidebar-collapsed]').count(): page.locator('[data-sidebar-toggle]:visible').click()
        region = page.get_by_role('region', name='工作区与会话', exact=True)
        expect(region).to_be_visible()
        page.get_by_role('button', name='View options', exact=True).click()
        page.get_by_role('menuitem', name='WorkSpace', exact=True).click()
        for title in ['project-files', 'personal-files']:
            page.locator('[data-row-key^="workspace:"]').filter(has=page.get_by_text(title, exact=True)).hover()
            expect(page.get_by_role('button', name=f'New session in {title}', exact=True)).to_be_visible()
        region.get_by_role('button', name='Search sessions', exact=True).click()
        search = region.get_by_placeholder('Search session names', exact=True)
        search.fill('Synthetic title')
        expect(region.get_by_text('Synthetic title', exact=True).first).to_be_visible()
        search.press('Escape')
        region.locator(f'[data-row-key="session:{current_session}"]').click()
        expect(composer).to_be_visible()
        assert page.evaluate('JSON.parse(localStorage.getItem("dsh.sessions.current")).sessionId') == current_session
        page.locator('.stella-sidebar-chat').click()
        page.wait_for_function('previous => JSON.parse(localStorage.getItem("dsh.sessions.current")).sessionId !== previous', arg=current_session)
        expect(composer).to_be_visible()
        new_session = page.evaluate('JSON.parse(localStorage.getItem("dsh.sessions.current")).sessionId')
        assert new_session != current_session
        new_row = next(item for item in rpc(page, url, 'session/list', {'_request': {}})['items'] if item['sessionId'] == new_session)
        assert new_row['cwd'] == str(project.resolve())
        assert len(requests) == count, 'Browsing and creating a blank conversation must not call a model'
        composer.fill('Verify Stella sidebar new conversation')
        composer.press('Enter')
        page.get_by_text('Stella synthetic tool round trip passed.', exact=True).wait_for(timeout=25000)
        count = len(requests)
        if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
            page.screenshot(path=str(Path(directory) / 'stella-sidebar-shared-workspaces.png'), animations='disabled')
        page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).click()
        page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
        expect(page.get_by_role('region', name='工作区与会话', exact=True)).to_have_count(0)
        page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).click()
        page.get_by_role('menuitem', name='Stella', exact=True).click()
        assert page.evaluate('JSON.parse(localStorage.getItem("dsh.sessions.current")).sessionId') == new_session
        assert len(requests) == count, 'Sharing workspaces and switching modes must not call a model'
    open_secretary(page)
    return imported

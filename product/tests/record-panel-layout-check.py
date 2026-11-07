"""Shared task/calendar panel geometry in the official Desktop with isolated data."""
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-record-panel-layout'))
    artifacts.mkdir(parents=True, exist_ok=True)
    desktop.ARTIFACTS = artifacts
    desktop.PACK = Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK', str(desktop.PACK)))
    desktop.verify_release()
    with tempfile.TemporaryDirectory(prefix='stella-panel-layout-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'panel-layout-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        desktop.native_cli(home, 'panel-install', 'add', str(desktop.PACK), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'panel-layout') as (page, errors):
            page.set_viewport_size({'width': 1440, 'height': 900})
            business = lambda domain, method, args=None: desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': domain, 'method': method, 'arguments': args or {}}})
            for index in range(18):
                business('tasks', 'create_todo', {'payload': {'title': f'面板事务 {index:02d}', 'notes': '这是用于验证面板滚动的说明。'}})
            today = page.evaluate('new Date().toLocaleDateString("sv-SE")')
            event = business('calendar', 'create_event', {'title': '面板日程', 'start_at': f'{today}T09:00:00+08:00', 'end_at': f'{today}T10:00:00+08:00', 'repeat_timezone': 'Asia/Shanghai'})
            open_secretary(page)
            page.get_by_role('button', name='刷新', exact=True).click()
            page.get_by_role('button', name='面板事务 00', exact=True).click()

            def check(name, action):
                panel = page.get_by_role('region', name=name, exact=True)
                expect(panel).to_be_visible()
                page.wait_for_function('''() => {const p=document.querySelector('.stella-calendar-record-panel'), d=document.querySelector('.stella-desk'); if (!p || !d) return false; const a=p.getBoundingClientRect(),b=d.getBoundingClientRect(),s=getComputedStyle(p),gap=s.position==='fixed'?8:12; return Math.abs(a.bottom-Math.min(innerHeight,b.bottom)+gap)<2 && a.top>=b.top && parseFloat(s.borderTopLeftRadius)>=12}''')
                expect(panel.get_by_role('button', name=action, exact=True)).to_be_in_viewport()
                assert panel.evaluate('e=>getComputedStyle(e).overflow') == 'hidden'
                return panel

            task = check('事务详情', '编辑详情')
            page.screenshot(path=str(artifacts / 'task-initial.png'), animations='disabled')
            desk = page.locator('.stella-desk')
            desk.evaluate('e=>e.scrollTop=250')
            page.wait_for_function("document.querySelector('.stella-desk').scrollTop>0")
            task = check('事务详情', '编辑详情')
            task.locator('.stella-inspector-body').evaluate('e=>e.scrollTop=e.scrollHeight')
            check('事务详情', '编辑详情')
            page.screenshot(path=str(artifacts / 'task-outer-inner-scroll.png'), animations='disabled')
            desk.evaluate('e=>e.scrollTop=e.scrollHeight')
            check('事务详情', '编辑详情')
            desk.evaluate('e=>e.scrollTop=250')
            check('事务详情', '编辑详情')
            task.get_by_role('button', name='标为已完成', exact=True).click()
            expect(page.locator('.stella-page-feedback')).to_contain_text('事务状态已更新')
            check('事务详情', '编辑详情')
            page.set_viewport_size({'width': 1280, 'height': 720})
            check('事务详情', '编辑详情')
            page.set_viewport_size({'width': 1440, 'height': 1100})
            check('事务详情', '编辑详情')
            task.get_by_role('button', name='编辑详情', exact=True).click()
            check('编辑事务', '保存事务')
            page.get_by_role('form', name='编辑事务').get_by_role('button', name='取消编辑').click()
            task = check('事务详情', '编辑详情')
            page.keyboard.press('Escape')
            expect(task).to_have_count(0)
            page.get_by_role('button', name='面板事务 00', exact=True).click()
            check('事务详情', '编辑详情')
            page.get_by_role('button', name='关闭详情', exact=True).click()
            page.get_by_role('button', name='新建事务', exact=True).click()
            check('新增事务', '保存事务')
            page.get_by_role('form', name='新增事务').get_by_role('button', name='取消编辑').click()
            page.get_by_role('button', name='面板事务 00', exact=True).click()
            page.set_viewport_size({'width': 390, 'height': 844})
            check('事务详情', '编辑详情')
            page.screenshot(path=str(artifacts / 'task-narrow.png'), animations='disabled')
            page.emulate_media(color_scheme='dark')
            page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            check('事务详情', '编辑详情')
            page.screenshot(path=str(artifacts / 'task-narrow-dark.png'), animations='disabled')
            page.emulate_media(color_scheme='light')
            page.set_viewport_size({'width': 1440, 'height': 900})
            page.get_by_role('button', name='关闭详情', exact=True).click()
            page.get_by_role('button', name='日程', exact=True).click()
            page.locator(f'.stella-calendar-chip[data-record-id="{event["id"]}"]').first.click()
            calendar = check('日程详情', '编辑详情')
            page.screenshot(path=str(artifacts / 'calendar-details.png'), animations='disabled')
            calendar.get_by_role('button', name='编辑详情', exact=True).click()
            check('编辑日程', '保存日程')
            page.get_by_role('form', name='编辑日程').get_by_role('button', name='取消编辑').click()
            page.get_by_role('button', name='新建', exact=True).click()
            page.get_by_role('menuitem', name='新建日程', exact=True).click()
            check('新增日程', '保存日程')
            page.get_by_role('form', name='新增日程').get_by_role('button', name='取消编辑').click()
            page.get_by_role('button', name='简报', exact=True).click()
            expect(page.locator('.stella-briefing-layout')).to_be_visible()
            page.screenshot(path=str(artifacts / 'briefing.png'), animations='disabled')
            assert not errors, errors
    print('PASS: task/calendar detail/create/edit panel geometry, outer/inner scroll, feedback, resize, reopen, keyboard, dark/narrow and briefing')


if __name__ == '__main__':
    main()

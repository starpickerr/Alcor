"""Unified calendar on the unmodified Desktop, using an isolated profile and synthetic EventKit."""
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import official_desktop_check as desktop
from web_check import open_secretary


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-unified-calendar'))
    artifacts.mkdir(parents=True, exist_ok=True)
    desktop.ARTIFACTS = artifacts
    desktop.PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(desktop.PACK)))
    desktop.verify_release()
    apple_version = json.loads((desktop.ROOT / 'packages/stella-apple-sync/package.json').read_text())['version']
    apple_pack = desktop.ROOT / f'.local/artifacts/stella-apple-sync-{apple_version}.tgz'
    with tempfile.TemporaryDirectory(prefix='stella-unified-calendar-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'unified-calendar-check', 'private': True,
            'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        desktop.native_cli(home, 'unified-install', 'add', str(desktop.PACK), str(apple_pack), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'unified-calendar') as (page, errors):
            business = lambda domain, method, args=None: desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': domain, 'method': method, 'arguments': args or {}}})
            apple = lambda method, args=None: desktop.rpc(page, 'stellaAppleSync/invoke', {'request': {'method': method, 'arguments': args or {}}})
            group = business('tasks', 'create_category', {'payload': {'name': 'planA', 'color': '#3B97F0'}})
            for kind in ['calendar', 'reminders']:
                result = apple('start_sync', {'kind': kind})
                assert result['result']['status'] == 'success', result
                apple('set_auto', {'kind': kind, 'enabled': False})
                state = apple('status', {'kind': kind})
                assert len(state['links']) == len(state['categories'])
            open_secretary(page)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.get_by_role('button', name='日程', exact=True).click()
            expect(page.get_by_role('button', name='导入日期', exact=True)).to_have_count(0)
            toolbar = page.get_by_role('navigation', name='日历筛选', exact=True)
            expect(toolbar.get_by_role('checkbox', name='事务', exact=True)).to_have_count(1)
            expect(toolbar.get_by_text('planA · 事务', exact=True)).to_have_count(0)
            expect(toolbar.get_by_role('button', name='未分类', exact=True)).to_have_count(0)
            page.get_by_role('button', name='分类管理', exact=True).click()
            manager = page.get_by_role('dialog', name='分类管理', exact=True)
            manager.get_by_role('button', name='新建分类', exact=True).click()
            manager.get_by_label('分类名称', exact=True).fill('Delete category and events')
            manager.get_by_label('分类颜色', exact=True).fill('#aabbcc')
            manager.get_by_role('button', name='保存分类', exact=True).click()
            target = next(row for row in business('calendar', 'categories') if row['name'] == 'Delete category and events')
            doomed = business('calendar', 'create_event', {'title':'Delete with category', 'category_id':target['id'], 'start_at':'2026-11-07T01:00:00Z', 'end_at':'2026-11-07T02:00:00Z', 'repeat_frequency':'daily'})
            apple('sync',{'kind':'calendar'})
            manager.get_by_role('button', name='删除分类：Delete category and events', exact=True).click()
            expect(manager).to_contain_text('将同时删除该分类中的全部日程')
            expect(manager).not_to_contain_text('归为未分类')
            manager.get_by_role('button', name='取消', exact=True).click()
            assert any(row['id'] == doomed['id'] for row in business('calendar', 'list_events'))
            page.screenshot(path=str(artifacts / 'categories-desktop.png'), animations='disabled')
            manager.get_by_role('button', name='删除分类：Delete category and events', exact=True).click()
            manager.get_by_role('button', name='确认删除分类', exact=True).click()
            assert any(row['id'] == doomed['id'] for row in business('calendar','list_events'))
            apple('sync',{'kind':'calendar'})
            assert not any(row['id'] == doomed['id'] for row in business('calendar','list_events'))
            assert not any(row['id'] == target['id'] for row in business('calendar', 'categories'))
            page.keyboard.press('Escape')
            page.get_by_role('button', name='新建', exact=True).click()
            page.get_by_role('menuitem', name='新建日程', exact=True).click()
            event = page.get_by_role('form', name='新增日程', exact=True)
            event.get_by_label('标题', exact=True).fill('Calendar appointment')
            # Switching types preserves the draft in each form.
            page.get_by_role('tab', name='事务', exact=True).click()
            task = page.get_by_role('form', name='新增事务', exact=True)
            task.get_by_label('标题', exact=True).fill('Calendar reminder')
            task.get_by_label('截止日期', exact=True).fill(page.evaluate('new Date().toLocaleDateString("en-CA")'))
            task.get_by_label('截止时间', exact=True).fill('04:00')
            task.get_by_label('事务清单', exact=True).select_option(group['id'])
            page.screenshot(path=str(artifacts / 'reminder-editor-desktop.png'), animations='disabled')
            page.get_by_role('tab', name='日程', exact=True).click()
            expect(event.get_by_label('标题', exact=True)).to_have_value('Calendar appointment')
            page.screenshot(path=str(artifacts / 'event-editor-desktop.png'), animations='disabled')
            event.get_by_role('button', name='保存日程', exact=True).click()
            page.get_by_role('button', name='新建', exact=True).click()
            page.get_by_role('menuitem', name='新建事务', exact=True).click()
            task = page.get_by_role('form', name='新增事务', exact=True)
            task.get_by_label('标题', exact=True).fill('Calendar reminder')
            task.get_by_label('截止日期', exact=True).fill(page.evaluate('new Date().toLocaleDateString("en-CA")'))
            task.get_by_label('截止时间', exact=True).fill('04:00')
            task.get_by_label('事务清单', exact=True).select_option(group['id'])
            task.get_by_role('button', name='保存事务', exact=True).click()
            toolbar.get_by_role('button', name='隐藏全部日历', exact=True).click()
            toolbar.get_by_role('checkbox', name='事务', exact=True).check()
            page.get_by_role('tab', name='周', exact=True).click()
            expect(page.locator('.stella-time-column .stella-calendar-open').filter(has_text='Calendar appointment')).to_have_count(0)
            card = page.locator('.stella-time-column .stella-calendar-task').filter(has_text='Calendar reminder')
            expect(card).to_be_visible()
            card.get_by_role('button', name='完成事务：Calendar reminder', exact=True).click()
            expect(card.get_by_role('button', name='重新打开：Calendar reminder', exact=True)).to_have_attribute('aria-pressed', 'true')
            records = business('tasks', 'list_todos')
            saved = next(row for row in records if row['title'] == 'Calendar reminder')
            assert saved['category_id'] == group['id'] and saved['status'] == 'completed'
            page.screenshot(path=str(artifacts / 'calendar-week-desktop.png'), animations='disabled')
            page.get_by_role('navigation', name='日历筛选', exact=True).get_by_role('button', name='显示全部日历', exact=True).click()
            page.get_by_role('tab', name='月', exact=True).click()
            expect(page.locator('.stella-calendar-chip').filter(has_text='Calendar reminder')).to_be_visible()
            page.get_by_role('tab', name='年', exact=True).click()
            expect(page.locator('.stella-year-grid .stella-calendar-dots').first).to_be_visible()
            page.get_by_role('tab', name='列表', exact=True).click()
            page.locator('.stella-calendar-surface').get_by_role('button', name='Calendar reminder', exact=False).filter(has=page.locator('h3')).click()
            page.get_by_role('region',name='事务详情',exact=True).get_by_role('button',name='编辑详情',exact=True).click()
            edit = page.get_by_role('form', name='编辑事务', exact=True)
            expect(edit.get_by_label('事务清单', exact=True)).to_have_value(group['id'])
            edit.get_by_label('截止时间', exact=True).fill('06:30')
            edit.get_by_role('button', name='保存事务', exact=True).click()
            page.get_by_role('tab', name='日', exact=True).click()
            expect(page.locator('.stella-time-column .stella-calendar-task').filter(has_text='Calendar reminder')).to_be_visible()
            page.get_by_role('tab', name='周', exact=True).click()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.keyboard.press('Escape')
            assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
            page.screenshot(path=str(artifacts / 'calendar-week-narrow.png'), animations='disabled')
            page.get_by_role('button', name='分类管理', exact=True).click()
            page.screenshot(path=str(artifacts / 'categories-narrow.png'), animations='disabled')
            page.keyboard.press('Escape')
            page.get_by_role('button', name='新建', exact=True).click()
            page.get_by_role('menuitem', name='新建事务', exact=True).click()
            expect(page.get_by_role('form', name='新增事务', exact=True)).to_be_visible()
            page.screenshot(path=str(artifacts / 'reminder-editor-narrow.png'), animations='disabled')
            page.get_by_role('button', name='取消编辑', exact=True).click()
            assert not errors, errors
        with desktop.desktop(playwright, home, 'unified-restart') as (page, errors):
            saved = desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'tasks', 'method': 'get_todo', 'arguments': {'todo_id': saved['id']}}})
            assert saved['category_id'] == group['id'] and saved['status'] == 'completed'
            assert not errors, errors
    print('PASS: official Desktop unified creation, category parity, task calendar completion, narrow layout and restart')


if __name__ == '__main__':
    main()

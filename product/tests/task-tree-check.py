"""Collapsible tasks in the official Desktop; isolated synthetic three-level hierarchy."""
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-task-tree'))
    artifacts.mkdir(parents=True, exist_ok=True)
    desktop.ARTIFACTS = artifacts
    desktop.PACK = Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK', str(desktop.PACK)))
    desktop.verify_release()
    with tempfile.TemporaryDirectory(prefix='stella-task-tree-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'task-tree-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        desktop.native_cli(home, 'tree-install', 'add', str(desktop.PACK), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'task-tree') as (page, errors):
            page.set_viewport_size({'width': 1440, 'height': 1100})
            business = lambda method, args=None: desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'tasks', 'method': method, 'arguments': args or {}}})
            today = page.evaluate('new Date().toLocaleDateString("sv-SE")')
            yesterday = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
            category = business('create_category', {'payload': {'name': '任务清单', 'color': '#597ac5'}})
            project = business('create_todo', {'payload': {'title': 'Real2Sim 十篇论文精读', 'category_id': category['id']}})
            day = business('add_subtask', {'parent_id': project['id'], 'payload': {'title': '周六读四篇：比较不同的世界重建方法', 'due_on': today}})
            papers = [business('add_subtask', {'parent_id': day['id'], 'payload': {'title': f'读 {name}', 'due_on': today, 'notes': '论文的完整说明只在详情中展示。'}}) for name in ['RoboSnap', 'WorldMirror', 'NeoWorld-3', 'CoDimRecon']]
            old = business('add_subtask', {'parent_id': project['id'], 'payload': {'title': '补齐前一天的阅读笔记', 'due_on': yesterday}})
            done = business('add_subtask', {'parent_id': day['id'], 'payload': {'title': '下载论文附件'}})
            business('complete_todo', {'todo_id': done['id'], 'completed': True})
            business('update_todo', {'todo_id': papers[1]['id'], 'payload': {'tags': ['精读']}})
            open_secretary(page)
            page.get_by_role('button', name='刷新', exact=True).click()
            workspace = page.locator('.stella-task-workspace')
            panel = page.get_by_role('region', name='事务详情', exact=True)
            card = lambda record: workspace.locator(f'[data-task-id="{record["id"]}"]')
            disclosure = lambda record: card(record).locator('.stella-task-disclosure button')
            expect(workspace.locator('.stella-task-card:visible')).to_have_count(1)
            expect(workspace.locator('.stella-task-group[data-group="overdue"] > h2')).to_have_text('含逾期事务 1 组')
            expect(card(project)).to_contain_text('1 项逾期')
            expect(card(project)).to_contain_text('5 项今天到期')
            expect(disclosure(project)).to_have_attribute('aria-expanded', 'false')
            disclosure(project).focus()
            page.keyboard.press('Enter')
            expect(disclosure(project)).to_have_attribute('aria-expanded', 'true')
            expect(card(day)).to_be_visible()
            expect(card(papers[0])).to_have_count(0)
            card(project).get_by_role('button', name='Real2Sim 十篇论文精读：5 项子事务今天到期', exact=True).click()
            expect(card(papers[0])).to_be_visible()
            expect(card(done)).not_to_be_visible()
            expect(workspace.get_by_text('论文的完整说明只在详情中展示。', exact=True)).to_have_count(0)
            workspace.locator('.stella-task-completed-children summary').click()
            expect(card(done)).to_be_visible()
            workspace.locator('.stella-task-completed-children summary').click()
            page.screenshot(path=str(artifacts / '01-tree-desktop.png'), animations='disabled')

            nav = workspace.get_by_role('navigation', name='事务清单', exact=True)
            nav.get_by_role('button', name='任务清单', exact=True).click()
            expect(workspace.locator('.stella-task-card:visible')).to_have_count(1)
            search = workspace.get_by_role('textbox', name='搜索事务', exact=True)
            search.fill('WorldMirror')
            expect(workspace.locator('.stella-task-card:visible')).to_have_count(3)
            expect(workspace.locator('[data-context-only]')).to_have_count(2)
            expect(workspace.locator('.stella-task-list-heading')).to_contain_text('1 项')
            expect(card(papers[0])).to_have_count(0)
            expect(card(project).locator('.stella-task-check')).to_have_count(0)
            page.screenshot(path=str(artifacts / '02-tree-search.png'), animations='disabled')
            card(papers[1]).get_by_role('button', name='读 WorldMirror', exact=True).click()
            expect(panel.locator('.stella-task-description')).to_contain_text('完整说明')
            expect(panel.get_by_label('子事务标题', exact=True)).to_have_count(0)
            expect(panel.get_by_role('heading', name='子事务', exact=False)).to_have_count(0)
            breadcrumbs = panel.get_by_role('navigation', name='事务路径')
            assert breadcrumbs.bounding_box()['height'] < 45
            panel.get_by_role('button', name='添加子事务', exact=True).click()
            expect(panel.get_by_label('子事务标题', exact=True)).to_be_focused()
            panel.get_by_label('子事务标题', exact=True).fill('记录论文结论')
            panel.get_by_role('button', name='添加子事务', exact=True).click()
            expect(panel.get_by_role('button', name='记录论文结论', exact=True)).to_be_visible()
            page.screenshot(path=str(artifacts / '03-tree-details.png'), animations='disabled')
            panel.get_by_role('button', name='关闭详情', exact=True).click()
            search.fill('')
            workspace.get_by_label('标签筛选', exact=True).select_option('精读')
            expect(workspace.locator('.stella-task-card:visible')).to_have_count(3)
            expect(workspace.locator('.stella-task-list-heading')).to_contain_text('1 项')
            workspace.get_by_label('标签筛选', exact=True).select_option('all')
            workspace.get_by_role('tab', name='逾期', exact=False).click()
            expect(card(old)).to_be_visible()
            expect(card(day)).to_have_count(0)
            expect(workspace.locator('.stella-task-list-heading')).to_contain_text('1 项')
            workspace.get_by_role('tab', name='已完成', exact=False).click()
            expect(card(done)).to_be_visible()
            expect(workspace.locator('.stella-task-list-heading')).to_contain_text('1 项')
            workspace.get_by_role('tab', name='全部事务', exact=False).click()
            disclosure(project).click()
            disclosure(day).click()
            card(papers[0]).get_by_role('button', name='完成事务', exact=True).click()
            expect(card(papers[0])).not_to_be_visible()
            assert business('get_todo', {'todo_id': papers[0]['id']})['status'] == 'completed'
            assert business('get_todo', {'todo_id': papers[1]['id']})['status'] != 'completed'
            workspace.locator('.stella-task-completed-children summary').click()
            card(papers[0]).get_by_role('button', name='重新打开', exact=True).click()
            assert business('get_todo', {'todo_id': papers[0]['id']})['status'] == 'todo'

            page.emulate_media(color_scheme='dark')
            page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            page.screenshot(path=str(artifacts / '04-tree-dark.png'), animations='disabled')
            page.emulate_media(color_scheme='light')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
            expect(card(papers[1]).get_by_role('button', name='读 WorldMirror', exact=True)).to_be_visible()
            page.screenshot(path=str(artifacts / '05-tree-narrow.png'), animations='disabled')
            card(papers[1]).get_by_role('button', name='读 WorldMirror', exact=True).click()
            expect(panel.get_by_role('button', name='编辑详情', exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts / '06-details-narrow.png'), animations='disabled')
            assert not errors, errors
    print('PASS: official Desktop collapsed hierarchy, urgency, category/search/tag/date filters, counts, completion, compact details, keyboard, dark and narrow')


if __name__ == '__main__':
    main()

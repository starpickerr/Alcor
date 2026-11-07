"""Task cards on an unmodified official Desktop, with isolated synthetic data."""
import json
import os
import tempfile
import runpy
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import official_desktop_check as desktop
from web_check import open_secretary

check_tasks = runpy.run_path(str(Path(__file__).with_name('organizer-ui-check.py')))['check_tasks']


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-task-cards'))
    artifacts.mkdir(parents=True, exist_ok=True)
    desktop.PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(desktop.PACK)))
    desktop.ARTIFACTS=artifacts
    desktop.verify_release()
    with tempfile.TemporaryDirectory(prefix='stella-task-cards-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'task-card-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        baseline = (profile / 'cordis.patch.yml').read_bytes()
        desktop.native_cli(home, 'task-cards-install', 'add', str(desktop.PACK), '--ignore-scripts')
        assert (profile / 'cordis.patch.yml').read_bytes() == baseline
        with desktop.desktop(playwright, home, 'task-cards') as (page, errors):
            business = lambda domain, method, args=None: desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': domain, 'method': method, 'arguments': args or {}}})
            check_tasks(page, business, artifacts)
            expect(page.locator('.stella-page-feedback').get_by_role('status')).to_have_text('事务状态已更新')
            vanished = business('tasks', 'create_todo', {'payload': {'title': 'Removed elsewhere'}})
            page.get_by_role('button', name='刷新', exact=True).click()
            page.get_by_role('button', name='更多操作：Removed elsewhere', exact=True).click()
            page.get_by_role('menuitem', name='删除事务', exact=True).click()
            business('tasks', 'delete_todo', {'todo_id': vanished['id']})
            dialog = page.get_by_role('dialog', name='删除事务', exact=True)
            dialog.get_by_role('button', name='确认删除', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('删除未完成')
            expect(dialog).to_be_visible()
            dialog.get_by_role('button', name='取消', exact=True).click()
            persistent = business('tasks', 'create_todo', {'payload': {'title': 'Persistent task', 'notes': 'Only task information remains.', 'due_on': '2026-12-25', 'repeat_frequency': 'daily'}})
            page.get_by_role('button', name='刷新', exact=True).click()
            page.get_by_role('button', name='更多操作：Persistent task', exact=True).click()
            page.get_by_role('menuitem', name='删除整个系列', exact=True).click()
            dialog = page.get_by_role('dialog', name='删除事务', exact=True)
            expect(dialog).to_contain_text('整个重复系列')
            dialog.get_by_role('button', name='取消', exact=True).click()
            page.emulate_media(color_scheme='dark')
            page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            page.screenshot(path=str(artifacts / 'tasks-list-dark.png'), animations='disabled')
            assert not errors, errors
        with desktop.desktop(playwright, home, 'task-cards-restart') as (page, errors):
            open_secretary(page)
            page.get_by_role('button', name='Persistent task', exact=True).click()
            expect(page.locator('.stella-task-description')).to_have_text('Only task information remains.')
            expect(page.get_by_role('heading', name='检查清单', exact=True)).to_have_count(0)
            saved = desktop.rpc(page, 'stellaOrganizer/read', {'request': {}})['records']
            assert any(row['id'] == persistent['id'] for row in saved)
            assert not any(row['title'] in ('Project parent', 'First step') for row in saved)
            assert not errors, errors
        assert (profile / 'cordis.patch.yml').read_bytes() == baseline
    print('PASS: official Desktop task lists, list navigation, keyboard/detail navigation, independent completion/delete, confirmation/cascade/restart, no checklist UI, legacy data preserved, light/dark and 390px')


if __name__ == '__main__':
    main()

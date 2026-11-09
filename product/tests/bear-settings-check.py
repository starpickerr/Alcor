"""Pet preferences and context menu in the signed official Desktop, isolated profile."""
import os
import re
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from fresh_profile_fixture import fresh_test_home, initialize_fresh
import official_desktop_check as desktop
from web_check import open_secretary


def settings(page):
    page.get_by_role('button', name=re.compile(r'^(Account menu|账号菜单|More|更多)$')).last.click()
    page.get_by_role('menuitem', name=re.compile(r'^(Settings|设置)')).click()
    dialog = page.get_by_role('dialog', name=re.compile(r'^(Settings|设置)$'))
    dialog.get_by_role('button', name='Alcor', exact=True).click()
    return dialog


def close(dialog):
    dialog.get_by_role('button', name=re.compile(r'^(Close|关闭)$')).click()


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/alcor-bear-settings'))
    artifacts.mkdir(parents=True, exist_ok=True)
    desktop.ARTIFACTS = artifacts
    desktop.verify_release()
    with fresh_test_home(prefix='alcor-bear-settings-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        initialize_fresh(temporary, home / 'dsh', profile)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'bear-settings-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        desktop.native_cli(home, 'pet-install', 'add', str(desktop.PACK), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'pet-settings') as (page, errors):
            page.set_viewport_size({'width': 1280, 'height': 960})
            open_secretary(page)
            bear = page.locator('.alcor-bear-handle')
            menu = page.locator('.alcor-bear-menu-list')
            chat = page.get_by_role('region', name='Alcor 小熊对话', exact=True)
            expect(bear).to_be_visible()
            original = bear.bounding_box()
            bear.click(button='right')
            expect(menu.get_by_role('menuitem')).to_have_count(2)
            expect(chat).to_have_count(0)
            page.screenshot(path=str(artifacts / '01-pet-menu.png'))
            page.keyboard.press('Escape')
            expect(menu).to_have_count(0)
            expect(bear).to_be_focused()
            assert bear.bounding_box() == original
            bear.press('Shift+F10')
            menu.get_by_role('menuitem', name='设置', exact=True).click()
            dialog = page.get_by_role('dialog', name='Alcor', exact=True)
            expect(dialog.get_by_role('heading', name='小熊宠物', exact=True)).to_be_visible()
            expect(dialog.get_by_role('switch', name='显示 Alcor 小熊')).to_have_attribute('aria-checked', 'true')
            name = dialog.get_by_label('小熊名称', exact=True)
            assert dialog.locator('.alcor-bear-preview').bounding_box()['y'] < name.bounding_box()['y']
            name.fill('团团')
            name.press('Enter')
            dialog.get_by_role('tab', name='大', exact=True).click()
            expect(dialog.locator('.alcor-bear-preview img')).to_have_attribute('alt', '团团')
            dialog.get_by_role('switch', name='显示 Alcor 小熊').click()
            expect(bear).to_have_count(0)
            expect(dialog).to_be_visible()
            dialog.get_by_role('switch', name='显示 Alcor 小熊').click()
            dialog.get_by_role('switch', name='悬停展开输入框').click()
            page.screenshot(path=str(artifacts / '02-pet-settings.png'))
            close(dialog)
            expect(bear).to_have_attribute('aria-label', '团团')
            assert bear.bounding_box()['width'] == 84
            bear.hover()
            page.wait_for_timeout(450)
            expect(chat).to_have_count(0)
            box = bear.bounding_box()
            page.mouse.move(box['x'] + 42, box['y'] + 42)
            page.mouse.down()
            page.mouse.move(180, 240, steps=6)
            page.mouse.up()
            moved = bear.bounding_box()
            assert abs(moved['x'] - 138) < 2 and abs(moved['y'] - 198) < 2, moved
            expect(chat).to_have_count(0)
            bear.click(button='right')
            menu.get_by_role('menuitem', name='隐藏', exact=True).click()
            expect(bear).to_have_count(0)
            assert not errors, errors
        with desktop.desktop(playwright, home, 'pet-restarted') as (page, errors):
            page.set_viewport_size({'width': 1280, 'height': 960})
            open_secretary(page)
            bear = page.locator('.alcor-bear-handle')
            expect(bear).to_have_count(0)
            dialog = settings(page)
            expect(dialog.get_by_label('小熊名称', exact=True)).to_have_value('团团')
            expect(dialog.get_by_role('tab', name='大', exact=True)).to_have_attribute('aria-selected', 'true')
            expect(dialog.get_by_role('switch', name='悬停展开输入框')).to_have_attribute('aria-checked', 'false')
            dialog.get_by_role('switch', name='显示 Alcor 小熊').click()
            close(dialog)
            expect(bear).to_be_visible()
            assert bear.bounding_box()['width'] == 84
            assert abs(bear.bounding_box()['x'] - moved['x']) < 2
            bear.click(button='right')
            page.get_by_role('menuitem', name='设置', exact=True).click()
            dialog = page.get_by_role('dialog', name='Alcor', exact=True)
            dialog.get_by_role('button', name='恢复默认位置', exact=True).click()
            expect(dialog.get_by_role('button', name='恢复默认位置', exact=True)).to_be_disabled()
            dialog.get_by_role('tab', name='小', exact=True).click()
            page.emulate_media(color_scheme='dark')
            page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            page.screenshot(path=str(artifacts / '03-pet-settings-dark.png'))
            page.emulate_media(color_scheme='light')
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(dialog.get_by_role('switch', name='显示 Alcor 小熊')).to_be_in_viewport()
            assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
            page.screenshot(path=str(artifacts / '04-pet-settings-narrow.png'))
            close(dialog)
            assert bear.bounding_box()['width'] == 48
            bear.click(button='right')
            expect(page.get_by_role('menuitem', name='隐藏', exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts / '05-pet-menu-narrow.png'))
            page.keyboard.press('Escape')
            bear.click()
            expect(page.get_by_label('给 Alcor 发消息', exact=True)).to_be_in_viewport()
            assert not errors, errors
    print('PASS: official Desktop pet preview, name, sizes, context/keyboard menu, direct settings, hide/restore, drag, restart, dark and narrow')


if __name__ == '__main__':
    main()

"""Verify plugin text and decoded artwork in the unchanged official Desktop."""
import json
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from fresh_profile_fixture import fresh_test_home, initialize_fresh
import official_desktop_check as desktop
from web_check import open_secretary


def main():
    packs = [str(Path(path).resolve()) for path in json.loads(os.environ['ALCOR_METADATA_PACKS'])]
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    expected = {'@alcor/secretary': 'Alcor 私人秘书：在对话中处理事务、日程、简报和账单。',
                '@alcor/apple-sync': 'Apple 同步：连接系统日历与提醒事项，共用 Alcor 事务和日程。',
                '@alcor/agent-hub': 'AgentHub — DSH 工作台中的独立开发者协作工具',
                '@alcor/elearning': '复旦 eLearning连接，使用 DSH 原生凭据',
                '@alcor/mail': '邮件连接，使用 DSH 原生凭据'}
    with fresh_test_home(prefix='alcor-plugin-metadata-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        env = desktop.environment(home)
        profile = Path(env['DSH_HOME']) / 'profiles/desktop'
        profile.mkdir(parents=True)
        initialize_fresh(temporary, Path(env['DSH_HOME']), profile)
        (profile / 'package.json').write_text(json.dumps({'name': 'plugin-metadata-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        desktop.native_cli(home, 'metadata-install', 'add', *packs, '--ignore-scripts')
        with desktop.desktop(playwright, home, 'plugin-metadata') as (page, errors):
            open_secretary(page)
            page.locator('.alcor-sidebar, .alcor-sidebar-actions').get_by_role('button', name='插件', exact=True).click()
            page.get_by_role('heading', name='插件', exact=True).wait_for()
            for package, description in expected.items():
                row = page.locator(f'[data-plugin-package="{package}"]')
                expect(row).to_have_count(1)
                expect(row.get_by_text(description, exact=True)).to_be_visible()
                image = row.locator('img')
                expect(image).to_have_count(1)
                assert image.evaluate('e => e.complete && e.naturalWidth > 0 && e.src.startsWith("data:image/svg+xml;base64,")')
            page.get_by_role('heading', name='已安装插件', exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(desktop.ARTIFACTS / 'plugin-metadata-desktop.png'))
            page.set_viewport_size({'width': 430, 'height': 900})
            expect(page.locator('.alcor-sidebar, .alcor-sidebar-actions').first).not_to_be_in_viewport(timeout=5000)
            page.locator('[data-plugin-package="@alcor/secretary"]').scroll_into_view_if_needed()
            expect(page.locator('[data-plugin-package="@alcor/apple-sync"] img')).to_be_in_viewport()
            page.get_by_role('heading', name='已安装插件', exact=True).scroll_into_view_if_needed()
            assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
            page.screenshot(path=str(desktop.ARTIFACTS / 'plugin-metadata-narrow.png'), animations='disabled')
            assert not errors, errors
    print('PASS: all five Alcor plugin descriptions and SVG icons render in official Desktop; narrow layout has no overflow')


if __name__ == '__main__':
    main()

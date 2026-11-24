"""Check the task and calendar toolbars in an isolated official DSH Desktop."""
import os
import runpy
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

import official_desktop_check as desktop
from web_check import open_secretary


def main():
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    desktop.verify_release()
    configure = runpy.run_path(str(Path(__file__).with_name('shared-business-chat-check.py')))['configure']
    with tempfile.TemporaryDirectory(prefix='alcor-organizer-toolbar-') as directory, sync_playwright() as playwright:
        home = Path(directory)
        configure(home, desktop.environment(home), 9)
        pack = Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK', desktop.PACK))
        desktop.native_cli(home, 'toolbar-install', 'add', str(pack), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'organizer-toolbar') as (page, errors):
            open_secretary(page)
            for view, manage in [('事务', '清单管理'), ('日程', '分类管理')]:
                page.set_viewport_size({'width': 1280, 'height': 850})
                page.get_by_role('group', name='应用', exact=True).get_by_role('button', name=view, exact=True).click()
                header = page.locator('.alcor-app-header').filter(has=page.get_by_role('heading', name=view, exact=True))
                for width in (1280, 540):
                    page.set_viewport_size({'width': width, 'height': 850})
                    expect(header.get_by_role('button', name=manage, exact=True)).to_be_in_viewport()
                    expect(page.get_by_role('button', name='恢复同步移除记录', exact=True)).to_have_count(0)
                    header.screenshot(path=str(desktop.ARTIFACTS / f'{view}-{width}.png'), animations='disabled')
            assert not errors, errors
    print('PASS: task and calendar toolbars at desktop and narrow widths')


if __name__ == '__main__':
    main()

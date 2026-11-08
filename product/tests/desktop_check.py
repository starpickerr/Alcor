"""Exercise the actual Electron renderer with an isolated, synthetic-data-only home."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]


def main():
    with tempfile.TemporaryDirectory(prefix='stella-desktop-check-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        (home / '.stella-product.json').write_text('{"version":1,"kind":"desktop"}\n')
        log_path = home / 'desktop.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(['node', '.local/upstream/node_modules/tsx/dist/cli.mjs', 'scripts/product/desktop.mjs'], cwd=ROOT,
                env={**os.environ, 'ALCOR_PRODUCT_HOME': str(home)}, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 60
                while True:
                    match = re.search(r'DevTools listening on ws://127.0.0.1:(\d+)/', log_path.read_text())
                    if match:
                        break
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError('Desktop did not become ready (see isolated log)')
                    time.sleep(0.1)
                browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:' + match.group(1))
                context = browser.contexts[0]
                page = context.wait_for_event('page') if not context.pages else context.pages[0]
                deadline = time.monotonic() + 30
                while not any(p.url.startswith('dsh-app://app/') for p in context.pages):
                    assert time.monotonic() < deadline, 'Application renderer did not open'
                    page.wait_for_timeout(100)
                page = next(p for p in context.pages if p.url.startswith('dsh-app://app/'))
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                stella = page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True)
                dsh = page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True)
                dsh.wait_for()
                if page.get_by_role('button', name='Continue', exact=True).count():
                    page.get_by_role('button', name='Continue', exact=True).click()
                dsh.click()
                page.get_by_role('menuitem', name='Alcor', exact=True).click()
                page.get_by_role('button', name='事务', exact=True).click()
                page.get_by_role('heading', name='事务', exact=True).wait_for()
                stella.click()
                page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                page.locator('[data-composer-input]').wait_for()
                page.reload()
                dsh = page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True)
                dsh.wait_for()
                dsh.click()
                page.get_by_role('menuitem', name='Alcor', exact=True).click()
                page.get_by_role('heading', name='事务', exact=True).wait_for()
                # Upstream's native minimum width is 520px; test the renderer at that boundary.
                page.set_viewport_size({'width': 520, 'height': 844})
                page.wait_for_function('window.innerWidth === 520')
                opener = page.locator('[data-sidebar-opener]')
                if opener.is_visible():
                    opener.click()
                stella.click()
                page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
                page.locator('[data-composer-input]').wait_for()
                assert not errors, errors
                if directory := os.environ.get('STELLA_CHECK_ARTIFACTS'):
                    Path(directory).mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(Path(directory) / 'native-dual-mode-narrow.png'))
                browser.close()
                print('PASS: Electron Stella/DSH switch, reload preference, 520px renderer drawer; no account or model')
            finally:
                process.terminate()
                process.wait(timeout=15)


if __name__ == '__main__':
    main()

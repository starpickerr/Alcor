"""Verify a downloaded, unchanged official Desktop with an isolated native plugin profile."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import plistlib
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
APP = Path(os.environ.get('STELLA_CHECK_DESKTOP_APP', str(ROOT / '.local/desktop-official/mount/DeepSeek Harness.app')))
ARTIFACTS = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', str(ROOT / '.local/desktop-official/check-artifacts')))
VERSION = json.loads((ROOT / 'packages/stella-secretary/package.json').read_text())['version']
PACK = ROOT / f'.local/artifacts/stella-secretary-{VERSION}.tgz'
RELEASE = json.loads((ROOT / 'product/desktop-release.lock.json').read_text())


def redact(value):
    value = re.sub(r'(?i)(token|api[_-]?key|authorization)([=: ]+)[^\s\"\'&,]+', r'\1\2[redacted]', value)
    return re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[redacted]', value)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_release():
    metadata = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
    global RELEASE
    candidates = [RELEASE, *RELEASE.get('compatibleReleases', [])]
    RELEASE = next((item for item in candidates if item['version'] == metadata['CFBundleShortVersionString']), None)
    assert RELEASE is not None, 'Unsupported official Desktop version'
    assert metadata['CFBundleIdentifier'] == RELEASE['bundleId'], 'Unexpected Desktop bundle identity'
    architectures = subprocess.check_output(['lipo', '-archs', str(APP / 'Contents/MacOS/DeepSeek Harness')], text=True).split()
    assert RELEASE['arch'] in architectures, 'Unexpected Desktop architecture'
    assert digest(APP / 'Contents/Resources/app.asar') == RELEASE['appAsarSha256'], 'Official Desktop archive does not match the pinned release'
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(APP)], check=True, capture_output=True)
    signature = subprocess.run(['codesign', '--display', '--verbose=4', str(APP)], check=True, capture_output=True, text=True)
    assert 'TeamIdentifier=' + RELEASE['teamId'] in signature.stderr, 'Unexpected official Desktop signer'


def rpc(page, endpoint, arguments=None):
    result = page.evaluate('''async ({endpoint, arguments: args, id}) => {
      const response = await fetch('/api/' + endpoint, {method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({type: 'client-request', rpcId: id, method: endpoint, payload: {args}})})
      if (!response.ok) throw new Error('Native RPC HTTP ' + response.status)
      return (await response.json()).result
    }''', {'endpoint': endpoint, 'arguments': arguments or {}, 'id': str(uuid4())})
    assert result.get('ok'), redact(json.dumps(result, ensure_ascii=False))
    return result.get('value')


def enter_workspace(context, process, timeout=45):
    deadline = time.monotonic() + timeout
    skipped = set()
    while time.monotonic() < deadline:
        assert process.poll() is None, 'Official Desktop exited before opening its workspace'
        for page in context.pages:
            if page.is_closed() or page in skipped:
                continue
            try:
                plugins = page.get_by_role('button', name=re.compile(r'^(Plugins|插件)$'))
                if plugins.count():
                    plugins.wait_for()
                    return page
                key = page.get_by_role('button', name=re.compile(r'^(Add API Key|添加 API Key)$'))
                skip = page.get_by_role('button', name=re.compile(r'^(Set up later|稍后配置)$'))
                if skip.is_visible():
                    skipped.add(page)
                    skip.click()
                elif key.is_visible():
                    key.click()
            except PlaywrightError as error:
                if not page.is_closed() and 'Execution context was destroyed' not in str(error):
                    raise
        try:
            context.wait_for_event('page', timeout=100)
        except PlaywrightTimeout:
            pass
    raise AssertionError('Official Desktop did not open its native workspace without credentials')


def environment(home):
    result = {name: value for name, value in os.environ.items()
                   if name in ('PATH', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL', 'https_proxy', 'http_proxy',
                               'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY', 'all_proxy', 'NO_PROXY', 'no_proxy', 'SSL_CERT_FILE')}
    result.update(HOME=str(home / 'home'), ZDOTDIR=str(home / 'home'), DSH_HOME=str(home / 'dsh'),
                       SHELL='/bin/zsh', DSH_TELEMETRY_DISABLED='1',
                       STELLA_EVENTKIT_HELPER=str(ROOT / 'product/tests/fake-eventkit.py'))
    (home / 'home').mkdir(exist_ok=True)
    return result


def native_cli(home, phase, *arguments):
    print('Official Desktop CLI phase:', phase, flush=True)
    cli = APP / 'Contents/Resources/runtime/cli/bin/dsh'
    result = subprocess.run([str(cli), 'plugin', '--profile', 'desktop', *arguments], cwd=home,
                            env=environment(home), capture_output=True, text=True, timeout=180)
    (ARTIFACTS / f'{phase}.log').write_text(redact(result.stdout + result.stderr))
    assert result.returncode == 0, redact(result.stdout + result.stderr)[-4000:]


def select_bundle(home, enabled):
    # The official CLI forwards pnpm commands; its public app-boot API owns cold bundle selection.
    module = APP / 'Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-app-boot/lib/index.js'
    profile = home / 'dsh/profiles/desktop'
    script = f"""import {{readProfileManifest, writeProfileBundles}} from {json.dumps(module.as_uri())};
      const directory={json.dumps(str(profile))};
      const manifest=readProfileManifest('dsh', directory);
      const name='@stella/secretary';
      const previous=manifest.dsh.profile.bundles;
      const bundles={str(enabled).lower()} ? [...new Set([...previous,name])] : previous.filter(item => item!==name);
      writeProfileBundles(directory, manifest, bundles);
    """
    native_cli(home, 'enable' if enabled else 'disable', 'exec', 'node', '--input-type=module', '-e', script)
    selected = json.loads((profile / 'package.json').read_text())['dsh']['profile']['bundles']
    assert ('@stella/secretary' in selected) is enabled


@contextmanager
def desktop(playwright, home, phase):
    print('Official Desktop phase:', phase, flush=True)
    output = home / f'{phase}.log'
    errors = []
    diagnostics = []
    browser = None
    (home / 'electron/DevToolsActivePort').unlink(missing_ok=True)
    with output.open('w') as stream:
        process = subprocess.Popen([str(APP / 'Contents/MacOS/DeepSeek Harness'),
                                    '--user-data-dir=' + str(home / 'electron'), '--remote-debugging-port=0'],
                                   cwd=home, env=environment(home), stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                match = re.search(r'DevTools listening on ws://127.0.0.1:(\d+)/', output.read_text())
                active_port = home / 'electron/DevToolsActivePort'
                if match or active_port.is_file():
                    port = match[1] if match else active_port.read_text().splitlines()[0]
                    break
                assert process.poll() is None, 'Official Desktop exited before CDP was ready'
                time.sleep(.1)
            else:
                raise AssertionError('Official Desktop CDP did not become ready')
            browser = playwright.chromium.connect_over_cdp('http://127.0.0.1:' + port)
            context = browser.contexts[0]
            def observe(page):
                page.on('pageerror', lambda error: errors.append(redact(str(error))))
                page.on('console', lambda message: diagnostics.append(redact(message.text)) if message.type in ('error', 'warning') else None)
            context.on('page', observe)
            for page in context.pages:
                observe(page)
            page = enter_workspace(context, process)
            page.add_locator_handler(page.get_by_role('button', name='Continue', exact=True), lambda button: button.click())
            print('Native renderer ready:', phase, flush=True)
            yield page, errors
            if not page.is_closed():
                page.screenshot(path=str(ARTIFACTS / f'official-{phase}.png'))
        except Exception:
            (ARTIFACTS / f'{phase}-console.txt').write_text('\n'.join(errors + diagnostics))
            if browser:
                for index, page in enumerate(browser.contexts[0].pages):
                    if not page.is_closed():
                        try:
                            page.screenshot(path=str(ARTIFACTS / f'{phase}-{index}-failure.png'), timeout=5000)
                            (ARTIFACTS / f'{phase}-{index}-failure.txt').write_text(redact(page.locator('body').inner_text(timeout=5000)))
                        except PlaywrightError:
                            pass  # A crashed renderer cannot supply a screenshot; preserve the startup failure.
            print(redact(output.read_text())[-4000:])
            raise
        finally:
            if browser and browser.is_connected():
                try:
                    browser.close()
                except PlaywrightError:
                    pass  # Process-group cleanup still owns a disconnected Electron process.
            # The official Host and plugin workers share this test-owned process group.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(.05)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            (ARTIFACTS / f'{phase}.log').write_text(redact(output.read_text()))


def install(page):
    page.get_by_role('button', name=re.compile(r'^(Plugins|插件)$')).click()
    page.get_by_role('heading', name=re.compile(r'^(Plugins|插件)$')).wait_for()
    result = rpc(page, 'pluginManager/installBundle', {'spec': str(PACK), 'options': {'saveExact': True}})
    assert result['application'] in ('applied', 'restart-required'), result
    print('PASS: official Desktop plugin manager installed the local Stella bundle:', result['application'])


def check_settings(page):
    page.get_by_role('button', name=re.compile(r'^(Account menu|账号菜单|More|更多)$')).last.click()
    page.get_by_role('menuitem', name=re.compile(r'^(Settings|设置)')).click()
    page.get_by_role('dialog', name=re.compile(r'^(Settings|设置)$')).get_by_role('button', name='Stella', exact=True).click()
    page.get_by_label('助手名称', exact=True).wait_for()
    page.get_by_role('button', name=re.compile(r'^(Close|关闭)$')).click()


def check_mode_menu_region(page):
    menu = page.locator('.stella-mode-menu')
    menu.wait_for()
    assert menu.evaluate('''menu => {
      if (document.querySelector('#root')?.contains(menu)) return false;
      if (getComputedStyle(menu).webkitAppRegion !== 'no-drag') return false;
      const box = menu.getBoundingClientRect();
      return [...document.querySelectorAll('[data-window-drag], .stella-mode-bar')].every(row => {
        const rect = row.getBoundingClientRect();
        const overlaps = rect.left < box.right && rect.right > box.left && rect.top < box.bottom && rect.bottom > box.top;
        return !overlaps || !!(row.compareDocumentPosition(menu) & Node.DOCUMENT_POSITION_FOLLOWING);
      });
    }'''), 'Mode menu must exclude native window dragging after all overlapping chrome rows'


def check_modes(page):
    mode = page.get_by_role('button', name=re.compile(r'^切换使用模式，当前 '))
    mode.wait_for()
    if mode.get_attribute('aria-label').endswith('Stella'):
        mode.click()
        page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
    # Give both modes a distinct location and verify the same renderer restores each.
    page.get_by_role('button', name=re.compile(r'^(Plugins|插件)$')).click()
    page.get_by_role('heading', name=re.compile(r'^(Plugins|插件)$')).wait_for()
    mode.click()
    check_mode_menu_region(page)
    expect(page.get_by_role('menuitem', name='DeepSeek Harness', exact=True)).to_have_attribute('aria-current', 'page')
    page.get_by_role('menuitem', name='Stella', exact=True).click()
    expect(mode).to_have_attribute('aria-label', '切换使用模式，当前 Stella')
    sidebar = page.get_by_role('complementary', name='Stella 秘书导航', exact=True)
    assert sidebar.evaluate('''sidebar => {
      const style = getComputedStyle(sidebar);
      return style.backgroundColor === 'rgba(0, 0, 0, 0)' && style.backgroundImage === 'none';
    }'''), 'Stella must not cover the native macOS sidebar gradient and vibrancy'
    for title in ('Agent 协作', '事务', '日程', '简报', '账单'):
        sidebar.get_by_role('button', name=title, exact=True).click()
        page.get_by_role('heading', name='尚无协作任务' if title == 'Agent 协作' else title, exact=True).wait_for()
        page.screenshot(path=str(ARTIFACTS / f'official-stella-{title}.png'))
    sidebar.get_by_role('button', name='自动化', exact=True).click()
    expect(page.get_by_text('当前 DSH 版本尚未提供共用自动化页面接口。', exact=False)).to_have_count(0)
    page.get_by_role('heading', level=1, name=re.compile(r'自动化')).wait_for()
    sidebar.get_by_role('button', name='账单', exact=True).click()
    mode.click()
    expect(page.get_by_role('menuitem', name='Stella', exact=True)).to_have_attribute('aria-current', 'page')
    page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
    expect(mode).to_have_attribute('aria-label', '切换使用模式，当前 DeepSeek Harness')
    page.get_by_role('heading', name=re.compile(r'^(Plugins|插件)$')).wait_for()
    mode.click()
    page.get_by_role('menuitem', name='Stella', exact=True).click()
    page.get_by_role('heading', name='账单', exact=True).wait_for()
    sidebar.get_by_role('button', name='事务', exact=True).click()
    page.get_by_role('heading', name='事务', exact=True).wait_for()
    assert len([item for item in page.context.pages if item.url.startswith('dsh-app://app/')]) == 1, 'Mode switches must reuse the same official Desktop renderer'
    assert page.locator('.stella-app-header > div > p').count() == 0, 'Page titles must not carry introductory subtitles'
    business = lambda method, args: rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': method, 'arguments': args}})
    entry = business('start_briefing', {'briefing_date': '2026-10-01', 'title': 'Synthetic Markdown'})
    business('complete_briefing', {'briefing_id': entry['id'], 'payload': {'body': '## Preview heading\n\n- **Preview text**', 'items': []}})
    sidebar.get_by_role('button', name='简报', exact=True).click()
    page.locator('.stella-briefing-history > button').filter(has_text='Synthetic Markdown').click()
    expect(page.locator('.stella-report h2')).to_have_text('Preview heading')
    expect(page.locator('.stella-report strong')).to_have_text('Preview text')
    page.screenshot(path=str(ARTIFACTS / 'official-markdown-preview.png'))
    sidebar.get_by_role('button', name='事务', exact=True).click()
    for width in (520, 1440):
        page.set_viewport_size({'width': width, 'height': 900})
        # Let the native sidebar finish its drawer/collapse transition before capturing it.
        page.wait_for_timeout(400)
        mode.focus()
        mode.press('ArrowDown')
        check_mode_menu_region(page)
        page.screenshot(path=str(ARTIFACTS / f'official-mode-menu-{width}.png'))
        page.get_by_role('menuitem', name='Stella', exact=True).press('ArrowDown')
        expect(page.get_by_role('menuitem', name='DeepSeek Harness', exact=True)).to_be_focused()
        page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).press('Escape')
        expect(mode).to_be_focused()
        expect(page.locator('.stella-mode-menu')).to_have_count(0)
    page.emulate_media(color_scheme='dark')
    expect(page.locator('body')).to_have_attribute('data-ds-dark-theme', '')
    mode.click()
    page.screenshot(path=str(ARTIFACTS / 'official-mode-menu-dark.png'))
    mode.press('Escape')
    page.emulate_media(color_scheme='light')
    expect(page.locator('body')).not_to_have_attribute('data-ds-dark-theme', '')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--baseline', action='store_true', help='Only launch the unmodified Desktop without installing Stella')
    group.add_argument('--install-only', action='store_true', help='Verify cold native installation and settings only')
    group.add_argument('--hot-install', action='store_true', help='Exercise live native UI installation; any renderer error fails this separate check')
    group.add_argument('--mode-only', action='store_true', help='Check the installed mode menu and native drag-region ordering without the full lifecycle')
    args = parser.parse_args()
    assert APP.is_dir(), 'Download and mount the official supported Desktop first'
    assert args.baseline or PACK.is_file(), 'Build the native Stella tarball first'
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    archive = APP / 'Contents/Resources/app.asar'
    verify_release()
    original = digest(archive)
    try:
        with tempfile.TemporaryDirectory(prefix='stella-official-desktop-') as temporary, sync_playwright() as playwright:
            home = Path(temporary)
            with desktop(playwright, home, 'hot-install' if args.hot_install else 'baseline') as (page, errors):
                if args.hot_install:
                    install(page)
                installation_errors = list(errors)
                if not args.hot_install:
                    assert not errors, errors
            profile = home / 'dsh/profiles/desktop'
            patch = profile / 'cordis.patch.yml'
            owner_patch = patch.read_bytes() if patch.is_file() else None
            original_bundles = json.loads((profile / 'package.json').read_text())['dsh']['profile']['bundles']
            if not args.baseline:
                if not args.hot_install:
                    native_cli(home, 'install', 'add', str(PACK), '--ignore-scripts')
                with desktop(playwright, home, 'installed-restart') as (page, errors):
                    check_settings(page)
                    if not args.install_only:
                        check_modes(page)
                    page.screenshot(path=str(ARTIFACTS / 'official-stella-installed.png'))
                    print('PASS: installed Stella settings and mode checks', flush=True)
                    if not args.install_only:
                        task = rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'tasks', 'method': 'create_todo', 'arguments': {'payload': {'title': 'Synthetic official Desktop persistence'}}}})
                        workers = [line for line in subprocess.check_output(['ps', '-Ao', 'args='], text=True).splitlines()
                                   if str(home / 'dsh') in line and '/business/worker.py' in line]
                        assert workers and all('/primary-runtime/dependencies/python/bin/python' in line for line in workers), 'Business worker must use the official Desktop Python payload'
                    assert not errors, errors
                if not args.install_only and not args.mode_only:
                    with desktop(playwright, home, 'persisted-restart') as (page, errors):
                        page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).wait_for()
                        page.get_by_role('heading', name='事务', exact=True).wait_for()
                        assert not errors, errors
                    select_bundle(home, False)
                    with desktop(playwright, home, 'disabled-restart') as (page, errors):
                        expect(page.get_by_role('button', name=re.compile(r'^切换使用模式，当前 '))).to_have_count(0)
                        page.locator('[data-composer-input]').wait_for()
                        assert not errors, errors
                    select_bundle(home, True)
                    with desktop(playwright, home, 'enabled-restart') as (page, errors):
                        page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).wait_for()
                        page.get_by_role('heading', name='事务', exact=True).wait_for()
                        rows = rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'tasks', 'method': 'list_todos'}})
                        assert any(row['id'] == task['id'] for row in rows)
                        assert not errors, errors
                    native_cli(home, 'uninstall', 'remove', '@stella/secretary')
                    with desktop(playwright, home, 'uninstalled-restart') as (page, errors):
                        expect(page.get_by_role('button', name=re.compile(r'^切换使用模式，当前 '))).to_have_count(0)
                        page.locator('[data-composer-input]').wait_for()
                        assert (profile / 'stella/business/tasks.sqlite3').is_file()
                        assert (patch.read_bytes() if patch.is_file() else None) == owner_patch, 'Owner profile configuration changed'
                        assert json.loads((profile / 'package.json').read_text())['dsh']['profile']['bundles'] == original_bundles, 'Original DSH bundle selection was not restored'
                        page.screenshot(path=str(ARTIFACTS / 'official-dsh-restored.png'))
                        assert not errors, errors
                    native_cli(home, 'reinstall', 'add', str(PACK), '--ignore-scripts')
                    with desktop(playwright, home, 'reinstalled-restart') as (page, errors):
                        check_settings(page)
                        check_modes(page)
                        rows = rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'tasks', 'method': 'list_todos'}})
                        assert sum(row['id'] == task['id'] for row in rows) == 1, 'Reinstallation must preserve existing business records without duplication'
                        assert (patch.read_bytes() if patch.is_file() else None) == owner_patch
                        assert not errors, errors
                assert not installation_errors, installation_errors
            print('PASS: downloaded official Desktop, isolated native profile, onboarding without credentials' +
                  ('' if args.baseline else ', native Stella install and restart/settings' + (', mode menu and six apps' if args.mode_only else '' if args.install_only else ', six apps, official Python, persisted modes/data, disable/enable/uninstall/reinstall recovery')))
    finally:
        verify_release()
        assert digest(archive) == original, 'Official app.asar changed during plugin installation'
        print('PASS: official app.asar SHA-256 unchanged:', original)


if __name__ == '__main__':
    main()

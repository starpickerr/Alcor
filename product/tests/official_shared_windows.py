"""Exercise two modes, one native Windows desktop Profile and the upstream AuraBot page."""
import argparse
import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from official_desktop_check import ROOT, digest, enter_workspace, redact, rpc
from playwright.sync_api import Error, expect, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from windows_caption_check import check_caption


def verify(app, *releases):
    binary_hash = digest(app).upper()
    release = next((item for item in releases if binary_hash == item['binarySha256']), None)
    assert release is not None, 'Unknown official executable'
    assert digest(app.parent / 'resources/app.asar').upper() == release['appAsarSha256']
    system = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0'
    command = ("$ErrorActionPreference='Stop';$s=Get-AuthenticodeSignature -LiteralPath $env:TEST_DSH_EXE;"
               "if ($s.Status -ne 'Valid') {throw $s.Status};$s.SignerCertificate.Thumbprint")
    thumbprint = subprocess.check_output([system / 'powershell.exe', '-NoProfile', '-NonInteractive',
                                         '-Command', command], text=True,
                                        env={**os.environ, 'PSModulePath': str(system / 'Modules'),
                                             'TEST_DSH_EXE': str(app)})
    assert thumbprint.strip() == release['signer']


def environment(home):
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'SYSTEMDRIVE',
               'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY'}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    for key, name in {'HOME': 'home', 'USERPROFILE': 'home', 'APPDATA': 'roaming',
                      'LOCALAPPDATA': 'local', 'TEMP': 'temp', 'TMP': 'temp', 'DSH_HOME': 'dsh'}.items():
        (home / name).mkdir(exist_ok=True)
        env[key] = str(home / name)
    return {**env, 'DSH_TELEMETRY_DISABLED': '1'}


@contextmanager
def desktop(pw, app, home, env, phase):
    port = home / 'electron/DevToolsActivePort'
    port.unlink(missing_ok=True)
    with (home / (phase + '.log')).open('wb') as log:
        process = subprocess.Popen([app, '--user-data-dir=' + str(home / 'electron'),
                                    '--remote-debugging-port=0'], cwd=home, env=env, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 45
        while not port.exists():
            assert process.poll() is None and time.monotonic() < deadline, 'Desktop startup failed'
            time.sleep(.1)
        browser = pw.chromium.connect_over_cdp('http://127.0.0.1:' + port.read_text().splitlines()[0])
        context = browser.contexts[0]
        errors = []
        context.on('weberror', lambda error: errors.append(str(error.error)))
        context.on('console', lambda msg: errors.append(msg.text) if 'slot entry crashed' in msg.text else None)
        page = enter_workspace(context, process)
        page.on('crash', lambda _: errors.append('Renderer crashed'))
        for name in ('继续', 'Continue'):
            page.add_locator_handler(page.get_by_role('button', name=name, exact=True), lambda button: button.click())
        onboarding = page.get_by_role('heading', name='开始使用', exact=True)
        page.add_locator_handler(onboarding, lambda: page.get_by_role('button', name='关闭', exact=True).click())
        if phase == 'shared':
            check_caption(page, process.pid, home, 'DeepSeek Harness')
        elif phase == 'restart':
            check_caption(page, process.pid, home, 'Alcor')
        yield page
        assert not errors, redact(str(errors))
        try:
            browser.new_browser_cdp_session().send('Browser.close')
        except Error:
            if browser.is_connected():
                raise
        assert process.wait(timeout=20) == 0, 'Desktop did not exit normally'
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def switch(page, source, target):
    page.get_by_role('button', name='切换使用模式，当前 ' + source, exact=True).click(timeout=20000)
    page.get_by_role('menuitem', name=target, exact=True).click()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'package', 'aurabot'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--upgrade-app', type=Path, help='Optional pinned newer official executable')
    parser.add_argument('--expect-compatible-upgrade', action='store_true',
                        help='Require the same business row on the newer host with a compatible plugin build')
    parser.add_argument('--artifacts', type=Path, default=ROOT / '.local/official-shared-windows')
    args = parser.parse_args()
    if args.expect_compatible_upgrade and not args.upgrade_app:
        parser.error('--expect-compatible-upgrade needs --upgrade-app')
    app = args.app.resolve(strict=True)
    release = json.loads((ROOT / 'product/desktop-release.lock.json').read_text())['windows']
    verify(app, release, release['upgradeProbe'])
    if args.upgrade_app:
        args.upgrade_app = args.upgrade_app.resolve(strict=True)
        verify(args.upgrade_app, release['upgradeProbe'])
    args.artifacts.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix='run-', dir=args.artifacts.resolve()))
    print('Synthetic Profile and acceptance evidence:', home, flush=True)
    env = environment(home)
    profile = home / 'dsh/profiles/desktop'
    profile.mkdir(parents=True)
    manifest = profile / 'package.json'
    manifest.write_text(json.dumps({'private': True, 'dsh': {'profile': {
        'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
    (profile / 'cordis.patch.yml').write_text('- id: ui-settings-models\n  config:\n    credentialOnboarding: false\n')
    modules = app.parent / 'resources/app.asar/dsh/node_modules/@deepseek-ai'
    node_env = {**env, 'ELECTRON_RUN_AS_NODE': '1', 'npm_config_ignore_scripts': 'true'}
    def plugin_cli(*arguments):
        subprocess.run([app, '--expose-internals', modules / 'dsh-desktop-host/lib/cli.js', 'plugin',
                        '--profile', 'desktop', *arguments],
                       cwd=home, env=node_env, check=True, timeout=180)
    for package in (args.package, args.aurabot):
        plugin_cli('add', package.resolve(strict=True), '--ignore-scripts')

    secretary_position = json.loads(manifest.read_text())['dsh']['profile']['bundles'].index('@alcor/secretary')

    def select_bundle(enabled):
        module = (modules / 'dsh-app-boot/lib/index.js').as_uri()
        script = f"""import {{readProfileManifest,writeProfileBundles}} from {json.dumps(module)};
            const dir=process.argv[1], name='@alcor/secretary', manifest=readProfileManifest('dsh',dir);
            const bundles=manifest.dsh.profile.bundles.filter(x=>x!==name);
            if ({json.dumps(enabled)}) bundles.splice({secretary_position},0,name);
            writeProfileBundles(dir,manifest,bundles);"""
        subprocess.run([app, '--input-type=module', '-e', script, profile], cwd=home, env=node_env, check=True)
        assert ('@alcor/secretary' in json.loads(manifest.read_text())['dsh']['profile']['bundles']) == enabled

    with sync_playwright() as pw:
        with desktop(pw, app, home, env, 'shared') as page:
            directory = home / 'shared-workspace'
            directory.mkdir()
            request = {'request': {'path': str(directory)}}
            created = rpc(page, 'workspace/create', request)
            assert created['created']
            switch(page, 'DeepSeek Harness', 'Alcor')
            expect(page.get_by_role('heading', name='事务', exact=True)).to_be_visible()
            page.get_by_role('button', name='清单管理', exact=True).click()
            expect(page.get_by_role('dialog', name='清单管理', exact=True)).to_be_visible()
            try:
                page.get_by_role('button', name='切换使用模式，当前 Alcor').click(trial=True, timeout=800)
            except PlaywrightTimeout:
                pass
            else:
                raise AssertionError('The top button should be blocked by the open dialog')
            page.keyboard.press('Escape')
            assert isinstance(rpc(page, 'alcorOrganizer/business',
                                  {'request': {'domain': 'tasks', 'method': 'categories'}}), list)
            task = rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'create_todo',
                       'arguments': {'payload': {'title': 'Synthetic Windows lifecycle'}}}})
            nav = page.get_by_label('Alcor 秘书导航', exact=True)
            expect(nav.locator('.alcor-sidebar-brand')).to_contain_text('Alcor')
            expect(nav.locator('.alcor-sidebar-chat')).to_contain_text('新对话')
            apps = nav.get_by_role('group', name='应用')
            system = nav.get_by_role('group', name='系统')
            workspace = nav.get_by_role('region', name='工作区与会话')
            expect(workspace.get_by_role('treeitem', name='shared-workspace', exact=True)).to_be_visible()
            expect(page.get_by_role('navigation', name='全局面板')).to_have_count(0)
            assert apps.bounding_box()['y'] < system.bounding_box()['y'] < workspace.bounding_box()['y']
            expect(apps.get_by_role('button', name='Aurabot')).to_have_count(0)
            page.screenshot(path=home / 'original-alcor-tasks.png')
            system.get_by_role('button', name='Aurabot', exact=True).click()
            expect(page.get_by_role('heading', name='大肥鱼', exact=True)).to_be_visible()
            page.screenshot(path=home / 'aurabot-alcor.png')
            (home / 'alcor-ax.txt').write_text(redact(page.locator('body').aria_snapshot()), encoding='utf8')
            system.get_by_role('button', name='插件', exact=True).click()
            expect(page.get_by_role('heading', name='插件', exact=True)).to_be_visible()
            bundles = rpc(page, 'pluginManager/listBundles')
            existing = rpc(page, 'workspace/create', request)
            assert not existing['created'] and existing['workspace']['workspaceId'] == created['workspace']['workspaceId']
            expect(page.get_by_role('treeitem', name='shared-workspace', exact=True)).to_be_visible()
            switch(page, 'Alcor', 'DeepSeek Harness')
            expect(nav).to_have_count(0)
            page.get_by_role('navigation', name='全局面板').get_by_role('button', name='Aurabot', exact=True).click()
            expect(page.get_by_role('heading', name='大肥鱼', exact=True)).to_be_visible()
            page.screenshot(path=home / 'aurabot-dsh.png')
            assert rpc(page, 'pluginManager/listBundles') == bundles
            switch(page, 'DeepSeek Harness', 'Alcor')
        with desktop(pw, app, home, env, 'restart') as page:
            expect(page.get_by_role('button', name='切换使用模式，当前 Alcor')).to_be_visible()
        select_bundle(False)
        with desktop(pw, app, home, env, 'disabled') as page:
            page.get_by_role('button', name='Aurabot', exact=True).click()
            expect(page.get_by_role('heading', name='大肥鱼', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='切换使用模式', exact=False)).to_have_count(0)
        select_bundle(True)
        with desktop(pw, app, home, env, 'restored') as page:
            expect(page.get_by_role('button', name='切换使用模式，当前 Alcor')).to_be_visible()
            restored = rpc(page, 'pluginManager/listBundles')
            (home / 'catalogs.json').write_text(json.dumps([bundles, restored], ensure_ascii=False), encoding='utf8')
            assert sorted(restored, key=lambda b: b['name']) == sorted(bundles, key=lambda b: b['name'])
            expect(page.get_by_role('treeitem', name='shared-workspace', exact=True)).to_be_visible()
        plugin_cli('remove', '@alcor/secretary')
        with desktop(pw, app, home, env, 'removed') as page:
            expect(page.get_by_role('button', name='切换使用模式', exact=False)).to_have_count(0)
            expect(page.get_by_role('treeitem', name='shared-workspace', exact=True)).to_be_visible()
        plugin_cli('add', args.package.resolve(strict=True), '--ignore-scripts')
        with desktop(pw, app, home, env, 'reinstalled') as page:
            records = rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'list_todos'}})
            assert any(row['id'] == task['id'] and row['title'] == task['title'] for row in records)
        if args.upgrade_app:
            config = profile / 'cordis.patch.yml'
            original = config.read_bytes()
            with desktop(pw, args.upgrade_app, home, env, 'official-upgrade') as page:
                catalog = rpc(page, 'pluginManager/listBundles')
                entry = next(b for b in catalog if b['name'] == '@alcor/secretary')
                if args.expect_compatible_upgrade:
                    assert not entry.get('error'), entry
                    expect(page.get_by_role('button', name='切换使用模式', exact=False)).to_be_visible()
                    rows = rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'list_todos'}})
                    assert any(row['id'] == task['id'] and row['title'] == task['title'] for row in rows)
                    expect(page.get_by_role('treeitem', name='shared-workspace', exact=True)).to_be_visible()
                else:
                    assert entry['error']['code'] == 'incompatible-version'
                    expect(page.get_by_role('button', name='切换使用模式', exact=False)).to_have_count(0)
                    page.get_by_role('button', name='插件', exact=True).click()
                    expect(page.get_by_role('heading', name='插件', exact=True)).to_be_visible()
                assert rpc(page, 'agentPresets/list')['presets']
                page.screenshot(path=home / 'official-upgrade.png')
            with desktop(pw, app, home, env, 'compatible-recovery') as page:
                rows = rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'list_todos'}})
                assert any(row['id'] == task['id'] and row['title'] == task['title'] for row in rows)
                assert config.read_bytes() == original
            print('PASS official host upgrade and recovery: DSH available, task and profile configuration retained')
    print('PASS shared official Windows desktop: top switch, AuraBot, workspace, catalog, restart, disable, reinstall and retention')


if __name__ == '__main__':
    main()

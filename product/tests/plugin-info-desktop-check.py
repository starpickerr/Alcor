"""Check scope labels in both product modes on an isolated official Desktop.

Pass local secretary, AgentHub and Alcor QQ archives as positional arguments.
Build @alcor/plugin-info with npm run product:plugin-info:pack first.
"""
import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home, initialize_fresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', nargs=3, type=Path, metavar='TARBALL')
    args = parser.parse_args()
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    info = desktop.ROOT / 'packages/alcor-plugin-info/package.json'
    version = json.loads(info.read_text())['version']
    package = desktop.ROOT / f'.local/artifacts/alcor-plugin-info-{version}.tgz'
    with fresh_test_home('alcor-plugin-scope-') as fixture, sync_playwright() as playwright:
        home = Path(fixture)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        initialize_fresh(fixture, home / 'dsh', profile)
        docs = home / 'documents'
        docs.mkdir()
        manifest = {'name': 'scope-test', 'private': True, 'dsh': {'profile': {'bundles': [
            '@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}
        (profile / 'package.json').write_text(json.dumps(manifest))
        (profile / 'cordis.yml').write_text('[]\n')
        (profile / 'pnpm-workspace.yaml').write_text('packages: ["."]\nnodeLinker: hoisted\nautoInstallPeers: false\n')
        (profile / 'cordis.patch.yml').write_text(
            f'- id: workspace-controller\n  config:\n    documentsDirectory: {docs}\n'
            '- id: dsh-onebot\n  disabled: true\n- id: session-telemetry-otel\n  disabled: true\n')
        desktop.native_cli(home, 'scope-install', 'add', '--ignore-scripts', str(package),
                           *(str(path.resolve()) for path in args.archives))
        with desktop.desktop(playwright, home, 'plugin-scope') as (page, errors):
            open_plugins = page.get_by_role('button', name=re.compile(r'^(Plugins|插件)$')).first
            mode = page.get_by_role('button', name=re.compile('切换使用模式'))
            open_plugins.click()
            for name, scope in [('@alcor/secretary', 'Alcor'), ('dsh-onebot-qq', 'Alcor'),
                                ('@alcor/plugin-info', '共用')]:
                expect(page.locator(f'[data-plugin-package="{name}"] .alcor-plugin-scope')).to_have_text(scope)
            hub = page.locator('[data-plugin-package="@alcor/agent-hub"]')
            buttons = hub.get_by_role('group', name='@alcor/agent-hub 使用范围')
            expect(buttons.get_by_role('button')).to_have_text(['共用', 'Alcor', 'DSH工作台'])
            expect(buttons.get_by_role('button', name='DSH工作台')).to_have_attribute('aria-pressed', 'true')
            buttons.get_by_role('button', name='共用').focus()
            page.keyboard.press('Space')
            expect(buttons.get_by_role('button', name='共用')).to_have_attribute('aria-pressed', 'true')
            assert desktop.rpc(page,'alcorPluginModes/readScopes',{'request':{}})['@alcor/agent-hub']=='both'
            hub.get_by_role('button').first.click()
            facts = page.locator('.alcor-plugin-facts')
            expect(facts.get_by_role('button', name='共用')).to_have_attribute('aria-pressed', 'true')
            expect(facts).to_have_text('共用AlcorDSH工作台')
            page.set_viewport_size({'width': 560, 'height': 840})
            facts.scroll_into_view_if_needed()
            assert facts.evaluate('e=>e.scrollWidth<=e.clientWidth+1')
            page.screenshot(path=str(desktop.ARTIFACTS / 'scope-narrow.png'))
            page.set_viewport_size({'width': 1150, 'height': 840})
            next_mode = 'DeepSeek Harness' if 'Alcor' in mode.get_attribute('aria-label') else 'Alcor'
            mode.click()
            page.get_by_role('menuitem', name=next_mode, exact=True).click()
            open_plugins.click()
            expect(hub.locator('.alcor-plugin-scope')).to_have_count(1)
            page.get_by_role('button', name=re.compile(r'^(添加插件|Add plugin)$')).first.click()
            expect(page.get_by_role('dialog')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.get_by_role('dialog')).to_have_count(0)
            assert not errors, errors
        with desktop.desktop(playwright,home,'plugin-scope-restart') as (page, errors):
            assert desktop.rpc(page,'alcorPluginModes/readScopes',{'request':{}})['@alcor/agent-hub']=='both'
            assert not errors,errors


if __name__ == '__main__':
    main()

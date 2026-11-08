"""Upgrade an existing Alpha plugin profile on the current signed Desktop; synthetic data only."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
import official_desktop_check as desktop
from briefing_sources_ui import check_briefing_sources
from plugin_check import pack_briefing_fixture


def main():
    if json.loads((desktop.ROOT / 'packages/alcor-secretary/package.json').read_text())['name'] != '@stella/secretary':
        print('SKIP: historical same-name upgrade check does not cover the Alcor package rename')
        return
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    legacy_cli = Path(os.environ['STELLA_LEGACY_CLI'])
    old_packs = [desktop.ROOT / '.local/artifacts/stella-secretary-0.1.5.tgz', desktop.ROOT / '.local/artifacts/stella-apple-sync-0.1.0.tgz']
    apple_version = json.loads((desktop.ROOT / 'packages/alcor-apple-sync/package.json').read_text())['version']
    apple_pack = desktop.ROOT / f'.local/artifacts/stella-apple-sync-{apple_version}.tgz'
    with tempfile.TemporaryDirectory(prefix='stella-upgrade-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        environment = desktop.environment(home)
        legacy_profile = home / 'dsh/profiles/compatibility'
        legacy_profile.mkdir(parents=True)
        (legacy_profile / 'pnpm-workspace.yaml').write_text('packages:\n  - .\nnodeLinker: hoisted\nautoInstallPeers: false\n')
        (legacy_profile / 'package.json').write_text(json.dumps({'name': 'upgrade-test', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        with (desktop.ARTIFACTS / 'legacy-install.log').open('w') as log:
            subprocess.run(['node', str(legacy_cli), 'plugin', '--profile', 'compatibility', 'add', *map(str, old_packs), '--ignore-scripts'], cwd=home, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=120, check=True)
        profile = home / 'dsh/profiles/desktop'
        (home / 'dsh/profiles/compatibility').rename(profile)
        patch = '# Synthetic owner configuration\n- id: agent-default-model\n  config:\n    provider: deepseek-account\n    model: deepseek-flash\n'
        (profile / 'cordis.patch.yml').write_text(patch)
        store = profile / 'stella/business'
        store.mkdir(parents=True)
        marker = store / 'retention-check.txt'
        marker.write_text('retain existing private business storage')
        assert (profile / 'node_modules/@deepseek-ai/dsh-tools').exists(), 'Old package must reproduce host dependency shadowing'
        plugin_pack = pack_briefing_fixture(home)
        desktop.native_cli(home, 'upgrade', 'add', str(desktop.PACK), str(apple_pack), str(plugin_pack), '--ignore-scripts')
        assert (profile / 'cordis.patch.yml').read_text() == patch
        assert marker.read_text() == 'retain existing private business storage'
        assert not (profile / 'node_modules/@deepseek-ai/dsh-tools').exists(), 'Upgraded plugin must remove its old shadow of host tools'
        with desktop.desktop(playwright, home, 'upgraded-restart') as (page, errors):
            desktop.check_modes(page)
            check_briefing_sources(page, lambda endpoint, args: desktop.rpc(page, endpoint, args), desktop.ARTIFACTS)
            reads = profile / 'node_modules/@stella-test/briefing-source/reads.jsonl'
            assert all(json.loads(line)['type'] == 'catalog' for line in reads.read_text().splitlines())
            status = desktop.rpc(page, 'alcorAppleSync/invoke', {'request': {'method': 'status'}})
            assert status['supported'] and not status['connected']
            assert not errors, errors
        # A future unsupported Stella must be skipped without removing host services.
        package = profile / 'node_modules/@stella/secretary/package.json'
        manifest = json.loads(package.read_text())
        manifest['engines']['dsh'] = '99.0.0'
        package.write_text(json.dumps(manifest))
        with desktop.desktop(playwright, home, 'unsupported-stella') as (page, errors):
            assert page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).count() == 0
            presets = desktop.rpc(page, 'agentPresets/list')
            assert presets['presets'], 'Native DSH must retain its agents when Stella is skipped'
            assert not errors, errors
        assert marker.exists()
    print('PASS: old profile upgraded, owner patch/data retained, both plugins start; incompatible Stella skipped without breaking DSH')


if __name__ == '__main__':
    main()

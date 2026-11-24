"""Verify standalone Apple settings in official DSH with a synthetic EventKit helper."""
import json
import os
from pathlib import Path
import re

from playwright.sync_api import expect, sync_playwright

import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home


def main():
    archive = Path(os.environ['ALCOR_APPLE_PACK']).resolve()
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with fresh_test_home('dsh-apple-standalone-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        package = profile / 'package.json'
        bundle = '@alcor/apple-sync'
        base = ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']
        package.write_text(json.dumps({'name': 'dsh-apple-standalone-check', 'private': True,
                                       'dsh': {'profile': {'bundles': base}}}))
        desktop.native_cli(home, 'apple-standalone-install', 'add', str(archive), '--ignore-scripts')
        manifest = json.loads(package.read_text())
        assert set(manifest.get('dependencies', {})) == {bundle}
        manifest['dsh']['profile']['bundles'] = [*base, bundle]
        package.write_text(json.dumps(manifest))
        with desktop.desktop(playwright, home, 'apple-standalone') as (page, errors):
            def call(method, request):
                return desktop.rpc(page, 'dshAppleSettings/' + method, {'request': request})

            assert call('state', {}) == {'settings': {'calendar': [], 'reminders': [], 'writes': False}, 'alcor': False}
            assert call('sources', {'kind': 'calendar'})['sources'][0]['title'] == 'Synthetic source'
            assert call('sources', {'kind': 'reminders'})['sources'][0]['title'] == 'Synthetic source'
            page.get_by_role('button', name=re.compile(r'^(插件|Plugins)$')).first.click()
            page.locator('[data-plugin-package="@alcor/apple-sync"] button').first.click()
            expect(page.get_by_role('heading', name='Apple 日历与提醒事项', exact=True)).to_be_visible()
            page.get_by_role('checkbox', name=re.compile('Synthetic source')).first.check()
            page.get_by_role('button', name='保存对话访问权限', exact=True).click()
            expect(page.get_by_role('status').get_by_text('已保存', exact=True)).to_be_visible()
            assert call('state', {})['settings'] == {'calendar': ['a'], 'reminders': [], 'writes': False}
            assert not errors, errors
        assert not (profile / 'alcor').exists(), 'The standalone package created an Alcor profile'
        with desktop.desktop(playwright, home, 'apple-standalone-restart') as (page, errors):
            state = desktop.rpc(page, 'dshAppleSettings/state', {'request': {}})
            assert state == {'settings': {'calendar': ['a'], 'reminders': [], 'writes': False}, 'alcor': False}
            assert not errors, errors
        if secretary := os.environ.get('ALCOR_SECRETARY_PACK'):
            desktop.native_cli(home, 'apple-with-alcor-install', 'add', str(Path(secretary).resolve()), '--ignore-scripts')
            manifest = json.loads(package.read_text())
            manifest['dsh']['profile']['bundles'] = [*base, bundle, '@alcor/secretary']
            package.write_text(json.dumps(manifest))
            with desktop.desktop(playwright, home, 'apple-with-alcor') as (page, errors):
                state = desktop.rpc(page, 'dshAppleSettings/state', {'request': {}})
                assert state['alcor'] and state['settings']['calendar'] == ['a']
                status = desktop.rpc(page, 'alcorAppleSync/invoke', {'request': {'method': 'status', 'arguments': {'kind': 'calendar'}}})
                assert status['authorization'] == 'full_access', status
                assert not errors, errors
            manifest['dsh']['profile']['bundles'] = [*base, bundle]
            package.write_text(json.dumps(manifest))
            with desktop.desktop(playwright, home, 'apple-without-alcor-again') as (page, errors):
                state = desktop.rpc(page, 'dshAppleSettings/state', {'request': {}})
                assert not state['alcor'] and state['settings']['calendar'] == ['a']
                assert not errors, errors
            print('PASS: optional Alcor sync registers, then can be disabled independently')
    print('PASS: official DSH standalone install, Apple settings and persisted source selection without Alcor')


if __name__ == '__main__':
    main()

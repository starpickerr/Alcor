"""Install both source plugins in official DSH and attach Alcor to the same accounts."""
import json
import os
from pathlib import Path
import re

from playwright.sync_api import expect, sync_playwright

import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home


BASE = ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']
CONNECTORS = ['@alcor/mail', '@alcor/elearning']


def settings(page, package):
    page.get_by_role('button', name=re.compile(r'^(插件|Plugins)$')).first.click()
    back = page.get_by_role('button', name=re.compile(r'^(返回插件|Back to plugins)$'))
    if back.is_visible():
        back.click()
    page.locator(f'[data-plugin-package="{package}"] button').first.click()
    return page.locator(f'[data-plugin-detail="{package}"] [data-plugin-config]')


def state(page):
    return (desktop.rpc(page, 'alcorMail/state', {'request': {}}),
            desktop.rpc(page, 'alcorSchool/elearningAccount', {'request': {}}))


def main():
    directory = Path(os.environ.get('STELLA_SOURCE_REVIEW_DIRECTORY', desktop.ROOT / '.local/artifacts'))
    secretary = Path(os.environ.get('ALCOR_SECRETARY_PACK', desktop.PACK))
    packs = []
    for name, repo in [('mail', 'dsh-email'), ('elearning', 'dsh-fudan-elearning')]:
        version = json.loads((desktop.ROOT.parent / repo / 'plugin/package.json').read_text())['version']
        packs.append(directory / f'alcor-{name}-{version}.tgz')
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with fresh_test_home('dsh-standalone-sources-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        manifest = profile / 'package.json'
        manifest.write_text(json.dumps({'name': 'dsh-standalone-sources', 'private': True,
                                        'dsh': {'profile': {'bundles': BASE}}}))

        def enable(bundles):
            value = json.loads(manifest.read_text())
            value['dsh']['profile']['bundles'] = [*BASE, *bundles]
            manifest.write_text(json.dumps(value))

        desktop.native_cli(home, 'standalone-sources-install', 'add', *[str(path.resolve()) for path in packs], '--ignore-scripts')
        assert set(json.loads(manifest.read_text())['dependencies']) == set(CONNECTORS)
        enable(CONNECTORS)
        with desktop.desktop(playwright, home, 'standalone-sources') as (page, errors):
            assert all(result['installed'] and not result['configured'] for result in state(page))
            school = settings(page, '@alcor/elearning')
            school.get_by_label('学工号', exact=True).fill('synthetic-school-account')
            school.get_by_label('eLearning 密码', exact=True).fill('synthetic-school-secret')
            school.get_by_role('button', name='保存账号', exact=True).click()
            expect(school.get_by_role('status')).to_contain_text('已保存', timeout=30000)
            expect(school.get_by_label('eLearning 密码', exact=True)).to_have_value('')
            mail = settings(page, '@alcor/mail')
            for label, value in [('邮箱账号', 'synthetic@example.com'), ('邮箱授权码', 'synthetic-mail-secret'), ('IMAP 服务器', 'imap.example.com')]:
                mail.get_by_label(label, exact=True).fill(value)
            mail.get_by_role('button', name='保存邮箱', exact=True).click()
            expect(mail.get_by_role('status')).to_contain_text('已保存', timeout=30000)
            expect(mail.get_by_label('邮箱授权码', exact=True)).to_have_value('')
            initial = state(page)
            assert all(result['configured'] and result['connected'] for result in initial)
            assert 'synthetic-mail-secret' not in json.dumps(initial)
            assert not errors, errors
        assert not (home / 'dsh/storages/alcor_organizer.json').exists()
        with desktop.desktop(playwright, home, 'standalone-sources-restart') as (page, errors):
            mail, school = state(page)
            assert mail['accounts'][0]['id'] == initial[0]['accounts'][0]['id']
            assert school['username'] == 'synthetic-school-account'
            assert mail['connected'] and school['connected']
            assert not errors, errors
        desktop.native_cli(home, 'standalone-sources-alcor-install', 'add', str(secretary.resolve()), '--ignore-scripts')
        enable([*CONNECTORS, '@alcor/secretary'])
        with desktop.desktop(playwright, home, 'standalone-sources-with-alcor') as (page, errors):
            catalog = desktop.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': 'discover_sources'}})
            assert {row['source'] for row in catalog['sources']} >= {'email', 'elearning'}, catalog
            assert state(page)[0]['accounts'][0]['id'] == initial[0]['accounts'][0]['id']
            assert not errors, errors
        enable(CONNECTORS)
        with desktop.desktop(playwright, home, 'standalone-sources-without-alcor') as (page, errors):
            mail, school = state(page)
            assert mail['connected'] and school['connected']
            assert not desktop.rpc(page, 'alcorMail/removeAccount', {'request': {'id': mail['accounts'][0]['id'], 'expectedRevision': mail['revision']}})['configured']
            assert not desktop.rpc(page, 'alcorSchool/removeElearningAccount', {'request': {}})['configured']
            assert not errors, errors
    print('PASS: standalone source settings, restart, optional Alcor discovery and owner revocation')


if __name__ == '__main__':
    main()

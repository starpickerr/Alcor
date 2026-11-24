"""Install four local archives on pinned Windows DSH with synthetic accounts."""
import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from official_shared_windows import ROOT, desktop, environment, profile_commands, rpc, switch, verify


def call(page, endpoint, **request):
    return rpc(page, endpoint, {'request': request})


def chatgpt_fixture(home, profile):
    tests = ROOT.parent / 'dsh-chatgpt/tests'
    spec = importlib.util.spec_from_file_location('chatgpt_desktop_check', tests / 'chatgpt-desktop-check.py')
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    (home / 'dsh/synthetic-plan.json').write_text('{}', encoding='utf-8')
    transport = json.dumps(str(tests / 'fixtures/chatgpt-plan.mjs'))
    (profile / 'cordis.patch.yml').write_text(
        f'- insert:\n    - id: synthetic-plan-transport\n      name: {transport}\n'
        '- id: ui-settings-models\n  config:\n    credentialOnboarding: false\n',
        encoding='utf-8',
    )
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('app', 'secretary', 'mail', 'elearning', 'chatgpt'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, default=ROOT / '.local/windows-four-plugin-integration')
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    releases = json.loads((ROOT / 'product/desktop-release.lock.json').read_text(encoding='utf-8'))['windows']
    verify(app, releases, *releases['compatibleReleases'])
    args.artifacts.mkdir(parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix='check-', dir=args.artifacts.resolve()))
    print('Synthetic Windows Profile:', home, flush=True)
    env = environment(home)
    profile = Path(env['DSH_HOME']) / 'profiles/desktop'
    profile.mkdir(parents=True)
    (profile / 'package.json').write_text(json.dumps({'private': True, 'dsh': {'profile': {
        'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}), encoding='utf-8')
    checks = chatgpt_fixture(home, profile)
    plugin_cli, _ = profile_commands(app, home, env)
    names = ('secretary', 'mail', 'elearning', 'chatgpt')
    for name in names:
        plugin_cli('add', getattr(args, name).resolve(strict=True), '--ignore-scripts')

    with sync_playwright() as pw:
        with desktop(pw, app, home, env, 'four-plugins') as page:
            catalog = {row['name']: row for row in rpc(page, 'pluginManager/listBundles')}
            for name in names:
                plugin = catalog['@alcor/' + name]
                assert not plugin.get('error'), plugin
            switch(page, 'DeepSeek Harness', 'Alcor')
            expect(page.get_by_role('heading', name='事务', exact=True)).to_be_visible()
            assert isinstance(call(page, 'alcorOrganizer/business', domain='tasks', method='list_todos'), list)
            mail = call(page, 'alcorMail/state')
            assert mail['installed'] and not mail['configured']
            email = call(page, 'alcorMail/save', expectedRevision=mail['revision'], account={
                'host': 'imap.test.invalid', 'user': 'fixture@test.invalid', 'password': 'synthetic-mail-only',
                'port': 993, 'folder': 'INBOX', 'enabled': True})
            assert email['connected'], email
            school = call(page, 'alcorSchool/elearningAccount')
            assert school['installed'] and not school['configured']
            school = call(page, 'alcorSchool/saveElearningAccount', username='fixture', password='synthetic-school-only')
            assert school['connected'], school
            assert not call(page, 'alcorChatgpt/accountState')['accounts']
            checks.login(page, home)
            assert 'plan-test' in checks.model_catalog(page)
            switch(page, 'Alcor', 'DeepSeek Harness')
            assert call(page, 'alcorMail/state')['connected']
            assert call(page, 'alcorSchool/elearningAccount')['connected']
            assert 'plan-test' in checks.model_catalog(page)
        with desktop(pw, app, home, env, 'four-plugins-restart') as page:
            email = call(page, 'alcorMail/state')
            assert email['connected']
            assert call(page, 'alcorSchool/elearningAccount')['connected']
            account = call(page, 'alcorChatgpt/accountState')['activeId']
            assert account and 'plan-test' in checks.model_catalog(page)
            call(page, 'alcorMail/removeAccount', expectedRevision=email['revision'], id=email['accounts'][0]['id'])
            call(page, 'alcorSchool/removeElearningAccount')
            call(page, 'alcorChatgpt/signOut', accountId=account)
            assert not call(page, 'alcorMail/state')['configured']
            assert not call(page, 'alcorSchool/elearningAccount')['configured']
            assert 'plan-test' not in checks.model_catalog(page)
    print('PASS: all four archives on official Windows DSH, both modes, frozen MCP connections, '
          'synthetic ChatGPT authorization and model catalog, and persistence and revocation across restart.')


if __name__ == '__main__':
    main()

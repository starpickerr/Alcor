"""Upgrade all current Alcor packages between signed hosts; disposable data only."""
import json
import os
from pathlib import Path
import re
import tarfile

from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home

PACKAGES = {'@alcor/' + name for name in ('secretary', 'apple-sync', 'elearning', 'mail', 'agent-hub')}


def archives(directory):
    result = {}
    for archive in Path(directory).glob('*.tgz'):
        with tarfile.open(archive) as bundle:
            manifest = json.load(bundle.extractfile('package/package.json'))
        name = manifest['name']
        if name in PACKAGES:
            assert name not in result, 'Only one artifact per package is allowed'
            result[name] = archive
    assert set(result) == PACKAGES, 'Build or provide all five Alcor packages'
    return [str(result[name]) for name in sorted(result)]


def check_services(page):
    assert desktop.rpc(page, 'alcorSchool/elearningAccount', {'request': {}})['installed']
    assert desktop.rpc(page, 'alcorMail/state', {'request': {}})['installed']
    assert desktop.rpc(page, 'agentHub/read', {'request': {}}) == {'jobs': []}
    assert desktop.rpc(page, 'alcorAppleSync/invoke', {'request': {'method': 'status'}})['supported']
    expect(page.get_by_role('button', name=re.compile(r'^切换使用模式，当前 '))).to_be_visible()


def main():
    previous = Path(os.environ['ALCOR_PREVIOUS_DESKTOP_APP'])
    current = desktop.APP
    releases = desktop.RELEASE
    old_packs = archives(os.environ['ALCOR_PREVIOUS_ARTIFACTS'])
    new_packs = archives(desktop.ROOT / '.local/artifacts')
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    identities = []
    for app in (previous, current):
        desktop.APP, desktop.RELEASE = app, releases
        desktop.verify_release()
        identities.append(desktop.digest(app / 'Contents/Resources/app.asar'))
    with fresh_test_home('alcor-host-upgrade-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.APP = previous
        with desktop.desktop(playwright, home, 'previous-baseline') as (page, errors):
            assert not errors, errors
        desktop.native_cli(home, 'previous-install', 'add', *old_packs, '--ignore-scripts')
        with desktop.desktop(playwright, home, 'previous-installed') as (page, errors):
            check_services(page)
            task = desktop.rpc(page, 'alcorOrganizer/business', {'request': {
                'domain': 'tasks', 'method': 'create_todo',
                'arguments': {'payload': {'title': 'Synthetic host upgrade retention'}},
            }})
            directory = home / 'upgrade-workspace'
            directory.mkdir()
            workspace = desktop.rpc(page, 'workspace/create', {'request': {'path': str(directory)}})['workspace']
            assert not errors, errors
        profile = home / 'dsh/profiles/desktop'
        before = json.loads((profile / 'package.json').read_text())
        patch = profile / 'cordis.patch.yml'
        owner_patch = patch.read_bytes() if patch.exists() else None
        marker = profile / 'alcor/upgrade-marker.txt'
        marker.write_text('Synthetic owner data must survive a host upgrade')
        desktop.APP = current
        # Unsupported old plugins must not stop native DSH from opening.
        with desktop.desktop(playwright, home, 'unsupported-recovery') as (page, errors):
            expect(page.get_by_role('button', name=re.compile(r'^切换使用模式，当前 '))).to_have_count(0)
            assert desktop.rpc(page, 'agentPresets/list')['presets']
            assert not errors, errors
        desktop.native_cli(home, 'upgrade', 'add', *new_packs, '--ignore-scripts')
        after = json.loads((profile / 'package.json').read_text())
        assert after['dsh'] == before['dsh'], 'Selected bundles changed during upgrade'
        assert (patch.read_bytes() if patch.exists() else None) == owner_patch
        for phase in ('upgraded', 'upgraded-restart'):
            with desktop.desktop(playwright, home, phase) as (page, errors):
                check_services(page)
                rows = desktop.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'list_todos'}})
                assert sum(row['id'] == task['id'] and row['title'] == task['title'] for row in rows) == 1
                existing = desktop.rpc(page, 'workspace/create', {'request': {'path': str(directory)}})
                assert not existing['created'] and existing['workspace']['workspaceId'] == workspace['workspaceId']
                assert marker.read_text() == 'Synthetic owner data must survive a host upgrade'
                assert (patch.read_bytes() if patch.exists() else None) == owner_patch
                assert not errors, errors
    for app, identity in zip((previous, current), identities):
        desktop.APP, desktop.RELEASE = app, releases
        desktop.verify_release()
        assert desktop.digest(app / 'Contents/Resources/app.asar') == identity
    print('PASS: five plugins start and restart after host/package upgrade; task, workspace, bundles and owner data retained; unsupported plugins leave native DSH usable; official apps unchanged')


if __name__ == '__main__':
    main()

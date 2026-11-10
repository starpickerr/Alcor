"""Install the real frozen school bundle on official Desktop; use synthetic credentials only."""
import json
import os
from pathlib import Path
import re
import time

from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from fresh_profile_fixture import fresh_test_home, initialize_fresh

ROOT = Path(__file__).resolve().parents[2]
VERSION = json.loads((ROOT / 'packages/alcor-elearning/package.json').read_text())['version']
PACK = Path(os.environ.get('ALCOR_ELEARNING_REVIEW_PACK', str(ROOT / '.local/artifacts/elearning-file-submissions' / f'alcor-elearning-{VERSION}.tgz')))
NAMES = {'mcp__elearning__' + name for name in ('list_courses', 'fetch', 'list_files', 'download_file', 'get_assignment', 'submit_assignment')}


def inventory(page, filename, expected):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if filename.is_file():
            rows = json.loads(filename.read_text())
            if {row['name'] for row in rows} == expected:
                return {row['name']: row for row in rows}
        page.wait_for_timeout(100)
    raise AssertionError('School tool inventory did not reach expected state')


def main():
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with fresh_test_home(prefix='alcor-transfer-desktop-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        initialize_fresh(temporary, home / 'dsh', profile)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'school-transfer-check', 'private': True,
            'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        observed = home / 'school-tools.json'
        observer = home / 'school-tool-observer.mjs'
        observer.write_text('''import {writeFileSync} from 'node:fs';
export const inject=['tools'];
export function apply(ctx) {
  let previous;
  const timer=setInterval(()=>{
    const value=JSON.stringify(ctx.tools.schemas().filter(row=>row.name.startsWith('mcp__elearning__')));
    if(value!==previous){writeFileSync(OUTPUT,value);previous=value;}
  },100);
  ctx.effect(()=>()=>clearInterval(timer));
}
'''.replace('OUTPUT', json.dumps(str(observed))))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n- insert:\n    - id: school-tool-observer\n      name: {json.dumps(str(observer))}\n')
        desktop.native_cli(home, 'school-install', 'add', str(PACK), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'school-tools') as (page, errors):
            inventory(page, observed, set())
            state = desktop.rpc(page, 'alcorSchool/saveElearningAccount', {'request': {'username': 'synthetic-transfer-user', 'password': 'synthetic-transfer-password'}})
            assert state['connected'] and state['configured'], state
            tools = inventory(page, observed, NAMES)
            assert tools['mcp__elearning__submit_assignment']['parameters']['required'] == ['course_id', 'assignment_id', 'file_paths', 'expected_attempt']
            assert 'destination' in tools['mcp__elearning__download_file']['parameters']['required']
            page.get_by_role('button', name=re.compile(r'^(Plugins|插件)$')).click()
            page.get_by_text('dsh-fudan-elearning', exact=True).click()
            expect(page.get_by_label('学工号', exact=True)).to_have_value('synthetic-transfer-user')
            desktop.rpc(page, 'pluginManager/setBundleEnabled', {'name': '@alcor/elearning', 'enabled': False})
            assert not errors, errors
        with desktop.desktop(playwright, home, 'school-disabled') as (page, errors):
            inventory(page, observed, set())
            desktop.rpc(page, 'pluginManager/setBundleEnabled', {'name': '@alcor/elearning', 'enabled': True})
            assert not errors, errors
        with desktop.desktop(playwright, home, 'school-restored') as (page, errors):
            inventory(page, observed, NAMES)
            state = desktop.rpc(page, 'alcorSchool/elearningAccount', {'request': {}})
            assert state['connected'] and state['username'] == 'synthetic-transfer-user'
            state = desktop.rpc(page, 'alcorSchool/removeElearningAccount', {'request': {}})
            assert not state['connected'] and not state['configured']
            inventory(page, observed, set())
            assert not errors, errors
        desktop.native_cli(home, 'school-uninstall', 'remove', '@alcor/elearning')
        assert '@alcor/elearning' not in json.loads((profile / 'package.json').read_text()).get('dependencies', {})
    print('PASS: official Desktop installs the school bundle, discovers six real frozen tools, preserves credentials over restart, disables, revokes and uninstalls; no real school calls')


if __name__ == '__main__':
    main()

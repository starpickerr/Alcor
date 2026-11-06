"""Official Desktop source bundles, credentials, discovery and lifecycle; no real accounts."""
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import official_desktop_check as desktop
from plugin_check import ROOT, PACK
from web_check import open_secretary

SOURCES = []
for name in ('elearning', 'mail'):
    version = json.loads((ROOT / 'packages' / f'stella-{name}' / 'package.json').read_text())['version']
    SOURCES.append(ROOT / f'.local/artifacts/stella-{name}-{version}.tgz')


def config(page, package):
    page.get_by_role('button', name=re.compile(r'^(插件|Plugins)$')).click()
    back = page.get_by_role('button', name=re.compile(r'^(返回插件|Back to plugins)$'))
    if back.is_visible(): back.click()
    page.get_by_role('heading', name=re.compile(r'^(插件|Plugins)$')).wait_for()
    page.get_by_text(package, exact=True).click()
    page.locator(f'[data-plugin-detail="{package}"] [data-plugin-config]').wait_for()


def open_sources(page):
    open_secretary(page)
    page.get_by_role('button', name='简报', exact=True).click()
    refresh = page.get_by_role('button', name='刷新简报', exact=True)
    assert not refresh.inner_text().strip() and refresh.locator('svg').count() == 1
    page.get_by_role('button', name='信息来源', exact=True).click()
    dialog = page.get_by_role('dialog', name='简报信息来源', exact=True)
    refresh = dialog.get_by_role('button', name='刷新来源', exact=True)
    refresh.click()
    expect(refresh).to_be_enabled()
    assert not refresh.inner_text().strip() and refresh.locator('svg').count() == 1
    return dialog


def check(page, home, profile, artifacts):
    call = lambda endpoint, args={}: desktop.rpc(page, endpoint, args)
    school = lambda: call('stellaSchool/elearningAccount', {'request': {}})
    mail = lambda: call('stellaMail/state', {'request': {}})
    grants = lambda: call('stellaSecretary/mcpState', {'request': {}})
    assert school()['installed'] and not school()['configured']
    assert mail()['installed'] and not mail()['configured']
    assert not grants()['allowedTools']
    dialog = open_sources(page)
    for package in ('@stella/elearning', '@stella/mail'):
        row = dialog.locator(f'[data-source-connection="{package}"]')
        expect(row).to_contain_text('未配置账号')
        expect(row.get_by_role('button', name='设置账号', exact=True)).to_be_enabled()
        expect(row.get_by_role('checkbox')).to_have_count(0)
    page.screenshot(path=str(artifacts / 'source-setup-desktop.png'), animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(artifacts / 'source-setup-narrow.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    dialog.locator('[data-source-connection="@stella/elearning"]').get_by_role('button', name='设置账号', exact=True).click()
    page.locator('[data-plugin-detail="@stella/elearning"] [data-plugin-config]').wait_for()
    expect(page.locator('[data-plugin-config] details')).to_have_count(0)
    expect(page.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
    page.get_by_label('学工号', exact=True).fill('synthetic-source-user')
    page.get_by_label('eLearning 密码', exact=True).fill('synthetic-school-secret')
    page.get_by_role('button', name='保存账号', exact=True).click()
    expect(page.locator('[data-plugin-config]').get_by_role('status')).to_contain_text('已保存')
    expect(page.get_by_label('eLearning 密码', exact=True)).to_have_value('')
    expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('placeholder', '********')
    expect(page.get_by_role('button', name='保存账号', exact=True)).to_be_disabled()
    expect(page.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
    assert page.get_by_role('button', name='测试连接', exact=True).bounding_box()['y'] > page.get_by_role('button', name='保存账号', exact=True).bounding_box()['y']
    page.get_by_label('学工号', exact=True).fill('changed-school-user')
    expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('placeholder', '')
    expect(page.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
    page.get_by_label('学工号', exact=True).fill('synthetic-source-user')
    assert school()['configured'] and school()['connected']
    page.screenshot(path=str(artifacts / 'school-settings.png'), animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='测试连接', exact=True).click(trial=True)
    assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(artifacts / 'school-narrow.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    dialog = open_sources(page)
    expect(dialog.get_by_label('复旦 eLearning', exact=True)).to_be_visible()
    expect(dialog.locator('[data-source-connection="@stella/elearning"]')).to_have_count(0)
    dialog.locator('[data-source-connection="@stella/mail"]').get_by_role('button', name='设置账号', exact=True).click()
    page.locator('[data-plugin-detail="@stella/mail"] [data-plugin-config]').wait_for()
    expect(page.locator('[data-plugin-config] details')).to_have_count(0)
    expect(page.locator('[data-mail-account="new"]')).to_be_visible()
    page.get_by_label('邮箱账号', exact=True).fill('synthetic@example.com')
    page.get_by_label('邮箱授权码', exact=True).fill('synthetic-mail-secret')
    page.get_by_label('IMAP 服务器', exact=True).fill('imap.example.com')
    page.get_by_role('button', name='保存邮箱', exact=True).click()
    expect(page.locator('[data-plugin-config]').get_by_role('status')).to_contain_text('已保存')
    assert mail()['configured'] and mail()['connected']
    assert not any('password' in row for row in mail()['accounts'])
    first_id = mail()['accounts'][0]['id']
    first = page.locator(f'[data-mail-account="{first_id}"]')
    expect(first.get_by_label('邮箱账号', exact=True)).to_have_value('synthetic@example.com')
    expect(first.get_by_label('邮箱授权码', exact=True)).to_have_value('')
    expect(first.get_by_label('邮箱授权码', exact=True)).to_have_attribute('placeholder', '********')
    expect(first.get_by_role('button', name='保存邮箱', exact=True)).to_be_disabled()
    expect(first.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
    assert first.get_by_role('button', name='测试连接', exact=True).bounding_box()['y'] > first.get_by_role('button', name='保存邮箱', exact=True).bounding_box()['y']
    first.get_by_label('邮件文件夹', exact=True).fill('Archive')
    expect(first.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
    page.get_by_role('button', name='添加邮箱', exact=True).click()
    second = page.locator('[data-mail-account="new"]')
    second.get_by_label('邮箱账号', exact=True).fill('second@example.com')
    second.get_by_label('邮箱授权码', exact=True).fill('second-synthetic-mail-secret')
    second.get_by_label('IMAP 服务器', exact=True).fill('imap.example.com')
    second.get_by_role('button', name='保存邮箱', exact=True).click()
    expect(second).to_have_count(0)
    assert len(mail()['accounts']) == 2
    page.get_by_role('button', name='选择邮箱：synthetic@example.com', exact=True).click()
    expect(first.get_by_label('邮件文件夹', exact=True)).to_have_value('Archive')
    first.get_by_role('button', name='保存邮箱', exact=True).click()
    expect(page.locator('[data-plugin-config]').get_by_role('alert')).to_contain_text('已变化')
    expect(first.get_by_label('邮件文件夹', exact=True)).to_have_value('Archive')
    first.get_by_role('button', name='撤销修改', exact=True).click()
    expect(first.get_by_label('邮件文件夹', exact=True)).to_have_value('INBOX')
    second_id = next(row['id'] for row in mail()['accounts'] if row['id'] != first_id)
    second = page.locator(f'[data-mail-account="{second_id}"]')
    page.get_by_role('button', name='选择邮箱：second@example.com', exact=True).click()
    second.get_by_role('button', name='移除邮箱', exact=True).click()
    page.get_by_role('button', name='确认移除邮箱', exact=True).click()
    expect(second).to_have_count(0)
    assert len(mail()['accounts']) == 1
    for folder in ('Archive', 'INBOX'):
        first.get_by_label('邮件文件夹', exact=True).fill(folder)
        first.get_by_role('button', name='保存邮箱', exact=True).click()
        expect(first.get_by_role('button', name='保存邮箱', exact=True)).to_be_disabled()
        assert mail()['accounts'][0]['folder'] == folder
    assert 'synthetic-mail-secret' in (home / 'dsh/.credentials.yaml').read_text()
    assert 'second-synthetic-mail-secret' not in (home / 'dsh/.credentials.yaml').read_text()
    page.get_by_role('button', name='选择邮箱：synthetic@example.com', exact=True).click()
    first.get_by_label('邮箱授权码', exact=True).fill('updated-mail-password')
    first.get_by_role('button', name='保存邮箱', exact=True).click()
    expect(first.get_by_label('邮箱授权码', exact=True)).to_have_value('')
    expect(first.get_by_label('邮箱授权码', exact=True)).to_have_attribute('placeholder', '********')
    assert 'updated-mail-password' in (home / 'dsh/.credentials.yaml').read_text()
    assert 'synthetic-mail-secret' not in (home / 'dsh/.credentials.yaml').read_text()
    assert 'updated-mail-password' not in json.dumps(mail())
    # Exercise the supported 20-mailbox limit without connecting to any external mailbox.
    extra_ids = []
    for index in range(19):
        result = call('stellaMail/save', {'request': {'expectedRevision': mail()['revision'], 'account': {
            'user': f'synthetic{index}@example.com', 'password': 'synthetic-only-pass', 'host': 'imap.example.com',
            'port': 993, 'folder': 'INBOX', 'enabled': False}}})
        extra_ids.append(next(r['id'] for r in result['accounts'] if r['user'] == f'synthetic{index}@example.com'))
    page.get_by_role('button', name='刷新状态', exact=True).click()
    expect(page.get_by_role('button', name='添加邮箱', exact=True)).to_be_disabled()
    expect(page.locator('.stella-mail-connection:visible')).to_have_count(1)
    page.get_by_label('搜索邮箱', exact=True).fill('synthetic18@')
    expect(page.locator('.stella-mail-account-row')).to_have_count(1)
    page.get_by_role('button', name='选择邮箱：synthetic18@example.com', exact=True).click()
    expect(page.locator('.stella-mail-connection:visible').get_by_label('邮箱账号', exact=True)).to_have_value('synthetic18@example.com')
    page.get_by_label('搜索邮箱', exact=True).fill('')
    page.get_by_role('button', name='选择邮箱：synthetic@example.com', exact=True).click()
    first.get_by_label('邮件文件夹', exact=True).fill('Draft kept locally')
    page.get_by_role('button', name='选择邮箱：synthetic18@example.com', exact=True).click()
    page.get_by_role('button', name='选择邮箱：synthetic@example.com', exact=True).click()
    expect(first.get_by_label('邮件文件夹', exact=True)).to_have_value('Draft kept locally')
    first.get_by_role('button', name='撤销修改', exact=True).click()
    page.locator('.stella-mail-settings').screenshot(path=str(artifacts / 'mail-multi-desktop.png'), animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('.stella-mail-settings').screenshot(path=str(artifacts / 'mail-multi-mobile.png'), animations='disabled')
    assert page.locator('.stella-mail-settings').evaluate('element => element.scrollWidth <= element.clientWidth')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    for identity in extra_ids:
        call('stellaMail/removeAccount', {'request': {'id': identity, 'expectedRevision': mail()['revision']}})
    page.get_by_role('button', name='刷新状态', exact=True).click()
    names = set(grants()['availableTools'])
    assert {'mcp__elearning__fetch', 'mcp__elearning__list_courses', 'mcp__email__fetch_messages', 'mcp__email__list_sources'} <= names
    assert not any(name.endswith(('__save_account', '__delete_account', '__save_settings')) for name in names)
    assert not grants()['allowedTools']
    credential_file = home / 'dsh/.credentials.yaml'
    assert credential_file.stat().st_mode & 0o777 == 0o600
    assert 'synthetic-mail-secret' not in (profile / 'cordis.patch.yml').read_text()
    for path in (profile / 'stella').rglob('*'):
        if path.is_file():
            assert not any(secret in path.read_bytes() for secret in (b'synthetic-mail-secret', b'updated-mail-password', b'synthetic-school-secret')), path.name
    page.screenshot(path=str(artifacts / 'mail-settings.png'), animations='disabled')
    page.get_by_role('button', name='刷新状态', exact=True).click()
    expect(first.get_by_label('邮箱授权码', exact=True)).to_have_attribute('placeholder', '********')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.keyboard.press('Escape')
    assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(artifacts / 'mail-narrow.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    dialog = open_sources(page)
    expect(dialog.locator('[data-source-connection]')).to_have_count(0)
    expect(dialog.get_by_label('复旦 eLearning', exact=True)).to_be_visible()
    expect(dialog.get_by_label('email (synthetic@example.com)', exact=True)).to_be_visible()
    assert not dialog.get_by_label('复旦 eLearning', exact=True).is_checked()
    assert not dialog.get_by_label('email (synthetic@example.com)', exact=True).is_checked()
    page.screenshot(path=str(artifacts / 'source-discovery.png'), animations='disabled')
    dialog.get_by_role('button', name='关闭', exact=True).click()
    # Granting a conversation tool does not change briefing source selection.
    saved = call('stellaSecretary/setMcpTools', {'request': {'expectedRevision': grants()['revision'], 'allowedTools': ['mcp__email__list_sources']}})
    assert saved['allowedTools'] == ['mcp__email__list_sources']
    chosen = call('stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': 'list_sources'}})
    assert not any(row['source'] in ('email', 'elearning') for row in chosen)


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-source-desktop')); artifacts.mkdir(parents=True, exist_ok=True)
    desktop.verify_release(); desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stella-source-desktop-') as temporary, sync_playwright() as playwright:
        home = Path(temporary); desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'; profile.mkdir(parents=True)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'source-bundle-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        baseline = (profile / 'cordis.patch.yml').read_bytes()
        desktop.native_cli(home, 'sources-install', 'add', str(PACK), *map(str, SOURCES), '--ignore-scripts')
        assert (profile / 'cordis.patch.yml').read_bytes() == baseline
        subprocess.run([str(ROOT.parent / 'MCP/email-mcp/.venv/bin/python'), str(ROOT / 'product/tests/source-protocol-check.py'), str(profile / 'node_modules')], check=True, timeout=60)
        with desktop.desktop(playwright, home, 'sources') as (page, errors):
            check(page, home, profile, artifacts)
            assert not errors, errors
            for name in ('@stella/elearning', '@stella/mail'):
                result = desktop.rpc(page, 'pluginManager/setBundleEnabled', {'name': name, 'enabled': False})
                assert result['application'] in ('applied', 'restart-required')
        with desktop.desktop(playwright, home, 'sources-disabled') as (page, errors):
            state = desktop.rpc(page, 'stellaSecretary/elearningAccount', {'request': {}})
            assert not state['installed']
            catalog = desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': 'discover_sources'}})
            assert not catalog['connections']
            grants = desktop.rpc(page, 'stellaSecretary/mcpState', {'request': {}})
            assert not grants['availableTools'] and grants['allowedTools'] == ['mcp__email__list_sources']
            for name in ('@stella/elearning', '@stella/mail'):
                desktop.rpc(page, 'pluginManager/setBundleEnabled', {'name': name, 'enabled': True})
            assert not errors, errors
        with desktop.desktop(playwright, home, 'sources-restored') as (page, errors):
            assert desktop.rpc(page, 'stellaSchool/elearningAccount', {'request': {}})['connected']
            config(page, '@stella/elearning')
            expect(page.get_by_label('学工号', exact=True)).to_have_value('synthetic-source-user')
            expect(page.get_by_label('eLearning 密码', exact=True)).to_have_value('')
            expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('placeholder', '********')
            expect(page.locator('[data-plugin-config] details')).to_have_count(0)
            state = desktop.rpc(page, 'stellaMail/state', {'request': {}})
            assert state['connected'] and len(state['accounts']) == 1
            config(page, '@stella/mail')
            expect(page.locator('[data-plugin-config] details')).to_have_count(0)
            expect(page.get_by_label('邮箱账号', exact=True)).to_have_value('synthetic@example.com')
            expect(page.get_by_label('邮箱授权码', exact=True)).to_have_value('')
            expect(page.get_by_label('邮箱授权码', exact=True)).to_have_attribute('placeholder', '********')
            page.get_by_role('button', name='移除邮箱', exact=True).click()
            page.get_by_role('button', name='确认移除邮箱', exact=True).click()
            expect(page.locator('[data-plugin-config]').get_by_role('status')).to_contain_text('邮箱已移除')
            assert not desktop.rpc(page, 'stellaMail/state', {'request': {}})['configured']
            assert 'synthetic-mail-secret' not in (home / 'dsh/.credentials.yaml').read_text()
            assert 'updated-mail-password' not in (home / 'dsh/.credentials.yaml').read_text()
            assert not errors, errors
        desktop.native_cli(home, 'sources-uninstall', 'remove', '@stella/elearning', '@stella/mail')
        assert 'synthetic-school-secret' in (home / 'dsh/.credentials.yaml').read_text()
        assert (profile / 'cordis.patch.yml').read_bytes() == baseline
    print('PASS: official Desktop source bundles, installed frozen protocols, configuration UI, native credentials, discovery without content reads, separate grants, disable/restart/restore/removal and config preservation')


if __name__ == '__main__': main()

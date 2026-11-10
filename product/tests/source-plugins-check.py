"""Official Desktop source bundles, credentials, discovery and lifecycle; no real accounts."""
import json
import os
import re
import subprocess
import tempfile
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import official_desktop_check as desktop
from plugin_check import ROOT, PACK
from web_check import open_secretary, settings

PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(PACK)))
SOURCES = []
for name in ('elearning', 'mail'):
    version = json.loads((ROOT / 'packages' / f'alcor-{name}' / 'package.json').read_text())['version']
    SOURCES.append(Path(os.environ.get('STELLA_SOURCE_REVIEW_DIRECTORY',str(ROOT/'.local/artifacts'))) / f'alcor-{name}-{version}.tgz')


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
    page.get_by_role('button', name='简报设置', exact=True).click()
    dialog = page.locator('[data-briefing-settings]')
    refresh = dialog.get_by_role('button', name='刷新来源', exact=True)
    refresh.click()
    expect(refresh).to_be_enabled()
    assert not refresh.inner_text().strip() and refresh.locator('svg').count() == 1
    return dialog


def check(page, home, profile, artifacts):
    call = lambda endpoint, args={}: desktop.rpc(page, endpoint, args)
    school = lambda: call('alcorSchool/elearningAccount', {'request': {}})
    mail = lambda: call('alcorMail/state', {'request': {}})
    assert school()['installed'] and not school()['configured']
    assert mail()['installed'] and not mail()['configured']
    open_secretary(page)
    settings(page)
    dialog = page.get_by_role('dialog', name=re.compile(r'^(Settings|设置)$'))
    expect(dialog.get_by_role('heading', name='当前空间的项目', exact=True)).to_be_visible()
    expect(dialog.get_by_text('连接', exact=True)).to_have_count(0)
    expect(dialog.get_by_text('权限与安全', exact=True)).to_have_count(0)
    expect(dialog.get_by_text('旧版身份导入', exact=True)).to_have_count(0)
    expect(dialog.get_by_text('Alcor 对话工具', exact=True)).to_have_count(0)
    expect(dialog.get_by_text('Stella 对话工具', exact=True)).to_have_count(0)
    expect(dialog.get_by_label('学工号', exact=True)).to_have_count(0)
    expect(dialog.get_by_label('DSH 文件工作区', exact=True)).to_have_count(0)
    expect(dialog.get_by_label('旧应用数据目录', exact=True)).to_have_count(0)
    page.screenshot(path=str(artifacts / 'stella-settings-desktop.png'), animations='disabled')
    page.set_viewport_size({'width':390,'height':844})
    expect(dialog.get_by_role('heading', name='当前空间的项目', exact=True)).to_be_in_viewport()
    assert page.locator('.alcor-settings-content').bounding_box()['width'] >= 250
    assert dialog.evaluate('element => element.scrollWidth <= element.clientWidth')
    page.screenshot(path=str(artifacts / 'stella-settings-mobile.png'), animations='disabled')
    page.set_viewport_size({'width':1440,'height':1000})
    dialog.get_by_role('button', name=re.compile(r'^(Close|关闭)$')).click()
    dialog = open_sources(page)
    for package in ('@alcor/elearning', '@alcor/mail'):
        row = dialog.locator('[data-source-card="'+('elearning' if package.endswith('elearning') else 'email')+'"]')
        expect(row).to_contain_text('待配置')
        expect(row).to_be_enabled()
        expect(row.get_by_role('checkbox')).to_have_count(0)
    page.screenshot(path=str(artifacts / 'source-setup-desktop.png'), animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(artifacts / 'source-setup-narrow.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    dialog.get_by_role('button', name='管理来源：eLearning', exact=True).click()
    page.locator('[data-plugin-detail="@alcor/elearning"] [data-plugin-config]').wait_for()
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
    for message in ('无法启动登录浏览器', '无法连接学校服务', '学校登录未完成'):
        page.get_by_role('button', name='测试连接', exact=True).click()
        expect(page.locator('[data-plugin-config]').get_by_role('status')).to_contain_text(message)
        expect(page.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
    page.screenshot(path=str(artifacts / 'school-check-error.png'), animations='disabled')
    assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(artifacts / 'school-narrow.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    dialog = open_sources(page)
    expect(dialog.locator('[data-source-card="elearning"]')).to_be_visible()
    expect(dialog.locator('[data-source-connection="@alcor/elearning"]')).to_have_count(0)
    dialog.get_by_role('button', name='管理来源：Email', exact=True).click()
    page.locator('[data-plugin-detail="@alcor/mail"] [data-plugin-config]').wait_for()
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
    chosen = lambda: call('alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': 'list_sources'}})
    assert any(row['source'] == 'email' for row in chosen()), 'An enabled mailbox must not require a second briefing subscription'
    expect(page.locator('[data-plugin-briefing="@alcor/mail"]')).to_have_count(0)
    first.get_by_role('button', name='停用邮箱', exact=True).click()
    expect(first.get_by_role('button', name='开启邮箱', exact=True)).to_be_enabled()
    expect(first.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
    assert not any(row['source'] == 'email' for row in chosen())
    first.get_by_role('button', name='开启邮箱', exact=True).click()
    expect(first.get_by_role('button', name='停用邮箱', exact=True)).to_be_enabled()
    assert any(row['source'] == 'email' for row in chosen())
    first.get_by_role('button', name='测试连接', exact=True).click()
    checking = first.get_by_role('button', name='正在测试…', exact=True)
    expect(checking).to_be_disabled()
    expect(checking).to_have_attribute('aria-busy', 'true')
    expect(page.locator('.alcor-mail-settings').get_by_role('alert')).to_contain_text('邮箱服务器拒绝登录')
    expect(first.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
    first.get_by_role('button', name='测试连接', exact=True).click()
    expect(first.get_by_role('button', name='正在测试…', exact=True)).to_be_disabled()
    expect(page.locator('.alcor-mail-settings').get_by_role('alert')).to_contain_text('测试连接超时')
    first.get_by_role('button', name='测试连接', exact=True).click()
    expect(first.get_by_role('button', name='正在测试…', exact=True)).to_be_disabled()
    expect(first.get_by_text('连接正常', exact=True)).to_be_visible()
    expect(page.locator('.alcor-mail-settings').get_by_role('alert')).to_have_count(0)
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
        result = call('alcorMail/save', {'request': {'expectedRevision': mail()['revision'], 'account': {
            'user': f'synthetic{index}@example.com', 'password': 'synthetic-only-pass', 'host': 'imap.example.com',
            'port': 993, 'folder': 'INBOX', 'enabled': False}}})
        extra_ids.append(next(r['id'] for r in result['accounts'] if r['user'] == f'synthetic{index}@example.com'))
    config(page, '@alcor/mail')
    page.get_by_role('button', name='刷新状态', exact=True).click()
    expect(page.get_by_role('button', name='添加邮箱', exact=True)).to_be_disabled()
    expect(page.locator('.alcor-mail-connection:visible')).to_have_count(1)
    page.get_by_label('搜索邮箱', exact=True).fill('synthetic18@')
    expect(page.locator('.alcor-mail-account-row')).to_have_count(1)
    page.get_by_role('button', name='选择邮箱：synthetic18@example.com', exact=True).click()
    expect(page.locator('.alcor-mail-connection:visible').get_by_label('邮箱账号', exact=True)).to_have_value('synthetic18@example.com')
    page.get_by_label('搜索邮箱', exact=True).fill('')
    page.get_by_role('button', name='选择邮箱：synthetic@example.com', exact=True).click()
    first.get_by_label('邮件文件夹', exact=True).fill('Draft kept locally')
    page.get_by_role('button', name='选择邮箱：synthetic18@example.com', exact=True).click()
    page.get_by_role('button', name='选择邮箱：synthetic@example.com', exact=True).click()
    expect(first.get_by_label('邮件文件夹', exact=True)).to_have_value('Draft kept locally')
    first.get_by_role('button', name='撤销修改', exact=True).click()
    page.locator('.alcor-mail-settings').screenshot(path=str(artifacts / 'mail-multi-desktop.png'), animations='disabled')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('.alcor-mail-settings').screenshot(path=str(artifacts / 'mail-multi-mobile.png'), animations='disabled')
    assert page.locator('.alcor-mail-settings').evaluate('element => element.scrollWidth <= element.clientWidth')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    for identity in extra_ids:
        call('alcorMail/removeAccount', {'request': {'id': identity, 'expectedRevision': mail()['revision']}})
    page.get_by_role('button', name='刷新状态', exact=True).click()
    credential_file = home / 'dsh/.credentials.yaml'
    assert credential_file.stat().st_mode & 0o777 == 0o600
    assert 'synthetic-mail-secret' not in (profile / 'cordis.patch.yml').read_text()
    for path in (profile / 'alcor').rglob('*'):
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
    expect(dialog.locator('[data-source-card="elearning"]')).to_be_visible()
    expect(dialog.locator('[data-source-card="email"]')).to_have_count(1)
    expect(dialog.get_by_role('checkbox')).to_have_count(0)
    expect(dialog.locator('[data-source-card="email"]')).to_contain_text('已开启')
    email_switch = dialog.get_by_role('switch', name='启用来源：Email', exact=True)
    account_flags = [(row['id'], row['enabled']) for row in mail()['accounts']]
    revision_before = mail()['revision']
    email_switch.click()
    expect(email_switch).to_have_attribute('aria-checked', 'false')
    assert not any(row['source'] == 'email' for row in call('alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': 'list_sources'}}))
    assert [(row['id'], row['enabled']) for row in mail()['accounts']] == account_flags
    assert mail()['revision'] == revision_before, 'Briefing switch must not rewrite plugin credentials'
    email_switch.click()
    expect(email_switch).to_have_attribute('aria-checked', 'true')
    page.screenshot(path=str(artifacts / 'source-discovery.png'), animations='disabled')
    dialog.get_by_role('button', name='返回简报', exact=True).click()
    chosen = call('alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': 'list_sources'}})
    assert {row['source'] for row in chosen} >= {'email', 'elearning'}


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/alcor-source-desktop')); artifacts.mkdir(parents=True, exist_ok=True)
    desktop.verify_release(); desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with fresh_test_home(prefix='alcor-source-desktop-') as temporary, sync_playwright() as playwright:
        home = Path(temporary); desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'; profile.mkdir(parents=True)
        initialize_fresh(temporary, home / 'dsh', profile)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'source-bundle-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        fixture = home / 'mail-check-fixture.mjs'
        fixture.write_text("""export const inject=['alcorMailConnection','tools'];
export function apply(ctx) {
  const execute=ctx.tools.execute;
  let schoolCount=0;
  ctx.tools.execute=async function(request){
    if(request.calls?.[0]?.id==='alcor-elearning-check') {
      const code=['browser_unavailable','network_error','login_timeout'][Math.min(schoolCount++,2)];
      return {results:[{result:{isError:true,error:{message:JSON.stringify({error_code:code})}}}]};
    }
    return execute.call(this,request);
  };
  ctx.effect(()=>()=>{ctx.tools.execute=execute});
  const prototype=Object.getPrototypeOf(ctx.alcorMailConnection);
  const original=prototype.check;
  const results=[{status:'query_failed',code:'auth_failed'},{status:'query_failed',code:'timeout'},{status:'ready'}];
  let count=0;
  prototype.check=async function(){await new Promise(resolve=>setTimeout(resolve,500));return results[Math.min(count++,results.length-1)]};
  ctx.effect(()=>()=>{prototype.check=original});
}
""")
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n- insert:\n    - id: synthetic-mail-check\n      name: {json.dumps(str(fixture))}\n')
        baseline = (profile / 'cordis.patch.yml').read_bytes()
        desktop.native_cli(home, 'sources-install', 'add', str(PACK), *map(str, SOURCES), '--ignore-scripts')
        assert (profile / 'cordis.patch.yml').read_bytes() == baseline
        subprocess.run([str(ROOT.parent / 'MCP/dsh-email/.venv/bin/python'), str(ROOT / 'product/tests/source-protocol-check.py'), str(profile / 'node_modules')], check=True, timeout=60)
        with desktop.desktop(playwright, home, 'sources') as (page, errors):
            check(page, home, profile, artifacts)
            assert not errors, errors
            for name in ('@alcor/elearning', '@alcor/mail'):
                result = desktop.rpc(page, 'pluginManager/setBundleEnabled', {'name': name, 'enabled': False})
                assert result['application'] in ('applied', 'restart-required')
        with desktop.desktop(playwright, home, 'sources-disabled') as (page, errors):
            state = desktop.rpc(page, 'alcorSecretary/elearningAccount', {'request': {}})
            assert not state['installed']
            catalog = desktop.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': 'discover_sources'}})
            assert not catalog['connections']
            for name in ('@alcor/elearning', '@alcor/mail'):
                desktop.rpc(page, 'pluginManager/setBundleEnabled', {'name': name, 'enabled': True})
            assert not errors, errors
        with desktop.desktop(playwright, home, 'sources-restored') as (page, errors):
            assert desktop.rpc(page, 'alcorSchool/elearningAccount', {'request': {}})['connected']
            config(page, '@alcor/elearning')
            expect(page.get_by_label('学工号', exact=True)).to_have_value('synthetic-source-user')
            expect(page.get_by_label('eLearning 密码', exact=True)).to_have_value('')
            expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('placeholder', '********')
            expect(page.locator('[data-plugin-config] details')).to_have_count(0)
            state = desktop.rpc(page, 'alcorMail/state', {'request': {}})
            assert state['connected'] and len(state['accounts']) == 1
            config(page, '@alcor/mail')
            expect(page.locator('[data-plugin-config] details')).to_have_count(0)
            expect(page.get_by_label('邮箱账号', exact=True)).to_have_value('synthetic@example.com')
            expect(page.get_by_label('邮箱授权码', exact=True)).to_have_value('')
            expect(page.get_by_label('邮箱授权码', exact=True)).to_have_attribute('placeholder', '********')
            page.get_by_role('button', name='移除邮箱', exact=True).click()
            page.get_by_role('button', name='确认移除邮箱', exact=True).click()
            expect(page.locator('[data-plugin-config]').get_by_role('status')).to_contain_text('邮箱已移除')
            assert not desktop.rpc(page, 'alcorMail/state', {'request': {}})['configured']
            assert 'synthetic-mail-secret' not in (home / 'dsh/.credentials.yaml').read_text()
            assert 'updated-mail-password' not in (home / 'dsh/.credentials.yaml').read_text()
            assert not errors, errors
        desktop.native_cli(home, 'sources-uninstall', 'remove', '@alcor/elearning', '@alcor/mail')
        assert 'synthetic-school-secret' in (home / 'dsh/.credentials.yaml').read_text()
        assert (profile / 'cordis.patch.yml').read_bytes() == baseline
        assert not (home / 'dsh/storages/stella_mcp.json').exists()
        assert not (home / 'dsh/storages/stella_workspace.json').exists()
    print('PASS: official Desktop source bundles, installed frozen protocols, configuration UI, native credentials, discovery without content reads, DSH-owned permissions, disable/restart/restore/removal and config preservation')


if __name__ == '__main__': main()

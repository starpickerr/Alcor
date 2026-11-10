import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from plugin_check import ROOT, CLI, PACK, launch
from web_check import rpc, stop, open_secretary


def main():
    apple_version = json.loads((ROOT / 'packages/alcor-apple-sync/package.json').read_text())['version']
    apple_pack = ROOT / f'.local/artifacts/alcor-apple-sync-{apple_version}.tgz'
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-organizer-plugin-artifacts'))
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stella-organizer-native-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        environment = {key: value for key, value in os.environ.items() if key in ('PATH', 'USER', 'LOGNAME', 'TMPDIR')}
        environment.update(HOME=str(home), DSH_HOME=str(home), DSH_TELEMETRY_DISABLED='1', ALCOR_EVENTKIT_HELPER=str(ROOT / 'product/tests/fake-eventkit.py'))
        profile = home / 'profiles/compatibility'
        profile.mkdir(parents=True)
        manifest = {'name': 'stella-apple-fixture', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}
        (profile / 'package.json').write_text(json.dumps(manifest))
        owner = f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n- id: session-telemetry-otel\n  disabled: true\n'
        (profile / 'cordis.patch.yml').write_text(owner)
        def install():
            with (home / 'install.log').open('w') as log:
                subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'add', str(PACK), str(apple_pack), '--ignore-scripts'], env=environment, cwd=home, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        install()
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_locator_handler(page.get_by_role('button', name='Continue', exact=True), lambda button: button.click())
        page.add_locator_handler(page.get_by_role('dialog', name='Get started', exact=True), lambda dialog: dialog.get_by_role('button', name='Close', exact=True).click())
        port = 0
        for phase in ('first', 'apple-disabled', 'apple-enabled', 'secretary-disabled', 'secretary-enabled', 'restart', 'remove', 'reinstall'):
            print('Native Apple UI phase:', phase, flush=True)
            with (home / f'{phase}.log').open('w') as log:
                process, url = launch(environment, log, port)
                port = int(url.split(':')[2].split('/')[0])
                try:
                    page.goto(url)
                    page.get_by_role('button', name='Account menu', exact=True).wait_for()
                    call = lambda method, args=None: rpc(page, url, 'alcorAppleSync/invoke', {'request': {'method': method, 'arguments': args or {}}})
                    business = lambda domain, method, args=None: rpc(page, url, 'alcorOrganizer/business', {'request': {'domain': domain, 'method': method, 'arguments': args or {}}})
                    if phase == 'first':
                        assert not call('status')['connected']
                        assert call('status')['supported']
                        page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness', exact=True).or_(page.get_by_role('button', name='切换使用模式，当前 Alcor', exact=True)).wait_for()
                        open_secretary(page)
                        page.get_by_role('button', name='Account menu', exact=True).click()
                        page.get_by_role('menuitem', name='Settings', exact=False).click()
                        settings = page.get_by_label('Settings', exact=True)
                        expect(settings.get_by_role('button', name='Apple 同步', exact=True)).to_have_count(0)
                        settings.get_by_role('button', name='Close', exact=True).click()
                        plugins = page.get_by_role('button', name='插件', exact=True)
                        (plugins if plugins.count() else page.get_by_role('button', name='Plugins', exact=True)).click()
                        page.get_by_role('heading', name='Plugins', exact=True).wait_for()
                        page.get_by_text('@alcor/apple-sync', exact=True).click()
                        page.get_by_role('heading', name='Apple 同步', exact=True).wait_for()
                        calendar = page.get_by_role('region', name='Apple 日历', exact=True)
                        reminders = page.get_by_role('region', name='Apple 提醒事项', exact=True)
                        for kind, region in (('calendar', calendar), ('reminders', reminders)):
                            expect(region.get_by_role('button')).to_have_count(2)
                            expect(region.get_by_role('checkbox')).to_have_count(0)
                            expect(region.get_by_text('未开启', exact=True)).to_be_visible()
                            expect(region.locator('.apple-sync-metadata')).to_have_count(0)
                            expect(region.get_by_text('尚未授权', exact=True)).to_have_count(0)
                            expect(region.get_by_text('同步全部', exact=False)).to_have_count(0)
                            expect(region.get_by_role('button', name='暂停同步', exact=True)).to_be_disabled()
                            expect(region.get_by_label('同步方向', exact=True)).to_have_count(0)
                            expect(region.get_by_role('button', name='保存连接', exact=True)).to_have_count(0)
                            expect(region.get_by_role('button', name='预览同步', exact=True)).to_have_count(0)
                            region.get_by_role('button', name='同步', exact=True).click()
                            expect(region.get_by_role('button', name='暂停同步', exact=True)).to_be_enabled()
                            expect(region.get_by_text('同步结果：', exact=False)).to_have_count(0)
                            expect(region.get_by_text('分类：Synthetic source', exact=True)).to_have_count(0)
                            state = call('status', {'kind': kind})
                            assert state['direction'] == 'bidirectional' and state['auto_enabled']
                            assert state['links'][0]['enabled'] and state['links'][0]['direction'] == 'bidirectional'
                            assert state['sync_all']
                            region.get_by_role('button', name='暂停同步', exact=True).click()
                            expect(region.get_by_role('button', name='暂停同步', exact=True)).to_be_disabled()
                            assert not call('status', {'kind': kind})['connected']
                            expect(region.get_by_text('已暂停', exact=True)).to_be_visible()
                            region.get_by_role('button', name='同步', exact=True).click()
                            expect(region.get_by_role('button', name='暂停同步', exact=True)).to_be_enabled()
                            expect(region.get_by_text('自动同步已开启', exact=True)).to_be_visible()
                            expect(region.get_by_text('上次成功同步', exact=True)).to_be_visible()
                            expect(region.locator('time')).to_have_text(re.compile(r'^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$'))
                        page.reload()
                        page.get_by_role('button', name='Account menu', exact=True).wait_for()
                        if not page.get_by_role('heading', name='Plugins', exact=True).count():
                            plugins = page.get_by_role('button', name='插件', exact=True)
                            (plugins if plugins.count() else page.get_by_role('button', name='Plugins', exact=True)).click()
                        page.get_by_text('@alcor/apple-sync', exact=True).click()
                        for region in (calendar, reminders):
                            expect(region.get_by_role('button', name='暂停同步', exact=True)).to_be_enabled()
                            expect(region.get_by_text('分类：Synthetic source', exact=True)).to_have_count(0)
                        page.screenshot(path=str(artifacts / 'apple-desktop.png'), full_page=True, animations='disabled')
                        page.locator('.apple-sync-settings').screenshot(path=str(artifacts / 'apple-config-desktop.png'), animations='disabled')
                        mobile(page)
                        assert page.locator('body').evaluate('element => element.scrollWidth <= element.clientWidth')
                        page.screenshot(path=str(artifacts / 'apple-mobile.png'), full_page=True, animations='disabled')
                        page.locator('.apple-sync-settings').screenshot(path=str(artifacts / 'apple-config-mobile.png'), animations='disabled')
                        page.set_viewport_size({'width': 1440, 'height': 1000})
                        if page.get_by_role('button', name='Close', exact=True).count():
                            page.get_by_role('button', name='Close', exact=True).click()
                        assert len(business('tasks', 'list_todos')) == 1
                        assert len(business('calendar', 'list_events')) == 1
                        assert business('tasks', 'list_todos')[0]['list_name'] == 'Synthetic source'
                        assert business('calendar', 'get_category', {'id': business('calendar', 'list_events')[0]['category_id']})['name'] == 'Synthetic source'
                        check_tasks(page, business, artifacts)
                        check_calendar(page, business, artifacts)
                        result = rpc(page, url, 'pluginManager/setBundleEnabled', {'name': '@alcor/apple-sync', 'enabled': False})
                        assert result['application'] in ('applied', 'restart-required'), result
                        print('Apple disable:', result['application'], flush=True)
                    elif phase in ('apple-disabled', 'secretary-disabled'):
                        response = page.request.post(url.split('/?')[0] + '/api/alcorAppleSync/invoke', data={'request': {'method': 'status'}})
                        assert response.status == 404 or not response.json().get('result', {}).get('ok', False)
                        if phase == 'apple-disabled':
                            result = rpc(page, url, 'pluginManager/setBundleEnabled', {'name': '@alcor/apple-sync', 'enabled': True})
                            assert result['application'] in ('applied', 'restart-required'), result
                    elif phase == 'apple-enabled':
                        assert call('status')['connected']
                    elif phase == 'secretary-enabled':
                        assert call('status')['connected']
                    elif phase == 'restart':
                        assert call('status')['connected']
                        for kind in ('calendar', 'reminders'):
                            state = call('status', {'kind': kind})
                            assert state['direction'] == 'bidirectional' and state['auto_enabled']
                            assert state['links'][0]['stella_name'] == 'Synthetic source'
                        assert len([row for row in business('tasks', 'list_todos') if row['title'] == 'Synthetic Apple task']) == 1
                        call('disconnect')
                        assert not call('status')['connected']
                        result = rpc(page, url, 'pluginManager/setBundleEnabled', {'name': '@alcor/apple-sync', 'enabled': False})
                        assert result['application'] in ('applied', 'restart-required'), result
                    elif phase == 'remove':
                        result = rpc(page, url, 'pluginManager/removeBundle', {'name': '@alcor/apple-sync'})
                        assert result['application'] in ('applied', 'restart-required'), result
                        assert any(row['title'] == 'Synthetic Apple task' for row in business('tasks', 'list_todos'))
                        assert (profile / 'alcor/apple-sync/apple-calendar.sqlite3').exists()
                    else:
                        assert not call('status')['connected']
                        assert len([row for row in business('tasks', 'list_todos') if row['title'] == 'Synthetic Apple task']) == 1
                        assert (profile / 'cordis.patch.yml').read_text().startswith(owner)
                    assert not errors, errors
                except Exception:
                    page.screenshot(path=str(artifacts / 'failure.png'), full_page=True, animations='disabled')
                    print(page.locator('body').inner_text()[-14000:])
                    print('Browser errors:', errors)
                    raise
                finally:
                    stop(process)
                    (artifacts / f'{phase}.log').write_text(Path(log.name).read_text())
            if phase == 'remove':
                install()
            if phase in ('apple-enabled', 'secretary-disabled'):
                # Secretary's root UI lifecycle is supported across a Host restart, not hot replacement.
                module = CLI.parents[2] / 'dsh-app-boot/lib/index.js'
                script = f'''import {{readProfileManifest, writeProfileBundles}} from {json.dumps(module.as_uri())};
                  const directory={json.dumps(str(profile))};
                  const manifest=readProfileManifest('dsh', directory);
                  const previous=manifest.dsh.profile.bundles;
                  const name='@alcor/secretary';
                  const bundles={str(phase == 'secretary-disabled').lower()} ? [...new Set([...previous,name])] : previous.filter(item => item!==name);
                  writeProfileBundles(directory, manifest, bundles);'''
                with (home / 'bundle-selection.log').open('w') as log:
                    subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'exec', 'node', '--input-type=module', '-e', script], env=environment, cwd=home, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        context.close()
        browser.close()
    print('PASS: Apple native installation, settings, shared records, disable/dependency stop/restart/uninstall/reinstall; task hierarchy and calendar backend features in real UI; synthetic data only')


def check_tasks(page, business, artifacts):
    open_secretary(page)
    page.get_by_role('button', name='新建事务', exact=True).click()
    form = page.get_by_role('form', name='新增事务', exact=True)
    assert form.locator('.alcor-field > span:first-child').all_text_contents()[:8] == ['标题', '事务详情', '截止日期', '截止时间', '优先级', '标签', '事务清单', '频率']
    for label in ['事务时区', '计划日期', '提醒时间', '清单', '重复频率', '标记为重要']:
        expect(form.get_by_label(label, exact=True)).to_have_count(0)
    expect(form.get_by_text('提醒时间使用本机时区', exact=False)).to_have_count(0)
    form.get_by_label('标题', exact=True).fill('Project parent')
    form.get_by_label('标签', exact=True).fill('python, homework')
    form.get_by_label('截止日期', exact=True).fill('2026-12-25')
    form.get_by_label('事务详情', exact=True).fill('Read requirements, prepare the proposal and bring the final document.')
    form.get_by_label('截止时间', exact=True).fill('14:00')
    form.get_by_label('优先级', exact=True).select_option('2')
    expect(form.get_by_role('button', name='添加检查项', exact=True)).to_have_count(0)
    form.get_by_role('button', name='保存事务', exact=True).click()
    expect(form).not_to_be_visible()
    saved = next(row for row in business('tasks', 'list_todos') if row['title'] == 'Project parent')
    system_zone = page.evaluate('Intl.DateTimeFormat().resolvedOptions().timeZone')
    assert saved['timezone'] == system_zone
    legacy_zone = 'America/New_York' if system_zone != 'America/New_York' else 'Asia/Shanghai'
    legacy = [{'id': 'old-checklist', 'title': 'Legacy checklist retained', 'completed': False}]
    business('tasks', 'update_todo', {'todo_id': saved['id'], 'payload': {'checklist': legacy, 'timezone': legacy_zone, 'list_name': 'Course work', 'planned_on': '2026-12-01', 'flagged': True, 'remind_at': '2099-12-25T05:00:00+00:00'}})
    page.get_by_role('button', name='刷新', exact=True).click()
    card = page.locator(f'[data-task-id="{saved["id"]}"]')
    expect(card.get_by_role('button', name='编辑事务', exact=True)).to_have_count(0)
    trash = card.get_by_role('button', name='更多操作：Project parent', exact=True)
    assert not trash.inner_text().strip() and trash.locator('svg').count() == 1
    assert card.evaluate('e => getComputedStyle(e).borderBottomStyle !== "none"')
    assert page.locator('.alcor-task-tabs').evaluate('e => Math.abs(e.getBoundingClientRect().right - document.querySelector(".alcor-agent-strip").getBoundingClientRect().right) < 2')
    page.screenshot(path=str(artifacts / 'tasks-list-desktop.png'), animations='disabled')
    card.click(position={'x': card.bounding_box()['width'] * .65, 'y': 18})
    expect(page.locator('.alcor-task-description')).to_have_text('Read requirements, prepare the proposal and bring the final document.')
    expect(page.get_by_role('heading', name='前置事务', exact=True)).to_have_count(0)
    expect(page.get_by_label('选择前置事务', exact=True)).to_have_count(0)
    expect(page.get_by_role('heading', name='检查清单', exact=True)).to_have_count(0)
    expect(page.get_by_label('Legacy checklist retained', exact=True)).to_have_count(0)
    expect(page.locator('.alcor-task-detail-summary .alcor-task-badge[data-status]')).to_have_count(0)
    expect(page.get_by_role('region', name='事务详情', exact=True)).to_contain_text('2026年12月25日')
    expect(page.get_by_role('region', name='事务详情', exact=True)).to_contain_text('14:00')
    page.get_by_role('button', name='编辑详情', exact=True).click()
    editor = page.get_by_role('form', name='编辑事务', exact=True)
    expect(editor.get_by_text('检查清单', exact=True)).to_have_count(0)
    expect(editor.get_by_label('事务时区', exact=True)).to_have_count(0)
    expect(editor.get_by_label('事务详情', exact=True)).to_have_value('Read requirements, prepare the proposal and bring the final document.')
    editor.get_by_label('标题', exact=True).focus()
    page.screenshot(path=str(artifacts / 'task-editor-desktop.png'), animations='disabled')
    mobile(page)
    assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
    page.screenshot(path=str(artifacts / 'task-editor-mobile.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    editor.get_by_role('button', name='保存事务', exact=True).click()
    expect(editor).not_to_be_visible()
    updated = business('tasks', 'get_todo', {'todo_id': saved['id']})
    assert json.loads(updated['checklist']) == legacy
    assert updated['list_name'] == 'Course work' and updated['planned_on'] == '2026-12-01' and updated['flagged']
    assert updated['remind_at'] == '2099-12-25T05:00:00+00:00'
    assert updated['timezone'] == legacy_zone
    assert updated['due_on'] == '2026-12-25' and updated['due_time'] == '14:00'
    assert json.loads(updated['tags']) == ['python', 'homework']

    page.get_by_role('region', name='事务详情', exact=True).get_by_role('button', name='添加子事务', exact=True).click()
    page.get_by_label('子事务标题', exact=True).fill('First step')
    page.get_by_role('button', name='添加子事务', exact=True).click()
    page.get_by_role('button', name='First step', exact=True).click()
    expect(page.get_by_role('navigation', name='事务路径')).to_contain_text('Project parent')
    page.get_by_role('navigation', name='事务路径').get_by_role('button', name='Project parent', exact=True).click()
    expect(page.get_by_role('heading', name='检查清单', exact=True)).to_have_count(0)
    page.locator('.alcor-task-children').get_by_role('button', name='完成事务', exact=True).click()
    page.get_by_text('子事务进度 1/1', exact=False).wait_for()
    page.screenshot(path=str(artifacts / 'tasks-desktop.png'), full_page=True, animations='disabled')
    mobile(page)
    assert page.locator('body').evaluate('element => element.scrollWidth <= element.clientWidth')
    page.screenshot(path=str(artifacts / 'tasks-mobile.png'), full_page=True, animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.get_by_role('button', name='关闭详情', exact=True).click()
    page.get_by_role('navigation', name='事务清单').get_by_role('button', name='Course work', exact=True).click()
    expect(page.get_by_role('button', name='Synthetic Apple task', exact=True)).to_have_count(0)
    page.get_by_role('navigation', name='事务清单').get_by_role('button', name='全部清单', exact=True).click()
    assert next(row for row in business('tasks', 'list_todos') if row['title'] == 'First step')['status'] == 'completed'
    page.get_by_role('button', name='Project parent', exact=True).focus()
    page.keyboard.press('Enter')
    expect(page.get_by_role('button', name='关闭详情', exact=True)).to_be_visible()
    page.get_by_role('button', name='关闭详情', exact=True).click()
    mobile(page)
    assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
    card.get_by_role('button', name='更多操作：Project parent', exact=True).click(trial=True)
    page.screenshot(path=str(artifacts / 'tasks-list-mobile.png'), full_page=True, animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    trash.click()
    page.get_by_role('menuitem', name='删除事务', exact=True).click()
    dialog = page.get_by_role('dialog', name='删除事务', exact=True)
    expect(dialog).to_contain_text('子事务')
    dialog.get_by_role('button', name='取消', exact=True).click()
    expect(card).to_be_visible()
    expect(page.locator('.alcor-task-detail')).to_have_count(0)
    trash.click()
    page.get_by_role('menuitem', name='删除事务', exact=True).click()
    dialog.get_by_role('button', name='确认删除', exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(card).to_have_count(0)
    assert not any(row['title'] in ('Project parent', 'First step') for row in business('tasks', 'list_todos'))
    page.get_by_role('button', name='新建事务', exact=True).click()
    repeat_form = page.get_by_role('form', name='新增事务', exact=True)
    repeat_form.get_by_label('标题', exact=True).fill('Custom weekly study')
    repeat_form.get_by_label('截止日期', exact=True).fill('2026-11-02')
    repeat_form.get_by_label('频率', exact=True).select_option('custom')
    repeat_form.get_by_label('重复间隔', exact=True).fill('2')
    repeat_form.get_by_label('重复单位', exact=True).select_option('weekly')
    repeat_form.get_by_label('周一', exact=True).check()
    repeat_form.get_by_label('周三', exact=True).check()
    repeat_form.get_by_label('结束日期', exact=True).fill('2027-12-31')
    page.screenshot(path=str(artifacts / 'task-frequency-desktop.png'), full_page=True, animations='disabled')
    repeat_form.get_by_role('button', name='保存事务', exact=True).click()
    expect(repeat_form).not_to_be_visible()
    custom = next(r for r in business('tasks','list_todos') if r['title'] == 'Custom weekly study')
    assert custom['repeat_interval'] == 2 and json.loads(custom['repeat_days']) == [0,2]
    page.get_by_role('button', name='Custom weekly study', exact=True).click()
    expect(page.get_by_label('当前事务状态', exact=True)).to_have_count(0)
    expect(page.get_by_label('状态筛选', exact=True)).to_have_count(0)
    page.get_by_role('button', name='编辑详情', exact=True).click()
    repeat_form = page.get_by_role('form', name='编辑事务', exact=True)
    expect(repeat_form.get_by_label('频率', exact=True)).to_have_value('custom')
    expect(repeat_form.get_by_label('重复间隔', exact=True)).to_have_value('2')
    mobile(page)
    page.screenshot(path=str(artifacts / 'task-frequency-mobile.png'), full_page=True, animations='disabled')
    repeat_form.get_by_role('button', name='取消编辑', exact=True).click()
    page.get_by_role('button', name='关闭详情', exact=True).click()
    business('tasks', 'delete_todo', {'todo_id': custom['id']})
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.get_by_role('button', name='刷新', exact=True).click()
    # All tasks show active and completed history immediately without disclosure controls.
    today = page.evaluate("new Date().toISOString().slice(0, 10)")
    tomorrow = page.evaluate("new Date(Date.now() + 86400000).toISOString().slice(0, 10)")
    daily = business('tasks', 'create_todo', {'payload': {'title': 'Daily study', 'due_on': today, 'repeat_frequency': 'daily', 'timezone': 'UTC'}})
    past = [business('tasks', 'create_todo', {'payload': {'title': 'Daily study', 'status': 'completed', 'due_on': day}}) for day in ('2026-04-17', '2026-05-13')]
    page.get_by_role('button', name='刷新', exact=True).click()
    expect(page.get_by_role('tab', name=re.compile(r'^全部事务'))).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_role('button', name='Daily study', exact=True)).to_have_count(3)
    expect(page.locator('.stella-task-history')).to_have_count(0)
    expect(page.locator('.alcor-tasks-page details')).to_have_count(0)
    daily_id = daily['id']
    daily_card = page.locator(f'[data-task-id="{daily_id}"]:visible').filter(has=page.get_by_role('button', name='完成本次', exact=True))
    daily_card.get_by_role('button', name='完成本次', exact=True).click()
    expect(daily_card.get_by_role('button', name='完成本次', exact=True)).to_be_visible()
    expect(daily_card.locator('time')).to_have_text(tomorrow)
    assert next(row for row in business('tasks', 'list_todos', {'start': today, 'end': tomorrow}) if row.get('series_id') == daily['id'])['status'] == 'completed'
    expect(page.get_by_role('button', name='Daily study', exact=True)).to_have_count(4)
    expect(page.get_by_role('tab', name=re.compile(r'^全部事务'))).to_have_attribute('aria-selected', 'true')
    page.screenshot(path=str(artifacts / 'tasks-repeat-desktop.png'), full_page=True, animations='disabled')
    mobile(page)
    assert page.locator('body').evaluate('element => element.scrollWidth <= element.clientWidth')
    page.screenshot(path=str(artifacts / 'tasks-repeat-mobile.png'), full_page=True, animations='disabled')
    for item in [daily, *past]:
        business('tasks', 'delete_todo', {'todo_id': item['id']})
    page.set_viewport_size({'width': 1440, 'height': 1000})




def check_calendar(page, business, artifacts):
    open_secretary(page)
    page.get_by_role('button', name='日程', exact=True).click()
    page.get_by_role('button', name='分类管理', exact=True).click()
    dialog = page.get_by_role('dialog', name='分类管理', exact=True)
    dialog.get_by_role('button', name='新建分类', exact=True).click()
    dialog.get_by_label('分类名称', exact=True).fill('UI calendar')
    dialog.get_by_label('分类颜色', exact=True).fill('#00aacc')
    dialog.get_by_role('button', name='保存分类', exact=True).click()
    dialog.get_by_role('button', name='UI calendar', exact=True).wait_for()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='新建', exact=True).click()
    page.get_by_role('menuitem', name='新建日程', exact=True).click()
    form = page.get_by_role('form', name='新增日程', exact=True)
    form.get_by_label('标题', exact=True).fill('Repeated lesson')
    form.get_by_label('开始', exact=True).fill('2026-11-05T09:00')
    form.get_by_label('结束', exact=True).fill('2026-11-05T12:30')
    form.get_by_label('日程分类', exact=True).select_option(label='UI calendar')
    form.get_by_label('地点', exact=True).fill('Room 101')
    form.locator('summary').filter(has_text='重复与时区').click()
    form.get_by_label('重复频率', exact=True).select_option('daily')
    form.get_by_label('重复截止日期', exact=True).fill('2026-11-07')
    form.get_by_role('button', name='保存日程', exact=True).click()
    expect(form).not_to_be_visible()
    page.get_by_role('tab', name='列表', exact=True).click()
    page.locator('.alcor-agenda-date').filter(has_text='Repeated lesson').first.wait_for()
    assert page.get_by_role('button', name='跳过本次', exact=True).count() == 3
    page.get_by_role('button', name='跳过本次', exact=True).first.click()
    page.get_by_role('button', name='确认跳过', exact=True).click()
    expect(page.get_by_role('button', name='跳过本次', exact=True)).to_have_count(2)
    expect(page.get_by_role('button', name='导入日期', exact=True)).to_have_count(0)
    # The existing import contract and records remain compatible after removing the UI entry.
    assert business('calendar', 'import_china_holidays', {'year': 2026})['total'] > 0
    business('calendar', 'create_special_date', {'title': 'Birthday', 'date': '2026-11-06', 'annual': True, 'timezone': 'Asia/Shanghai'})
    assert business('calendar', 'import_special_dates', {'content': 'title,date,annual\nCSV anniversary,2026-11-08,true', 'format': 'csv', 'timezone': 'Asia/Shanghai'})['added'] == 1
    page.get_by_role('button', name='刷新', exact=True).click()
    page.get_by_role('button', name='隐藏全部日历', exact=True).click(); page.get_by_role('checkbox', name='UI calendar', exact=True).check()
    expect(page.get_by_role('heading', name='Birthday', exact=True)).to_have_count(0)
    page.get_by_role('button', name='显示全部日历', exact=True).click()
    for label, slug in [('日', 'day'), ('周', 'week'), ('月', 'month'), ('年', 'year'), ('列表', 'list')]:
        page.get_by_role('tab', name=label, exact=True).click()
        page.locator(f'#calendar-view-{slug}-panel').wait_for()
        expect(page.get_by_text('正在读取日程…', exact=True)).to_have_count(0)
        page.get_by_role('button', name={'day': '今天', 'week': '本周', 'month': '本月', 'year': '今年', 'list': '今年'}[slug], exact=True).wait_for()
        expect(page.locator('.alcor-calendar-sidebar')).to_be_visible()
        if slug in ('day', 'week'):
            viewport = page.locator('.alcor-time-viewport')
            expect(viewport).to_be_visible()
            assert viewport.evaluate('element => element.scrollTop') > 0
            lesson = page.locator('.alcor-time-column .alcor-timed-event').filter(has_text='Repeated lesson').first
            assert lesson.evaluate("element => element.querySelector('strong').getBoundingClientRect().top - element.getBoundingClientRect().top") < 12
            assert lesson.evaluate("element => getComputedStyle(element).borderLeftColor") == 'rgb(0, 170, 204)'
            assert lesson.evaluate("element => getComputedStyle(element).backgroundColor") != 'rgb(255, 255, 255)'
            viewport.evaluate('element => element.scrollTop = 0')
            today = page.evaluate('new Date().toLocaleDateString("en-CA")')
            assert page.get_by_role('button', name=f'{today} 00:00 新建日程', exact=True).is_visible()
            page.get_by_role('button', name='隐藏全部日历', exact=True).click(); page.get_by_role('checkbox', name='UI calendar', exact=True).check()
            assert viewport.evaluate('element => element.scrollTop') > 0
            page.get_by_role('button', name='显示全部日历', exact=True).click()
        if slug == 'month':
            chip = page.locator('.alcor-calendar-chip').filter(has_text='Repeated lesson').first
            assert chip.evaluate("element => getComputedStyle(element).borderLeftColor") == 'rgb(0, 170, 204)'
        page.screenshot(path=str(artifacts / f'calendar-{slug}-desktop.png'), full_page=True, animations='disabled')
    page.get_by_role('tab', name='月', exact=True).click()
    page.locator('.alcor-calendar-surface').get_by_role('button', name='2026-11-06', exact=True).click()
    expect(page.get_by_role('tab', name='日', exact=True)).to_have_attribute('aria-selected', 'true')
    page.get_by_role('tab', name='周', exact=True).click()
    page.set_viewport_size({'width': 1028, 'height': 900})
    expect(page.locator('.alcor-time-scroll')).to_have_js_property('scrollLeft', 0)
    mobile(page)
    assert page.locator('body').evaluate('element => element.scrollWidth <= element.clientWidth')
    assert page.locator('.alcor-time-scroll').evaluate('element => element.scrollLeft') > 0
    page.locator('.alcor-time-head > button').first.click()
    expect(page.locator('.alcor-time-scroll')).to_have_js_property('scrollLeft', 0)
    page.locator('.alcor-time-head > button').nth(5).click()
    assert page.locator('.alcor-time-scroll').evaluate('element => element.scrollLeft') > 0
    page.screenshot(path=str(artifacts / 'calendar-week-mobile.png'), full_page=True, animations='disabled')
    page.get_by_role('tab', name='月', exact=True).click()
    assert page.locator('body').evaluate('element => element.scrollWidth <= element.clientWidth')
    page.screenshot(path=str(artifacts / 'calendar-month-mobile.png'), full_page=True, animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    categories = business('calendar', 'categories')
    category = next(row for row in categories if row['name'] == 'UI calendar')
    result = business('calendar', 'list_events', {'start': '2026-11-05T00:00:00+08:00', 'end': '2026-11-08T00:00:00+08:00', 'category_id': category['id']})
    assert len(result) == 2


def mobile(page):
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('[data-sidebar-opener]').wait_for(state='visible')
    drawer = page.get_by_role('dialog', name='Alcor 秘书导航', exact=True)
    if drawer.is_visible():
        page.keyboard.press('Escape')
        expect(drawer).not_to_be_visible()


if __name__ == '__main__':
    main()

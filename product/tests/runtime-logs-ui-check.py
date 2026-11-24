"""Runtime log page in an unchanged official Desktop, using disposable data."""
import re
from pathlib import Path
import official_desktop_check as d
from fresh_profile_fixture import fresh_test_home
from playwright.sync_api import expect, sync_playwright

d.ARTIFACTS.mkdir(parents=True, exist_ok=True)
d.verify_release()
with fresh_test_home('alcor-runtime-logs-') as temp, sync_playwright() as pw:
    home = Path(temp)
    with d.desktop(pw, home, 'logs-baseline'):
        pass
    d.native_cli(home, 'logs-install', 'add', str(d.PACK), '--ignore-scripts')
    with d.desktop(pw, home, 'logs') as (page, errors):
        page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness').click()
        page.get_by_role('menuitem', name='Alcor', exact=True).click()
        system = page.get_by_role('group', name='系统', exact=True)
        system.get_by_role('button', name='日志', exact=True).click()
        expect(page.get_by_role('heading', name='运行日志', exact=True)).to_be_visible()
        records = page.get_by_role('list', name='日志记录', exact=True)
        expect(records.locator('summary').filter(has_text='Alcor 日志采集已启动。')).to_be_visible()
        refresh = page.get_by_role('button', name='刷新', exact=True)
        expect(refresh).to_have_count(1)
        expect(refresh.locator('svg')).to_have_count(1)
        expect(page.get_by_role('button', name=re.compile('暂停刷新|继续刷新'))).to_have_count(0)
        header = page.get_by_role('group', name='日志表头', exact=True)
        source_filter = header.get_by_label('来源', exact=True)
        time_filter = header.get_by_label('时间', exact=True)
        category = header.get_by_role('combobox', name='类别', exact=True)
        expect(header.get_by_role('combobox')).to_have_count(3)
        expect(header.get_by_text('耗时', exact=True)).to_have_count(0)
        for source in ['Alcor：事务', 'Alcor：日程', 'Alcor：简报', 'Alcor：账单', 'Alcor：自动化', 'Apple 同步', 'eLearning', '邮件']:
            expect(source_filter.get_by_role('option', name=source, exact=True)).to_have_count(1)
        d.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'list_todos', 'arguments': {}}})
        refresh.click()
        page.get_by_role('searchbox', name='搜索', exact=True).fill('tasks.list_todos')
        category.select_option('all')
        recent = records.locator('summary').filter(has_text='tasks.list_todos').first
        expect(recent.locator('.alcor-log-category')).to_have_text('调试')
        assert '[worker_operation_succeeded]' not in records.locator('summary').first.inner_text()
        recent.click()
        expect(records.locator('dl').get_by_text('耗时', exact=True)).to_be_visible()
        expect(records.locator('dl')).to_contain_text(re.compile(r'\d+ ms'))
        expect(category.locator('option')).to_have_count(6)
        expect(category.get_by_role('option', name='错误', exact=True)).to_have_count(0)
        expect(category.get_by_role('option', name='调试', exact=True)).to_have_count(0)
        category.select_option('normal')
        expect(page.locator('.alcor-log-empty')).to_contain_text('没有符合条件的日志')
        page.get_by_role('searchbox', name='搜索', exact=True).fill('')
        d.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'create_todo', 'arguments': {'payload': {'title': 'Synthetic log success'}}}})
        refresh.click()
        category.select_option('success')
        expect(records.locator('li > details > summary').filter(has_text='tasks.create_todo')).to_be_visible()
        # A failing read in this temporary profile emits a safe worker diagnostic.
        try:
            d.rpc(page, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'get_todo', 'arguments': {'todo_id': 'synthetic-missing'}}})
        except AssertionError:
            pass
        else:
            raise AssertionError('Synthetic missing item unexpectedly succeeded')
        page.get_by_role('button', name='刷新', exact=True).click()
        category.select_option('failure')
        expect(records.locator('summary').filter(has_text='tasks.get_todo').filter(has_text='失败')).to_be_visible()
        source_filter.select_option(label='Alcor：事务')
        time_filter.select_option('1')
        search = page.get_by_role('searchbox', name='搜索', exact=True)
        search.fill('tasks.get_todo')
        expect(records.locator('li')).to_have_count(1)
        records.locator('li > details > summary').click()
        expect(records.get_by_text('todo not found', exact=True)).to_be_visible()
        expect(records.locator('section pre')).to_contain_text(re.compile(r'tasks/domain/service\.py:\d+ in get_todo'))
        expect(records.locator('dl')).to_contain_text('KeyError')
        expect(records.locator('dl')).to_contain_text('not_found')
        records.get_by_text('原始记录', exact=True).click()
        expect(records.locator('.alcor-log-raw pre')).to_contain_text('worker_operation_failed')
        expect(records.locator('dl')).to_contain_text('日志级别')
        expect(records.locator('dl')).to_contain_text(re.compile(r'\d+ ms'))
        page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        page.get_by_role('button', name='复制当前结果', exact=True).click()
        expect(page.get_by_role('status').filter(has_text='已复制')).to_be_visible()
        assert 'tasks/domain/service.py:' in page.evaluate('navigator.clipboard.readText()')
        page.screenshot(path=str(d.ARTIFACTS / 'runtime-logs-failure.png'), scale='css')
        search.fill('synthetic-no-matches')
        expect(page.locator('.alcor-log-empty')).to_contain_text('没有符合条件的日志')
        reset = header.get_by_role('button', name='重置筛选', exact=True)
        expect(page.get_by_role('button', name='重置筛选', exact=True)).to_have_count(1)
        reset.click()
        expect(reset).to_have_count(0)
        expect(search).to_have_value('')
        expect(category).to_have_value('normal')
        expect(time_filter).to_have_value('all')
        expect(source_filter).to_have_value('')
        expect(records).to_be_visible()
        refresh.click()
        expect(records).to_be_visible()
        page.screenshot(path=str(d.ARTIFACTS / 'runtime-logs-desktop.png'), scale='css')
        expect(page.get_by_text('采集范围与保留', exact=True)).to_have_count(0)
        expect(page.get_by_text(re.compile('不包含采集启动前'))).to_have_count(0)
        for width in (720, 390):
            page.set_viewport_size({'width': width, 'height': 700})
            page.evaluate('''async () => {
              await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
              await Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {})));
            }''')
            page.locator('.alcor-logs').evaluate('element => { element.scrollTop = 0 }')
            expect(page.get_by_role('heading', name='运行日志', exact=True)).to_be_visible()
            expect(time_filter).to_be_visible()
            expect(source_filter).to_be_visible()
            for control, selector in [(category, '.alcor-log-category'), (source_filter, '.alcor-log-source'), (time_filter, 'time')]:
                control_box = control.bounding_box()
                cell_box = records.locator('li').first.locator('summary').first.locator(selector).bounding_box()
                assert abs(control_box['x'] - cell_box['x']) <= 1, (width, control_box, cell_box)
                if selector == '.alcor-log-source':
                    assert control_box['width'] <= cell_box['width'] + 1, (width, control_box, cell_box)
                else:
                    assert control_box['width'] < 75, (width, control_box)
            assert page.locator('.alcor-logs').evaluate('e => e.scrollWidth <= e.clientWidth'), width
            leading = page.locator('[data-shell-leading]')
            if leading.is_visible():
                a, b = leading.bounding_box(), page.get_by_role('heading', name='运行日志', exact=True).bounding_box()
                assert a['y'] + a['height'] <= b['y'] or a['x'] + a['width'] <= b['x'], (a, b)
            category.select_option('failure')
            expect(reset).to_be_visible()
            reset_box = reset.bounding_box()
            source_box = source_filter.bounding_box()
            assert reset_box['x'] >= source_box['x'] + source_box['width'], width
            assert abs(reset_box['y'] + reset_box['height']/2 - source_box['y'] - source_box['height']/2) < 2, width
            page.screenshot(path=str(d.ARTIFACTS / f'runtime-logs-{width}.png'), scale='css')
            reset.click()
            expect(category).to_have_value('normal')
        page.set_viewport_size({'width': 1280, 'height': 900})
        page.get_by_role('button', name='切换使用模式，当前 Alcor').click()
        page.get_by_role('menuitem', name='DeepSeek Harness', exact=True).click()
        page.get_by_role('button', name='切换使用模式，当前 DeepSeek Harness').click()
        page.get_by_role('menuitem', name='Alcor', exact=True).click()
        expect(page.get_by_role('heading', name='运行日志', exact=True)).to_be_visible()
        assert not errors, errors
    with d.desktop(pw, home, 'logs-restart') as (page, errors):
        expect(page.get_by_role('heading', name='运行日志', exact=True)).to_be_visible(timeout=30000)
        page.get_by_role('searchbox', name='搜索', exact=True).fill('worker_operation_failed')
        expect(page.get_by_role('list', name='日志记录').locator('summary').filter(has_text='tasks.get_todo')).to_be_visible()
        assert not errors, errors
print('PASS: official Desktop log navigation, filters, detail, copy, responsive layout and restart')

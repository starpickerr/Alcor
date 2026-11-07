"""Bills screenshots/workflows on a packed native plugin with synthetic data."""
import json
import os
import runpy
import subprocess
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from plugin_check import ROOT, CLI, PACK
from web_check import open_secretary
import official_desktop_check as desktop


surface = runpy.run_path(str(ROOT / 'product/tests/bill-import-chat-check.py'))['surface']


def check_composition(page, name, category, amount, share):
    composition = page.get_by_role('region', name=name, exact=True)
    segment = composition.locator('.stella-bill-segment[data-category="' + category + '"]')
    segment.scroll_into_view_if_needed()
    point = segment.evaluate('path => { const p = path.getPointAtLength(path.getTotalLength() / 2); const screen = new DOMPoint(p.x, p.y).matrixTransform(path.getScreenCTM()); return {x:screen.x,y:screen.y}; }')
    page.mouse.move(point['x'], point['y'])
    tooltip = composition.get_by_role('tooltip')
    expect(tooltip).to_contain_text(category)
    expect(tooltip).to_contain_text(amount)
    expect(tooltip).to_contain_text('占比 ' + share)
    expect(segment).to_have_attribute('data-active', 'true')
    page.wait_for_function('element => new DOMMatrix(getComputedStyle(element).transform).a > 1.06', arg=segment.element_handle())
    composition.get_by_role('heading').hover()
    expect(tooltip).to_have_count(0)
    button = composition.get_by_role('button', name=category + '，', exact=False)
    button.focus()
    expect(tooltip).to_contain_text(amount)
    expect(segment).to_have_attribute('data-active', 'true')
    page.keyboard.press('Escape')
    expect(tooltip).to_have_count(0)


def check_amount_colors(page):
    for direction, dominant in [('income', 1), ('expense', 0)]:
        values = page.locator('.stella-bills [data-direction="' + direction + '"]').evaluate_all('elements => elements.map(e => getComputedStyle(e).color.match(/[0-9.]+/g).slice(0,3).map(Number))')
        assert values and all(color[dominant] > max(color[index] for index in range(3) if index != dominant) for color in values), (direction, values)


def check(page, query, artifacts, native):
    def call(method, args):
        return query('stellaOrganizer/business', {'request': {'domain': 'bills', 'method': method, 'arguments': args}})
    def add(name, amount, **extra):
        return call('create_ledger', {'name': name, 'amount_minor': amount, 'currency': 'CNY', 'entry_type': 'expense', 'category': '餐饮', 'occurred_at': '2026-09-27T17:46:12.123+08:00', **extra})
    lunch = add('Synthetic Cafe', 1234)
    add('Synthetic Cafe', 66)
    add('Synthetic Metro', 200, category='交通', occurred_at='2026-09-28T00:10:00+08:00')
    add('Synthetic Salary', 30000, category='工资', entry_type='income')
    add('Synthetic Pending', 9999, status='pending')
    add('Synthetic Reversed', 8888, status='reversed')
    add('Synthetic Yen', 300, currency='JPY')
    add('Synthetic October', 1000, occurred_at='2026-10-01T12:00:00+08:00')
    open_secretary(page)
    page.get_by_role('button', name='账单', exact=True).click()
    page.get_by_role('button', name='刷新', exact=True).click()
    page.get_by_label('账单年份', exact=True).select_option('2026')
    page.get_by_label('账单月份', exact=True).select_option('09')
    totals = page.locator('.stella-bill-totals-cards')
    expect(totals).to_contain_text('¥300.00'); expect(totals).to_contain_text('¥15.00'); expect(totals).to_contain_text('¥285.00')
    expect(page.locator('.stella-bill-day')).to_have_count(2)
    expect(page.locator('.stella-ledger-list > li')).to_have_count(6)
    expect(page.locator('.stella-bill-day').first).to_contain_text('Synthetic Metro')
    expect(page.locator('.stella-bill-day').last).to_contain_text('餐饮 · 17:46')
    row_height = page.locator('.stella-bill-row').first.bounding_box()['height']
    assert 56 <= row_height <= 64, row_height
    check_amount_colors(page)
    page.screenshot(path=str(artifacts / 'bills-ledger-desktop.png'))
    page.get_by_label('账单币种').select_option('JPY')
    expect(totals).to_contain_text('JP¥300'); expect(page.locator('.stella-ledger-list > li')).to_have_count(1)
    page.get_by_label('账单币种').select_option('CNY')
    with page.expect_download() as export:
        page.get_by_role('button', name='导出 CSV', exact=True).click()
    content = Path(export.value.path()).read_text(encoding='utf-8-sig')
    assert 'Synthetic Cafe' in content and 'Synthetic Yen' not in content
    page.get_by_role('tab', name='月账单', exact=True).click()
    page.get_by_role('button', name='查看2026年9月账单', exact=True).click()
    expect(page.get_by_role('heading', name='分类构成', exact=True)).to_be_visible()
    expect(page.locator('.stella-bill-donut')).to_have_count(2)
    assert page.locator('.stella-bill-donut').first.bounding_box()['width'] <= 132
    expect(page.locator('.stella-bill-legend').first).to_contain_text('¥13.00')
    expect(page.locator('.stella-bill-ranking')).to_contain_text('2 笔 · 86.7%')
    expect(page.locator('.stella-bill-ranking')).not_to_contain_text('Synthetic Pending')
    check_composition(page, '支出构成', '餐饮', '¥13.00', '86.7%')
    check_composition(page, '支出构成', '交通', '¥2.00', '13.3%')
    check_composition(page, '收入构成', '工资', '¥300.00', '100%')
    page.get_by_role('region', name='支出构成', exact=True).get_by_role('button', name='餐饮，', exact=False).focus()
    page.screenshot(path=str(artifacts / 'bills-chart-focus-desktop.png'))
    page.keyboard.press('Escape')
    page.get_by_role('tab', name='收入', exact=True).click()
    expect(page.locator('.stella-bill-ranking')).to_contain_text('Synthetic Salary')
    page.get_by_role('tab', name='支出', exact=True).click()
    page.screenshot(path=str(artifacts / 'bills-month-desktop.png'))
    for theme in ('dark', 'light'):
        page.emulate_media(color_scheme=theme)
        page.wait_for_function('dark => document.body.hasAttribute("data-ds-dark-theme") === dark', arg=theme == 'dark')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.keyboard.press('Escape')
        expect(page.locator('[data-sidebar-drawer]')).to_have_attribute('data-sidebar-collapsed', 'true')
        assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth'), theme
        check_amount_colors(page)
        check_composition(page, '支出构成', '交通', '¥2.00', '13.3%')
        composition = page.get_by_role('region', name='支出构成', exact=True)
        composition.get_by_role('button', name='交通，', exact=False).focus()
        tooltip = composition.get_by_role('tooltip').bounding_box()
        assert tooltip['x'] >= 0 and tooltip['x'] + tooltip['width'] <= 390, tooltip
        page.screenshot(path=str(artifacts / f'bills-report-{theme}-narrow.png'), full_page=True, animations='disabled')
        page.keyboard.press('Escape')
        page.locator('.stella-bill-ranking').scroll_into_view_if_needed()
        assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth'), theme
        page.screenshot(path=str(artifacts / f'bills-ranking-{theme}-narrow.png'), animations='disabled')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.get_by_role('button', name='← 返回', exact=True).click()
    expect(page.get_by_role('button', name='查看2026年10月账单', exact=True)).to_be_visible()
    page.get_by_role('tab', name='年账单', exact=True).click()
    page.get_by_role('button', name='查看2026年账单', exact=True).click()
    expect(page.locator('.stella-bill-report .stella-bill-totals')).to_contain_text('¥25.00')
    expect(page.locator('.stella-bill-period-meta')).to_contain_text('2026-01-01 – 2026-12-31')
    page.screenshot(path=str(artifacts / 'bills-year-desktop.png'))
    page.get_by_role('tab', name='明细', exact=True).click()
    page.get_by_role('button', name='记账', exact=True).click()
    page.get_by_label('标题', exact=True).fill('Synthetic single bill')
    page.get_by_label('金额', exact=True).fill('28.01')
    page.get_by_label('分类', exact=True).fill('生活')
    page.get_by_label('发生时间', exact=True).fill('2026-09-27T23:58:49')
    page.get_by_role('button', name='保存账单', exact=True).click()
    expect(page.get_by_role('button', name='编辑账单：Synthetic single bill', exact=True)).to_be_visible()
    created = next(row for row in call('list_ledger', {'year': 2026, 'month': 9}) if row['name'] == 'Synthetic single bill')
    assert created['amount_minor'] == 2801 and created['category'] == '生活' and created['occurred_at'][11:19] == '23:58:49'
    expect(totals).to_contain_text('¥43.01')
    page.get_by_role('button', name='编辑账单：Synthetic Cafe', exact=True).last.click()
    page.get_by_label('分类', exact=True).fill('早餐')
    page.get_by_role('button', name='保存账单', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert call('get_ledger', {'id': lunch['id']})['occurred_at'] == lunch['occurred_at']
    page.get_by_role('button', name='编辑账单：Synthetic single bill', exact=True).click()
    page.get_by_role('button', name='归档', exact=True).click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    expect(totals).to_contain_text('¥43.01')
    expect(page.get_by_role('button', name='编辑账单：Synthetic single bill', exact=True)).to_contain_text('已归档')
    page.get_by_role('button', name='编辑账单：Synthetic single bill', exact=True).click()
    current = call('get_ledger', {'id': created['id']})
    call('update_ledger', {'id': created['id'], 'patch': {'note': 'Concurrent edit'}, 'expectedRevision': current['revision']})
    page.get_by_label('备注', exact=True).fill('Stale edit')
    page.get_by_role('button', name='保存账单', exact=True).click()
    expect(page.locator('.stella-editor-dialog').get_by_role('alert')).to_contain_text('revision conflict')
    assert call('get_ledger', {'id': created['id']})['note'] == 'Concurrent edit'
    page.get_by_role('button', name='取消编辑', exact=True).click()
    with page.expect_response(lambda response: '/api/stellaOrganizer/business' in response.url and 'ledger_report' in (response.request.post_data or '')):
        page.get_by_role('button', name='刷新', exact=True).click()
    expect(page.get_by_role('tabpanel')).to_have_attribute('aria-busy', 'false')
    page.get_by_role('button', name='编辑账单：Synthetic single bill', exact=True).click()
    expect(page.get_by_label('备注', exact=True)).to_have_value('Concurrent edit')
    page.get_by_role('button', name='删除记录', exact=True).click()
    assert call('get_ledger', {'id': created['id']}) is not None
    page.get_by_role('button', name='确认删除', exact=True).click()
    expect(page.get_by_role('button', name='编辑账单：Synthetic single bill', exact=True)).to_have_count(0)
    expect(totals).to_contain_text('¥15.00')
    page.get_by_label('账单月份', exact=True).select_option('01')
    expect(page.get_by_role('heading', name='这个月还没有流水', exact=True)).to_be_visible()
    expect(page.get_by_role('button', name='导出 CSV', exact=True)).to_be_disabled()
    if not native:
        # Desktop uses its native RPC bridge, so HTTP failure injection belongs to Web.
        page.route('**/api/stellaOrganizer/business', lambda route: route.fulfill(status=503, body='Unavailable'))
        page.get_by_role('tab', name='月账单', exact=True).click()
        expect(page.locator('.stella-bill-load-error')).to_be_visible()
        page.unroute('**/api/stellaOrganizer/business')
        page.get_by_role('button', name='重试', exact=True).click()
        expect(page.get_by_role('button', name='查看2026年9月账单', exact=True)).to_be_visible()
    page.reload()
    page.get_by_role('button', name='切换使用模式，当前 Stella', exact=True).wait_for()
    open_secretary(page)
    page.get_by_role('button', name='账单', exact=True).click()
    page.get_by_label('账单月份', exact=True).select_option('09')
    expect(page.locator('.stella-bill-totals-cards')).to_contain_text('¥15.00')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.keyboard.press('Escape')
    expect(page.locator('[data-sidebar-drawer]')).to_have_attribute('data-sidebar-collapsed', 'true')
    assert page.locator('body').evaluate('node => node.scrollWidth <= node.clientWidth')
    page.screenshot(path=str(artifacts / 'bills-ledger-narrow.png'), animations='disabled')


def main():
    native = os.environ.get('STELLA_CHECK_NATIVE_DESKTOP') == '1'
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-bills-ui')); artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stella-bills-ui-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        env = desktop.environment(home) if native else {**{key: value for key, value in os.environ.items() if key in ('PATH', 'USER', 'LOGNAME', 'TMPDIR')}, 'HOME': str(home), 'DSH_HOME': str(home), 'DSH_TELEMETRY_DISABLED': '1'}
        profile = Path(env['DSH_HOME']) / ('profiles/desktop' if native else 'profiles/compatibility'); profile.mkdir(parents=True)
        (profile / 'package.json').write_text(json.dumps({'name': 'bill-ui-test', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        if native:
            desktop.verify_release(); desktop.native_cli(home, 'bills-install', 'add', str(PACK), '--ignore-scripts')
        else:
            with (artifacts / 'install.log').open('w') as log:
                subprocess.run(['node', str(CLI), 'plugin', '--profile', 'compatibility', 'add', str(PACK), '--ignore-scripts'], cwd=home, env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        with surface(playwright, home, env, artifacts, native) as (page, query, errors):
            check(page, query, artifacts, native)
            assert not errors, errors
    print('PASS: bill day groups, exact totals, currency isolation, monthly/yearly reports, charts/rankings, single entry editing/deletion, revision conflict, recovery, light/dark narrow layouts and reload')


if __name__ == '__main__': main()

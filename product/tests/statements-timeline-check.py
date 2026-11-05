"""Compact statements and connected briefing history on official Desktop."""
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
import official_desktop_check as desktop
from web_check import open_secretary


def check(page, call, artifacts):
    for month in (8, 9, 10):
        call('bills', 'create_ledger', {'name': f'Synthetic month {month}', 'amount_minor': 1234, 'currency': 'CNY', 'entry_type': 'expense', 'category': '餐饮', 'occurred_at': f'2026-{month:02d}-15T12:00:00+08:00'})
    call('bills', 'create_ledger', {'name': 'Synthetic income', 'amount_minor': 30000, 'currency': 'CNY', 'entry_type': 'income', 'category': '工资', 'occurred_at': '2026-09-15T12:00:00+08:00'})
    open_secretary(page)
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.get_by_role('button', name='账单', exact=True).click()
    page.get_by_role('button', name='刷新', exact=True).click()
    page.get_by_role('tab', name='月账单', exact=True).click()
    cards = page.locator('.stella-bill-period-list > button')
    expect(cards).to_have_count(3)
    for card in cards.all():
        box = card.bounding_box()
        assert box['height'] <= 104 and box['width'] <= 720, box
        assert card.evaluate('e => e.scrollWidth <= e.clientWidth')
    boxes = [card.bounding_box() for card in cards.all()]
    assert all(boxes[i + 1]['y'] > boxes[i]['y'] + boxes[i]['height'] and abs(boxes[i + 1]['x'] - boxes[i]['x']) < 1 for i in range(len(boxes) - 1))
    assert page.locator('.stella-bill-period-list').evaluate('e => { const r = e.getBoundingClientRect(); const p = e.closest(".stella-bills-content").getBoundingClientRect(); return r.left > p.left + 24 && r.right < p.right - 24 && Math.abs(r.left + r.width / 2 - p.left - p.width / 2) < 2 }')
    expect(page.get_by_role('button', name='查看2026年9月账单', exact=True)).to_contain_text('¥300.00')
    page.screenshot(path=str(artifacts / 'statements-month-desktop.png'), animations='disabled')
    page.get_by_role('tab', name='年账单', exact=True).click()
    expect(cards).to_have_count(1)
    assert cards.first.bounding_box()['height'] <= 104 and cards.first.bounding_box()['width'] <= 720
    expect(cards.first).to_contain_text('¥37.02')
    page.screenshot(path=str(artifacts / 'statements-year-desktop.png'), animations='disabled')
    cards.first.click()
    expect(page.get_by_role('heading', name='分类构成', exact=True)).to_be_visible()
    page.get_by_role('tab', name='月账单', exact=True).click()
    page.set_viewport_size({'width': 390, 'height': 844})
    page.keyboard.press('Escape')
    assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
    page.screenshot(path=str(artifacts / 'statements-month-narrow.png'), animations='disabled')

    briefing = call('briefing', 'start_briefing', {'briefing_date': '2026-10-22', 'title': 'Synthetic timeline'})
    for index, clock in enumerate(('15:10:02', '15:10:47', '15:10:49', '15:10:50', '15:30:58'), 1):
        call('briefing', 'complete_briefing', {'briefing_id': briefing['id'], 'payload': {'status': 'completed' if index == 5 else 'failed', 'body': 'Synthetic timeline preview', 'run_at': f'2026-10-22T{clock}+08:00', 'trigger': 'manual'}})
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.get_by_role('button', name='简报', exact=True).click()
    timeline = page.get_by_role('list', name='简报更新历史', exact=True)
    expect(timeline.locator('li')).to_have_count(6)
    assert timeline.locator('[data-revision]').evaluate_all('es => es.map(e => Number(e.dataset.revision))') == [5, 4, 3, 2, 1]
    expect(timeline.locator('[data-status=failed]')).to_have_count(4)
    expect(timeline.locator('[data-status=completed]')).to_have_count(1)
    assert timeline.locator('li').evaluate_all('''items => items.every((item, i) => {
      const dot = getComputedStyle(item, '::before')
      if (dot.content === 'none' || parseFloat(dot.width) !== 12) return false
      if (i === items.length - 1) return getComputedStyle(item, '::after').content === 'none'
      const line = getComputedStyle(item, '::after')
      return line.content !== 'none' && Math.abs(item.getBoundingClientRect().top + parseFloat(line.top) + parseFloat(line.height) - items[i + 1].getBoundingClientRect().top - 20) < 1
    })''')
    failed = timeline.locator('[data-status=failed]').first
    successful = timeline.locator('[data-status=completed]').first
    assert failed.evaluate('e => getComputedStyle(e, "::before").borderTopColor') != successful.evaluate('e => getComputedStyle(e, "::before").borderTopColor')
    assert timeline.locator('[data-revision] > span:nth-child(2)').evaluate_all('es => es.every(e => e.getBoundingClientRect().height <= parseFloat(getComputedStyle(e).lineHeight) + 1)')
    page.screenshot(path=str(artifacts / 'briefing-timeline-desktop.png'), animations='disabled')
    timeline.locator('[data-revision="3"]').click()
    expect(page.get_by_text('历史版本 3', exact=False)).to_be_visible()
    expect(timeline.locator('[data-revision="3"]')).to_have_attribute('aria-current', 'true')
    timeline.get_by_role('button', name='当前版本', exact=False).click()
    expect(timeline.get_by_role('button', name='当前版本', exact=False)).to_have_attribute('aria-current', 'true')
    for theme in ('dark', 'light'):
        page.emulate_media(color_scheme=theme)
        page.wait_for_function('dark => document.body.hasAttribute("data-ds-dark-theme") === dark', arg=theme == 'dark')
        page.set_viewport_size({'width': 390, 'height': 844})
        timeline.scroll_into_view_if_needed()
        assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
        page.screenshot(path=str(artifacts / f'briefing-timeline-{theme}-narrow.png'), animations='disabled')


def main():
    artifacts = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', '/private/tmp/stella-statements-timeline'))
    artifacts.mkdir(parents=True, exist_ok=True)
    desktop.verify_release()
    with tempfile.TemporaryDirectory(prefix='stella-statements-timeline-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        desktop.environment(home)
        profile = home / 'dsh/profiles/desktop'
        profile.mkdir(parents=True)
        (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name': 'statement-timeline-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        baseline = (profile / 'cordis.patch.yml').read_bytes()
        desktop.native_cli(home, 'statement-timeline-install', 'add', str(desktop.PACK), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'statement-timeline') as (page, errors):
            call = lambda domain, method, args: desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': domain, 'method': method, 'arguments': args}})
            check(page, call, artifacts)
            assert not errors, errors
        assert (profile / 'cordis.patch.yml').read_bytes() == baseline
    print('PASS: compact monthly/yearly cards, readable exact totals and opening reports; connected chronological revision timeline, status nodes and version switching, light/dark and narrow official Desktop')


if __name__ == '__main__':
    main()

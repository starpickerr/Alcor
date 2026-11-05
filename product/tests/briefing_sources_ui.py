"""Source selection belongs to briefing; profiles remain unchanged outside explicit saves."""
from playwright.sync_api import expect


def check_briefing_sources(page, call, artifacts):
    page.emulate_media(reduced_motion='reduce')
    business = lambda method, args={}: call('stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': method, 'arguments': args}})
    original = business('list_sources')
    grants = call('stellaSecretary/mcpState', {'request': {}})
    expect(page.get_by_role('button', name='数据', exact=True)).to_have_count(0)
    page.get_by_role('button', name='简报', exact=True).click()
    page.get_by_role('button', name='信息来源', exact=True).click()
    dialog = page.get_by_role('dialog', name='简报信息来源', exact=True)
    dialog.get_by_label('Stella 事务', exact=True).check()
    dialog.get_by_role('button', name='保存来源', exact=True).click()
    expect(dialog.get_by_role('status')).to_contain_text('简报来源已保存')
    dialog.get_by_role('button', name='关闭', exact=True).click()
    page.reload()
    page.get_by_role('button', name='信息来源', exact=True).click()
    expect(dialog.get_by_label('Stella 事务', exact=True)).to_be_checked()
    assert call('stellaSecretary/mcpState', {'request': {}}) == grants
    business('configure_sources', {'sources': original})
    dialog.get_by_label('Stella 账单', exact=True).check()
    dialog.get_by_role('button', name='保存来源', exact=True).click()
    expect(dialog.get_by_role('alert')).to_contain_text('其他页面变化')
    assert business('list_sources') == original
    dialog.get_by_role('button', name='刷新来源', exact=True).click()
    expect(dialog.get_by_label('Stella 事务', exact=True)).not_to_be_checked()
    native = dialog.get_by_label('Synthetic plugin inbox', exact=True)
    if native.count():
        native.check()
        dialog.get_by_role('button', name='保存来源', exact=True).click()
        expect(dialog.get_by_role('status')).to_contain_text('简报来源已保存')
        assert any(source['method'] == 'briefing_fetch' for source in business('list_sources') if 'method' in source)
    page.screenshot(path=str(artifacts / 'briefing-sources-desktop.png'))
    for theme in ('dark', 'light'):
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({'width': 520, 'height': 900})
        expect(dialog.get_by_role('button', name='保存来源', exact=True)).to_be_in_viewport()
        assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
        page.screenshot(path=str(artifacts / f'briefing-sources-{theme}-narrow.png'))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.emulate_media(color_scheme='light')
    dialog.get_by_role('button', name='管理 DSH 插件', exact=True).click()
    expect(page.get_by_role('heading', name='Plugins', exact=True).or_(page.get_by_role('heading', name='插件', exact=True))).to_be_visible()
    page.get_by_role('button', name='简报', exact=True).click()
    print('PASS: no data page, briefing source selection, persistence, conflict protection, independent grants, native plugin navigation and narrow themes', flush=True)

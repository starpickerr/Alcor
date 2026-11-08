"""Independent briefing switches retain plugin ownership and source conflict protection."""
from playwright.sync_api import expect


def check_briefing_sources(page, call, artifacts):
    page.emulate_media(reduced_motion='reduce')
    business = lambda method, args={}: call('alcorOrganizer/business', {'request': {'domain': 'briefing', 'method': method, 'arguments': args}})
    original = business('source_groups')
    def overview():
        page.get_by_role('button', name='简报', exact=True).click()
        page.get_by_role('button', name='简报设置', exact=True).click()
        return page.locator('[data-briefing-settings]')
    screen = overview()
    switch = screen.get_by_role('switch', name='启用来源：Alcor 事务', exact=True)
    previous = switch.get_attribute('aria-checked') == 'true'
    switch.click()
    expect(switch).to_have_attribute('aria-checked', str(not previous).lower())
    assert any(row['source'] == 'alcor_tasks' for row in business('list_sources')) != previous
    page.reload()
    page.get_by_role('button', name='简报设置', exact=True).click()
    expect(switch).to_have_attribute('aria-checked', str(not previous).lower())
    current = business('source_groups')
    business('configure_source_groups', {'groups': {**current, 'alcor_bills': True}, 'expectedGroups': current})
    switch.click()
    expect(screen.get_by_role('alert')).to_contain_text('其他页面变化')
    current = business('source_groups')
    business('configure_source_groups', {'groups': original, 'expectedGroups': current})
    screen.get_by_role('button', name='刷新来源', exact=True).click()
    expect(switch).to_have_attribute('aria-checked', str(previous).lower())
    native = screen.get_by_role('switch', name='启用来源：synthetic', exact=True)
    if native.count():
        catalog = business('discover_sources')
        inbox = next(row for row in catalog['sources'] if row.get('label') == 'Synthetic plugin inbox')
        prefs = business('source_preferences')
        business('configure_sources', {'sources': [*prefs, inbox], 'expectedSources': prefs})
        if native.get_attribute('aria-checked') == 'false': native.click()
        expect(native).to_have_attribute('aria-checked', 'true')
    page.screenshot(path=str(artifacts / 'briefing-sources-desktop.png'))
    for theme in ('dark', 'light'):
        page.emulate_media(color_scheme=theme)
        page.set_viewport_size({'width': 520, 'height': 900})
        screen.locator('.alcor-source-card').first.scroll_into_view_if_needed()
        assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
        page.screenshot(path=str(artifacts / f'briefing-sources-{theme}-narrow.png'))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.emulate_media(color_scheme='light')
    screen.get_by_role('button', name='管理插件', exact=True).click()
    expect(page.get_by_role('heading', name='Plugins', exact=True).or_(page.get_by_role('heading', name='插件', exact=True))).to_be_visible()
    page.get_by_role('button', name='简报', exact=True).click()
    print('PASS: source switches persist independently, reject stale writes and preserve native plugin navigation; narrow themes work', flush=True)

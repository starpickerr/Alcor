"""Stress the rendered briefing columns without writing business records."""


def check_briefing_scroll(page, artifacts):
    layout = page.locator('.alcor-briefing-layout')
    sidebars = ['.alcor-briefing-history', '.alcor-briefing-revisions']
    page.set_viewport_size({'width': 1440, 'height': 1000})
    initial_height = layout.bounding_box()['height']
    try:
        page.evaluate('''() => {
          for (const selector of ['.alcor-briefing-history', '.alcor-briefing-timeline']) {
            const target = document.querySelector(selector);
            const sample = target.querySelector(selector.endsWith('history') ? 'button' : 'li');
            for (let i = 0; i < 80; i++) {
              const row = sample ? sample.cloneNode(true) : document.createElement('button');
              row.dataset.scrollFixture = '';
              if (!sample) row.textContent = `历史简报 ${i + 1}`;
              target.append(row);
            }
          }
        }''')
        assert abs(layout.bounding_box()['height'] - initial_height) < 2, 'History must not lengthen the page'
        for width in [1440, 980, 520]:
            page.set_viewport_size({'width': width, 'height': 1000})
            for selector in sidebars:
                panel = page.locator(selector)
                dimensions = panel.evaluate('(e) => ({content:e.scrollHeight, visible:e.clientHeight})')
                assert dimensions['content'] > dimensions['visible'] > 100, (width, selector, dimensions)
                assert panel.bounding_box()['height'] <= page.locator('.alcor-desk').bounding_box()['height'] + 2, 'Sidebar must stay bounded'
                panel.locator('button').last.focus()
                assert panel.evaluate('(e) => e.scrollTop > 0'), 'Keyboard focus must reveal the last entry'
                panel.evaluate('(e) => e.scrollTop = 0')
            assert page.locator('body').evaluate('(e) => e.scrollWidth <= e.clientWidth'), 'Horizontal overflow'
            page.screenshot(path=str(artifacts / f'briefing-scroll-{width}.png'), animations='disabled')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        before = layout.bounding_box()['height']
        page.locator('.alcor-briefing-detail').evaluate('''e => {
          const body = document.createElement('div');
          body.dataset.scrollFixture = '';
          body.style.height = '1800px';
          body.textContent = 'Long briefing body';
          e.append(body);
        }''')
        assert layout.bounding_box()['height'] > before + 1000, 'The briefing body must determine page height'
        for selector in sidebars:
            assert page.locator(selector).bounding_box()['height'] <= page.locator('.alcor-desk').bounding_box()['height'] + 2
        for width in [1440, 1028]:
            page.set_viewport_size({'width': width, 'height': 1000})
            desk = page.locator('.alcor-desk')
            for offset in [900, 100000]:
                desk.evaluate('(e, offset) => e.scrollTop = offset', offset)
                assert desk.evaluate('(e) => e.scrollTop') >= 900
                for selector in sidebars if width == 1440 else sidebars[:1]:
                    bounds, viewport = page.locator(selector).bounding_box(), desk.bounding_box()
                    assert bounds['y'] >= viewport['y'] - 2, ('History must follow the body scroll', width, bounds, viewport)
                    assert bounds['y'] + bounds['height'] >= viewport['y'] + viewport['height'] - 2, ('History must fill the visible column', width, bounds, viewport)
                page.screenshot(path=str(artifacts / f'briefing-scroll-body-down-{width}-{offset}.png'), animations='disabled')
            desk.evaluate('(e) => e.scrollTop = 0')
        page.screenshot(path=str(artifacts / 'briefing-scroll-long-body.png'), animations='disabled')
    finally:
        page.evaluate("document.querySelectorAll('[data-scroll-fixture]').forEach(e => e.remove())")

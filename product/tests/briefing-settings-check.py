"""Unified briefing settings page on the unchanged official Desktop; no model/source content reads."""
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from briefing_sources_ui import check_briefing_sources
from plugin_check import pack_briefing_fixture
from web_check import open_secretary


def main():
    desktop.PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(desktop.PACK)))
    desktop.verify_release()
    desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stella-briefing-settings-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        env = desktop.environment(home)
        profile = Path(env['DSH_HOME']) / 'profiles/desktop'
        profile.mkdir(parents=True)
        (profile / 'package.json').write_text(json.dumps({'name': 'briefing-settings-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
        fixture = pack_briefing_fixture(home)
        desktop.native_cli(home, 'briefing-settings-install', 'add', str(desktop.PACK), str(fixture), '--ignore-scripts')
        with desktop.desktop(playwright, home, 'briefing-settings') as (page, errors):
            open_secretary(page)
            check_briefing_sources(page, lambda endpoint, args: desktop.rpc(page, endpoint, args), desktop.ARTIFACTS)
            gear = page.get_by_role('button', name='简报设置', exact=True)
            expect(gear).to_have_text('')
            expect(gear.locator('svg')).to_have_count(1)
            expect(page.get_by_role('button', name='信息来源', exact=True)).to_have_count(0)
            gear.click()
            screen = page.locator('[data-briefing-settings]')
            expect(screen.get_by_role('heading', name='简报设置', exact=True)).to_be_focused()
            expect(screen.get_by_role('heading', name='信息来源', exact=True)).to_be_visible()
            expect(screen.get_by_role('heading', name='筛选与写作规则', exact=True)).to_be_visible()
            expect(page.get_by_role('dialog', name='简报设置', exact=True)).to_have_count(0)
            screen.get_by_role('button', name='新增规则', exact=True).click()
            detail = page.get_by_role('dialog', name='新增规则', exact=True)
            markdown = '# 重点通知\n\n保留 **截止日期** 和需要处理的通知。\n\n- 忽略重复信息\n- [参考](https://example.com/rules)\n\n```text\n完整规则原文\n```\n\n<img src=x onerror="window.__ruleUnsafe=true">'
            detail.get_by_label('规则内容', exact=True).fill(markdown)
            detail.get_by_role('button', name='保存规则', exact=True).click()
            detail = page.get_by_role('dialog', name='规则详情', exact=True)
            expect(detail.locator('.stella-rule-preview h1')).to_have_text('重点通知')
            expect(detail.locator('.stella-rule-preview strong')).to_have_text('截止日期')
            expect(detail.locator('.stella-rule-preview a')).to_have_attribute('href', 'https://example.com/rules')
            expect(detail.locator('.stella-rule-preview pre')).to_contain_text('完整规则原文')
            expect(detail.locator('.stella-rule-preview img')).to_have_count(0)
            assert not page.evaluate('Boolean(window.__ruleUnsafe)')
            detail.get_by_role('button', name='编辑规则', exact=True).click()
            detail.get_by_label('规则内容', exact=True).fill(markdown.replace('重点通知', '重点通知（更新）'))
            detail.get_by_role('button', name='保存规则', exact=True).click()
            expect(detail.locator('.stella-rule-preview h1')).to_have_text('重点通知（更新）')
            detail.get_by_role('switch', name='启用规则', exact=True).click()
            expect(detail.get_by_role('switch', name='启用规则', exact=True)).to_have_attribute('aria-checked', 'false')
            detail.get_by_role('button', name='编辑规则', exact=True).click()
            detail.get_by_label('规则内容', exact=True).fill('不要保存此草稿')
            detail.get_by_role('button', name='取消编辑', exact=True).click()
            expect(detail.locator('.stella-rule-preview h1')).to_have_text('重点通知（更新）')
            detail.get_by_role('button', name='关闭', exact=True).click()
            card = screen.get_by_role('button', name='查看规则：重点通知（更新）', exact=True)
            expect(screen.get_by_role('switch', name='启用规则：重点通知（更新）', exact=True)).to_have_attribute('aria-checked', 'false')
            expect(card).not_to_contain_text('onerror')
            expect(card).not_to_contain_text('完整规则原文')
            expect(screen.locator('textarea')).to_have_count(0)
            screen.get_by_role('button', name='返回简报', exact=True).click()
            expect(gear).to_be_focused()
            expect(page.get_by_role('heading', name='简报', exact=True)).to_be_visible()
            gear.click()
            expect(screen.get_by_role('switch', name='启用规则：重点通知（更新）', exact=True)).to_have_attribute('aria-checked', 'false')
            rule_switch = screen.get_by_role('switch', name='启用规则：重点通知（更新）', exact=True)
            rule_switch.click()
            expect(rule_switch).to_have_attribute('aria-checked', 'true')
            rule_switch.click()
            expect(rule_switch).to_have_attribute('aria-checked', 'false')
            refresh_rules = screen.get_by_role('button', name='刷新规则', exact=True)
            expect(refresh_rules).to_have_text('')
            expect(refresh_rules.locator('svg')).to_have_count(1)
            request = lambda method, args={}: desktop.rpc(page, 'stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': method, 'arguments': args}})
            saved_rule = next(row for row in request('list_rules') if '重点通知（更新）' in row['text'])
            request('update_rule', {'rule_id': saved_rule['id'], 'payload': {'text': saved_rule['text'].replace('重点通知（更新）', '重点通知（刷新）')}})
            refresh_rules.click()
            expect(screen.get_by_role('button', name='查看规则：重点通知（刷新）', exact=True)).to_be_visible()
            request('update_rule', {'rule_id': saved_rule['id'], 'payload': {'text': saved_rule['text']}})
            refresh_rules.click()
            expect(card).to_be_visible()
            left = screen.locator('.stella-source-card').first.bounding_box()
            right = card.bounding_box()
            assert abs(left['y'] - right['y']) <= 1, (left, right)
            assert abs(left['height'] - right['height']) <= 2, (left, right)
            screen.get_by_role('heading', name='信息来源', exact=True).scroll_into_view_if_needed()
            page.screenshot(path=str(desktop.ARTIFACTS / 'briefing-settings-desktop.png'))
            for theme in ('dark', 'light'):
                page.emulate_media(color_scheme=theme)
                page.set_viewport_size({'width': 430, 'height': 900})
                expect(page.locator('.stella-sidebar [data-sidebar-toggle]')).not_to_be_visible()
                card.scroll_into_view_if_needed()
                expect(card).to_be_in_viewport()
                assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
                page.screenshot(path=str(desktop.ARTIFACTS / f'briefing-settings-{theme}-narrow.png'))
                card.click()
                expect(detail.locator('.stella-rule-preview h1')).to_have_text('重点通知（更新）')
                page.screenshot(path=str(desktop.ARTIFACTS / f'rule-detail-{theme}-narrow.png'))
                detail.get_by_role('button', name='关闭', exact=True).click()
            card.click()
            detail.get_by_role('button', name='删除规则', exact=True).click()
            expect(card).to_have_count(1)
            detail.get_by_role('button', name='确认删除', exact=True).click()
            expect(card).to_have_count(0)
            rows = [json.loads(line) for line in (profile / 'node_modules/@stella-test/briefing-source/reads.jsonl').read_text().splitlines()]
            assert all(row['type'] == 'catalog' for row in rows), 'Settings must not read source content'
            expect(page.locator('.stella-sidebar').get_by_role('button',name='AgentHub',exact=True)).to_have_count(0)
            assert not errors, errors
    print('PASS: one gear opens a full settings page with sources and rules; source saves/conflicts, rule creation/edit/cancel/toggle/confirmed deletion and safe Markdown work; desktop and narrow themes; zero model or source content calls')


if __name__ == '__main__':
    main()

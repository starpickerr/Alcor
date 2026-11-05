"""Real DSH pages and credential lifecycle, with a synthetic stdio server only."""
import json
from pathlib import Path
import shutil
import tempfile

from playwright.sync_api import sync_playwright, expect
from web_check import ROOT, start, stop, rpc, open_page, settings, open_secretary


def sources(page):
    open_secretary(page)
    settings(page)
    expect(page.get_by_role('button', name='测试连接', exact=True)).to_be_visible()


def main():
    with tempfile.TemporaryDirectory(prefix='stella-credential-web-') as temporary, sync_playwright() as p:
        home = Path(temporary)
        (home / '.stella-product.json').write_text('{"version":1,"kind":"web"}\n')
        profile = home / 'profiles/stella'
        profile.mkdir(parents=True)
        command = home / 'synthetic-mcp'
        command.write_text('#!' + shutil.which('node') + '\n' + (ROOT / 'product/tests/fixtures/mcp-server.mjs').read_text())
        command.chmod(0o700)
        (home / 'documents').mkdir()
        (profile / 'cordis.patch.yml').write_text(f'''- id: workspace-controller
  config:
    documentsDirectory: {home / 'documents'}
- id: agent-default-model
  config:
    provider: synthetic
    model: synthetic-model
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:1/v1
        models:
          - id: synthetic-model
            name: Synthetic model
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
- insert:
    - id: stella-elearning-mcp
      name: '@stella/secretary/elearning'
      config:
        command: {json.dumps(str(command))}
        cwd: {json.dumps(str(home))}
        dataDir: {json.dumps(str(home / 'elearning'))}
''')
        (home / '.credentials.yaml').write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-key\n')
        (home / '.credentials.yaml').chmod(0o600)
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(reduced_motion='reduce')
        with (home / 'web.log').open('w') as log:
            process, url = start(home, log)
            try:
                page, errors = open_page(context, url)
                sources(page)
                expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('type', 'password')
                page.get_by_label('学工号', exact=True).fill('synthetic-user')
                page.get_by_label('eLearning 密码', exact=True).fill('synthetic-web-secret')
                page.get_by_role('button', name='保存账号', exact=True).click()
                expect(page.locator('.stella-connection-settings [role=status]')).to_have_text('账号已保存。')
                expect(page.get_by_label('eLearning 密码', exact=True)).to_have_value('')
                expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('placeholder', '********')
                assert (home / '.credentials.yaml').stat().st_mode & 0o777 == 0o600
                assert 'synthetic-web-secret' in (home / '.credentials.yaml').read_text()
                assert 'synthetic-web-secret' not in (profile / 'cordis.patch.yml').read_text()
                assert not (home / 'elearning/elearning.json').exists()
                meta = rpc(page, url, 'stellaSecretary/elearningAccount', {'request': {}})
                assert meta == {'installed': True, 'configured': True, 'connected': True, 'writable': True, 'username': 'synthetic-user'}
                page.get_by_role('button', name='测试连接', exact=True).click()
                expect(page.locator('.stella-connection-settings [role=status]')).to_have_text('连接有效，可访问 1 门课程。')
                page.get_by_role('dialog', name='Settings', exact=True).get_by_role('button', name='Close', exact=True).click()
                sources(page)
                page.get_by_text('Stella 对话工具', exact=True).click()
                page.get_by_role('checkbox', name='elearning · 查看课程列表', exact=True).check()
                page.get_by_role('button', name='保存对话权限', exact=True).click()
                expect(page.locator('.stella-connection-settings [role=status]')).to_have_text('对话权限已保存。')
                Path('/private/tmp/stella-mcp-preview').mkdir(exist_ok=True)
                page.screenshot(path='/private/tmp/stella-mcp-preview/native-credentials-desktop.png')
                page.set_viewport_size({'width': 390, 'height': 844})
                if page.get_by_role('dialog', name='Stella 秘书导航', exact=True).count():
                    page.locator('.stella-sidebar [data-sidebar-toggle]').click()
                expect(page.get_by_role('dialog', name='Stella 秘书导航', exact=True)).to_have_count(0)
                page.get_by_role('button', name='保存对话权限', exact=True).click(trial=True)
                page.screenshot(path='/private/tmp/stella-mcp-preview/native-credentials-mobile.png', animations='disabled')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.set_viewport_size({'width': 1280, 'height': 850})
                page.get_by_role('dialog', name='Settings', exact=True).get_by_role('button', name='Close', exact=True).click()
                page.get_by_role('button', name='简报', exact=True).click()
                page.get_by_role('button', name='信息来源', exact=True).click()
                page.get_by_label('复旦 eLearning', exact=True).check()
                page.get_by_role('button', name='选择课程', exact=True).click()
                scope = page.get_by_role('dialog', name='简报信息来源', exact=True)
                expect(scope.get_by_label('Synthetic course', exact=True)).to_be_visible()
                scope.get_by_label('全部可访问课程', exact=True).uncheck()
                scope.get_by_role('button', name='保存来源', exact=True).click()
                expect(scope.get_by_role('alert')).to_contain_text('至少选择一门课程')
                scope.get_by_label('Synthetic course', exact=True).check()
                scope.get_by_role('button', name='保存来源', exact=True).click()
                expect(scope.get_by_role('status')).to_contain_text('简报来源已保存')
                scope.get_by_role('button', name='关闭', exact=True).click()
                saved = rpc(page, url, 'stellaOrganizer/business', {'request': {'domain': 'briefing', 'method': 'list_sources'}})
                assert next(source for source in saved if source['source'] == 'elearning')['arguments']['course_ids'] == ['synthetic-course']
                assert not errors, errors
                page.close()
            finally:
                stop(process)
        with (home / 'restart.log').open('w') as log:
            process, url = start(home, log)
            try:
                page, errors = open_page(context, url)
                sources(page)
                expect(page.get_by_label('学工号', exact=True)).to_have_value('synthetic-user')
                expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('placeholder', '********')
                page.get_by_role('button', name='移除账号', exact=True).click()
                page.get_by_role('button', name='确认移除账号', exact=True).click()
                expect(page.locator('.stella-connection-settings [role=status]')).to_have_text('账号已移除，历史记录已保留。')
                assert rpc(page, url, 'stellaSecretary/elearningAccount', {'request': {}})['configured'] is False
                assert 'synthetic-web-secret' not in (home / '.credentials.yaml').read_text()
                expect(page.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
                expect(page.get_by_label('eLearning 密码', exact=True)).to_have_attribute('placeholder', '')
                assert not errors, errors
            finally:
                stop(process)
        browser.close()
    print('PASS: DSH settings entry, masked credential save, native 0600 storage, MCP discovery/query, restart and deletion, desktop and 390px; synthetic data only')


if __name__ == '__main__':
    main()

"""Synthetic-profile UI confirmation and cancellation, without Apple or a model."""
import os
import sys
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from web_check import open_page, open_secretary, rpc, start, stop


def main():
    with fresh_test_home('alcor-delete-ui-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        (home / '.alcor-product.json').write_text('{"version":1,"kind":"web"}\n')
        profile = home / 'profiles/alcor'; profile.mkdir(parents=True)
        initialize_fresh(temporary, home, profile)
        (profile / 'cordis.patch.yml').write_text('''- id: agent-default-model
  config:
    provider: synthetic
    model: no-calls
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: SYNTHETIC_UI_KEY
        baseURL: http://127.0.0.1:9/v1
        models:
          - id: no-calls
            name: Synthetic UI model
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
''')
        (home / '.credentials.yaml').write_text('version: 1\nrefs:\n  SYNTHETIC_UI_KEY: synthetic-only\n'); os.chmod(home / '.credentials.yaml', 0o600)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-secretary'))
        from business.tasks.domain.service import TodoService
        data = profile / 'alcor/business'; data.mkdir(parents=True)
        tasks = TodoService(data / 'tasks.sqlite3')
        parent = tasks.create_todo({'title':'Synthetic delete parent'})
        child = tasks.create_todo({'title':'Synthetic delete child','parent_id':parent['id']})
        browser = playwright.chromium.launch(headless=True); context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        with (home / 'host.log').open('w') as log:
            process, url = start(home, log)
            try:
                page, errors = open_page(context, url); open_secretary(page)
                card = page.locator('.alcor-task-node').filter(has_text='Synthetic delete parent')
                try: expect(card).to_be_visible()
                except Exception:
                    print('VISIBLE TEXT:',page.locator('body').inner_text()); page.screenshot(path='/tmp/alcor-delete-ui-failure.png', full_page=True); raise
                def open_delete():
                    card.get_by_role('button', name='更多操作：Synthetic delete parent', exact=True).click()
                    page.get_by_role('menuitem', name='删除事务', exact=True).click()
                    dialog = page.get_by_role('dialog', name='删除事务', exact=True)
                    expect(dialog).to_contain_text('Synthetic delete child')
                    expect(dialog).to_contain_text(child['id'])
                    return dialog
                dialog = open_delete(); dialog.get_by_role('button', name='取消', exact=True).click(); expect(card).to_be_visible()
                page.set_viewport_size({'width': 390, 'height': 844})
                dialog = open_delete(); assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
                dialog.get_by_role('button', name='确认删除', exact=True).click(); expect(card).to_have_count(0)
                rows = rpc(page, url, 'alcorOrganizer/business', {'request': {'domain':'tasks','method':'list_todos','arguments':{}}})
                assert all(row['id'] not in (parent['id'], child['id']) for row in rows)
                category = rpc(page, url, 'alcorOrganizer/business', {'request': {'domain':'tasks','method':'create_category','arguments':{'payload':{'name':'Synthetic delete list'}}}})
                member = rpc(page, url, 'alcorOrganizer/business', {'request': {'domain':'tasks','method':'create_todo','arguments':{'payload':{'title':'Synthetic list member','category_id':category['id']}}}})
                page.get_by_role('button', name='刷新', exact=True).click()
                page.get_by_role('button', name='清单管理', exact=True).click()
                panel = page.get_by_role('dialog', name='清单管理')
                panel.get_by_role('button', name='Synthetic delete list', exact=True).click()
                panel.get_by_role('button', name='删除清单：Synthetic delete list', exact=True).click()
                expect(panel).to_contain_text('Synthetic list member'); expect(panel).to_contain_text(member['id'])
                panel.get_by_role('button', name='返回清单详情', exact=True).click()
                panel.get_by_role('button', name='删除清单：Synthetic delete list', exact=True).click()
                expect(panel).to_contain_text(member['id'])
                panel.get_by_role('button', name='确认删除清单', exact=True).click()
                expect(panel.get_by_role('button', name='Synthetic delete list', exact=True)).to_have_count(0)
                assert not errors, errors
                print('PASS: owner UI shows exact tree, cancellation preserves it, confirmed deletion removes it; synthetic profile only')
            finally:
                stop(process); browser.close()

if __name__ == '__main__': main()

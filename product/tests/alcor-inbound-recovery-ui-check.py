"""Recovery UI over an isolated synthetic DSH profile, without Apple or model access."""
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from web_check import open_page, open_secretary, rpc, start, stop


def main():
    with fresh_test_home('alcor-inbound-ui-') as temporary, sync_playwright() as playwright:
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
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-secretary'))
        from business.tasks.domain.service import TodoService
        from business.calendar.domain.service import CalendarService
        data = profile / 'alcor/business'; data.mkdir(parents=True)
        tasks = TodoService(data / 'tasks.sqlite3'); todo = tasks.create_todo({'title':'Synthetic recovery task'}); tasks.apple_remove_item(todo['id'])
        calendar = CalendarService(data / 'calendar.sqlite3'); event = calendar.create_event({'title':'Synthetic recovery event','start_at':'2026-11-01T09:00:00+00:00','end_at':'2026-11-01T10:00:00+00:00'}); calendar.apple_remove_item(event['id'])
        browser = playwright.chromium.launch(headless=True); context = browser.new_context(viewport={'width': 390, 'height': 844})
        with (home / 'host.log').open('w') as log:
            process, url = start(home, log); page = None
            try:
                page, errors = open_page(context, url); open_secretary(page)
                for label, title, domain in [('事务','Synthetic recovery task','tasks'),('日程','Synthetic recovery event','calendar')]:
                    page.get_by_role('button', name=label, exact=True).click()
                    page.get_by_role('button', name='恢复同步移除记录', exact=True).click()
                    dialog = page.get_by_role('dialog', name='恢复同步移除记录'); expect(dialog).to_be_visible()
                    expect(dialog.get_by_text(title, exact=True)).to_be_visible()
                    dialog.get_by_role('button', name='恢复本地副本', exact=True).click()
                    expect(page.get_by_text('已恢复本地副本，不会自动同步到 Apple。', exact=False)).to_be_visible()
                    expect(dialog.get_by_text('已恢复', exact=True)).to_be_visible()
                    records = rpc(page, url, 'alcorOrganizer/listInboundRecoveries', {'request': {'domain':domain}})
                    assert len(records)==1 and records[0]['status']=='restored'
                    assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
                    dialog.get_by_role('button', name='关闭恢复记录').click()
                assert not errors, errors
                print('PASS: owner recovery UI restores local-only task and event; synthetic profile, no Apple access')
            finally:
                stop(process); browser.close()


if __name__ == '__main__': main()

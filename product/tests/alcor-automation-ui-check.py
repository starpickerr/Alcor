"""Test fixed-target deletion in an isolated native DSH Web profile, without model or account calls."""
import os
from pathlib import Path
import re
import subprocess
from playwright.sync_api import expect, sync_playwright
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from web_check import ROOT, open_page, open_secretary, rpc, start, stop


def main():
    with fresh_test_home('alcor-automation-ui-') as temporary, sync_playwright() as playwright:
        home = Path(temporary)
        (home / '.alcor-product.json').write_text('{"version":1,"kind":"web"}\n')
        profile = home / 'profiles/alcor'
        profile.mkdir(parents=True)
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
        (home / '.credentials.yaml').write_text('version: 1\nrefs:\n  SYNTHETIC_UI_KEY: synthetic-only\n')
        os.chmod(home / '.credentials.yaml', 0o600)
        subprocess.run(['node', '--input-type=module', '-', str(home / 'storages')], input='''
import { Context } from '@deepseek-ai/cordis';
import Storage from '@deepseek-ai/dsh-storage';
import * as Json from '@deepseek-ai/dsh-storage-json';
import * as Domain from '@deepseek-ai/dsh-storage-domain';
import { scheduleDomain } from '@deepseek-ai/dsh-schedule';
import { z } from 'zod';
import { recordSchema } from './packages/alcor-organizer/src/records.js';
import { jobSchema } from './packages/alcor-organizer/src/remote.js';
const ctx = new Context();
try {
  await ctx.plugin(Storage); await ctx.plugin(Json, { root: process.argv[2] }); await ctx.plugin(Domain, { backend: 'json' });
  const schedule = await ctx.storageDomain.open(scheduleDomain);
  const own = await ctx.storageDomain.open(Domain.defineDomain({ name: 'alcor_organizer', version: 1,
    tables: { records: Domain.domainTable(recordSchema), jobs: Domain.domainTable(jobSchema), schedules: Domain.domainTable(z.object({ id: z.string() }).strict()) } }));
  for (const id of ['single', 'batch-one', 'batch-two', 'foreign']) {
    await schedule.table('tasks').put(id, { record: { id, kind: 'at', title: 'Synthetic ' + id, prompt: 'Synthetic only', scheduledAt: '2099-01-01T00:00:00.000Z' }, status: 'ended', updatedAt: '2026-11-07T00:00:00.000Z', target: { kind: 'new', create: {} }, involvedSessionIds: [] });
    if (id !== 'foreign') await own.table('schedules').put(id, { id });
  }
} finally { await ctx.fiber.dispose() }
''', text=True, cwd=ROOT, check=True)
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        with (home / 'host.log').open('w') as log:
            process, url = start(home, log)
            page = None
            try:
                page, errors = open_page(context, url)
                open_secretary(page)
                page.get_by_role('button', name='自动化', exact=True).click()
                manager = page.get_by_test_id('task-manager-page')
                expect(manager).to_be_visible()
                manager.get_by_role('button', name='Ended', exact=True).click()

                def catalog(scope='alcor'):
                    return rpc(page, url, 'alcorOrganizer/automation', {'request': {'scope': scope, 'action': 'catalog'}})

                def open_delete(title):
                    manager.get_by_role('button', name=title, exact=True).click()
                    page.get_by_role('button', name='Automation task actions', exact=True).click()
                    page.get_by_role('menuitem', name='Delete task', exact=True).click()
                    page.get_by_role('button', name='Confirm deletion', exact=True).click()
                    dialog = page.get_by_role('dialog', name='确认永久删除自动化', exact=True)
                    expect(dialog).to_be_visible()
                    return dialog

                dialog = open_delete('Synthetic single')
                expect(dialog.get_by_text('ID：single', exact=True)).to_be_visible()
                expect(dialog.get_by_text('版本：2026-11-07T00:00:00.000Z', exact=True)).to_be_visible()
                dialog.get_by_role('button', name='取消', exact=True).click()
                expect(dialog).not_to_be_visible()
                assert any(row['id'] == 'single' for row in catalog())

                dialog = open_delete('Synthetic single')
                rpc(page, url, 'alcorOrganizer/automation', {'request': {'scope': 'alcor', 'action': 'edit', 'request': {'id': 'single', 'action': 'update', 'patch': {'title': 'Changed while confirming'}}}})
                dialog.get_by_role('button', name='删除所列 1 项', exact=True).click()
                expect(page.get_by_text('自动化已变化或删除未完成，请刷新后重新确认。', exact=True)).to_be_visible()
                assert any(row['id'] == 'single' for row in catalog())
                page.reload()
                page.get_by_role('button', name='自动化', exact=True).click()
                manager.get_by_role('button', name='Ended', exact=True).click()
                dialog = open_delete('Changed while confirming')
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.locator('body').evaluate('e => e.scrollWidth <= e.clientWidth')
                dialog.get_by_role('button', name='删除所列 1 项', exact=True).click()
                expect(dialog).not_to_be_visible()
                expect(page.get_by_text('Task deleted.', exact=True)).to_be_visible()
                assert all(row['id'] != 'single' for row in catalog())
                page.set_viewport_size({'width': 1280, 'height': 850})
                page.get_by_role('button', name='More task actions', exact=True).first.click()
                page.get_by_role('menuitem', name='Clear all ended tasks', exact=True).click()
                page.get_by_role('button', name='Clear tasks', exact=True).click()
                dialog = page.get_by_role('dialog', name='确认永久删除自动化', exact=True)
                expect(dialog.get_by_text('ID：batch-one', exact=True)).to_be_visible()
                expect(dialog.get_by_text('ID：batch-two', exact=True)).to_be_visible()
                rpc(page, url, 'alcorOrganizer/automation', {'request': {'scope': 'alcor', 'action': 'edit', 'request': {'id': 'batch-two', 'action': 'update', 'patch': {'title': 'Changed batch row'}}}})
                dialog.get_by_role('button', name='删除所列 2 项', exact=True).click()
                expect(page.get_by_text(re.compile('已删除 1 项；1 项未确认删除'))).to_be_visible()
                assert {row['id'] for row in catalog()} == {'batch-two'}
                assert {row['id'] for row in catalog('all')} == {'batch-two', 'foreign'}
                assert not errors, errors
                print('PASS: actual native UI owner deletion, cancellation, version conflict, fixed batch partial result and 390px modal; synthetic storage only')
            except Exception:
                if page:
                    print('PAGE:', page.locator('body').inner_text()[-7000:])
                raise
            finally:
                stop(process)
                browser.close()


if __name__ == '__main__':
    main()

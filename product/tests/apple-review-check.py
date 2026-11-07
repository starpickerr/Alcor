"""Render difficult Apple states in the official Desktop with a synthetic worker only."""
import json
import os
import tempfile
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary


def main():
    artifacts=Path(os.environ.get('STELLA_CHECK_ARTIFACTS','/private/tmp/stella-apple-ui-review'))
    artifacts.mkdir(parents=True,exist_ok=True);desktop.ARTIFACTS=artifacts
    desktop.PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(desktop.PACK)))
    desktop.verify_release()
    original=json.loads((Path(__file__).parent/'fixtures/apple-review.json').read_text())

    with tempfile.TemporaryDirectory(prefix='stella-apple-review-') as temporary,sync_playwright() as playwright:
        home=Path(temporary);desktop.environment(home)
        profile=home/'dsh/profiles/desktop';profile.mkdir(parents=True);(home/'documents').mkdir()
        (profile/'package.json').write_text(json.dumps({'name':'apple-review','private':True,'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}))
        (profile/'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        pack=Path(os.environ.get('STELLA_APPLE_REVIEW_PACK',str(artifacts/'stella-apple-sync-0.1.15.tgz')))
        desktop.native_cli(home,'review-install','add',str(desktop.PACK),str(pack),'--ignore-scripts')
        worker=profile/'node_modules/@stella/apple-sync/python/worker.py'
        worker.unlink()
        worker.write_text((Path(__file__).parent/'fixtures/apple-review-worker.py').read_text())
        state_file=worker.parent/'review-state.json'
        def write_states(value):
            pending=state_file.with_suffix('.next')
            pending.write_text(json.dumps(value));pending.replace(state_file)
        write_states(original)
        failure=worker.parent/'review-fail'
        def calls():
            log=worker.parent/'review-calls.jsonl'
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        with desktop.desktop(playwright,home,'apple-review') as (page,errors):
            page.set_viewport_size({'width':1440,'height':1100})
            open_secretary(page)
            page.get_by_role('button',name='Plugins',exact=True).or_(page.get_by_role('button',name='插件',exact=True)).click()
            page.get_by_text('@stella/apple-sync',exact=True).click()
            settings=page.locator('.apple-sync-settings');expect(settings).to_be_visible()
            calendar=page.get_by_role('region',name='Apple 日历',exact=True);reminders=page.get_by_role('region',name='Apple 提醒事项',exact=True)
            expect(calendar.get_by_text('每周项目回顾',exact=True)).to_be_visible()
            settings.screenshot(path=str(artifacts/'01-overview.png'),animations='disabled')
            calendar.get_by_role('button',name='工作',exact=False).click()
            dialog=page.get_by_role('dialog',name='处理同步冲突',exact=True)
            expect(dialog.get_by_role('button',name='确认并同步')).to_be_disabled()
            dialog.get_by_label('采用 Apple',exact=False).check()
            page.screenshot(path=str(artifacts/'02-name-conflict.png'),animations='disabled')
            dialog.screenshot(path=str(artifacts/'02-name-conflict-detail.png'),animations='disabled')
            failure.touch();dialog.get_by_role('button',name='确认并同步').click()
            expect(dialog.get_by_role('alert')).to_contain_text('Apple 暂时无法连接')
            page.wait_for_timeout(5200)
            expect(dialog.get_by_role('alert')).to_contain_text('Apple 暂时无法连接')
            failure.unlink();dialog.get_by_role('button',name='确认并同步').click();expect(dialog).to_have_count(0)
            assert calls()[-1]=={'method':'resolve_category_conflict','arguments':{'kind':'calendar','apple_id':'work','field':'name','choice':'keep_apple'}},calls
            calendar.get_by_role('button',name='课程',exact=False).click()
            dialog.get_by_label('重新关联到',exact=False).check()
            dialog.screenshot(path=str(artifacts/'03-missing-source.png'),animations='disabled')
            dialog.get_by_label('确认 Apple 已删除此来源',exact=False).check();dialog.get_by_role('button',name='继续…').click()
            deletion=page.get_by_role('dialog',name='删除 Stella 日历？',exact=True)
            expect(deletion).to_contain_text('其中全部日程')
            expect(deletion.get_by_role('button',name='返回',exact=True)).to_be_focused()
            deletion.screenshot(path=str(artifacts/'04-delete-confirmation.png'),animations='disabled')
            before=len(calls());page.keyboard.press('Escape');expect(deletion).to_have_count(0);assert len(calls())==before
            calendar.get_by_role('button',name='每周项目回顾',exact=False).click()
            receipt=page.get_by_role('dialog',name='等待 Apple 确认',exact=True)
            receipt.screenshot(path=str(artifacts/'05-pending-operation.png'),animations='disabled')
            receipt.get_by_role('button',name='取消此次操作').click();expect(receipt).to_have_count(0)
            assert calls()[-1]['method']=='cancel_operation' and calls()[-1]['arguments']['operation_id']=='pending-1'
            reminders.get_by_role('button',name='完成 Python 练习',exact=False).click()
            dialog.get_by_label('保留 Stella',exact=False).check()
            dialog.screenshot(path=str(artifacts/'06-record-conflict.png'),animations='disabled')
            page.set_viewport_size({'width':390,'height':844})
            assert dialog.evaluate('e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}')
            expect(dialog.get_by_role('button',name='确认并同步')).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'07-narrow.png'),animations='disabled')
            page.emulate_media(color_scheme='dark');page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            dialog.screenshot(path=str(artifacts/'08-dark.png'),animations='disabled')
            dialog.get_by_role('button',name='确认并同步').click();expect(dialog).to_have_count(0)
            assert calls()[-1]['arguments']=={'kind':'reminders','todo_id':'task-1','choice':'keep_stella'}
            page.set_viewport_size({'width':1440,'height':1100});page.emulate_media(color_scheme='light')
            calendar.get_by_role('button',name='课程',exact=False).click();dialog.get_by_label('保留 Stella，重建',exact=False).check()
            expect(dialog.get_by_role('combobox')).to_have_value('icloud');dialog.get_by_role('button',name='确认并同步').click()
            assert calls()[-1]['arguments']=={'kind':'calendar','apple_id':'old-course','choice':'keep_stella','source_id':'icloud'}
            expect(page.get_by_role('dialog')).to_have_count(0)
            states=json.loads(state_file.read_text())
            states['calendar']=dict(supported=True,authorization='denied',connected=False)
            write_states(states)
            page.wait_for_timeout(5200)
            expect(calendar).to_contain_text('隐私与安全性')
            settings.screenshot(path=str(artifacts/'09-permissions.png'),animations='disabled')
            states['calendar']={**original['calendar'],'category_conflicts':[],'operations':[],'links':[{k:v for k,v in link.items() if k!='name_conflict'} for link in original['calendar']['links']]}
            states['reminders']={**original['reminders'],'conflicts':[]}
            write_states(states);page.wait_for_timeout(5200)
            expect(calendar.get_by_text('自动同步已开启',exact=True)).to_be_visible()
            expect(reminders.get_by_text('部分同步 · 含只读内容',exact=True)).to_be_visible()
            settings.screenshot(path=str(artifacts/'10-connected.png'),animations='disabled')
            states['calendar']['category_conflicts']=[{'apple_id':'work','stella_id':'work-local','kind':'color','reason':'Apple 与 Stella 同时修改颜色'}]
            states['calendar']['calendars'][0]['color']='#28b8d4'
            write_states(states);page.wait_for_timeout(5200)
            calendar.get_by_role('button',name='工作',exact=False).click()
            expect(dialog).to_contain_text('#6185dc');expect(dialog).to_contain_text('#28b8d4')
            dialog.get_by_label('采用 Apple',exact=False).check();dialog.get_by_role('button',name='确认并同步').click()
            assert calls()[-1]['arguments']['field']=='color'
            expect(page.get_by_role('dialog')).to_have_count(0)
            states=json.loads(state_file.read_text())
            states['calendar']['category_conflicts']=[{'apple_id':'work','stella_id':'work-local','kind':'legacy_retained','reason':'旧内容已保留'}]
            write_states(states);page.wait_for_timeout(5200)
            calendar.get_by_role('button',name='工作',exact=False).click();dialog.get_by_role('button',name='确认保留',exact=True).click()
            assert calls()[-1]['arguments']['choice']=='acknowledge'
            assert not errors,errors
    print('PASS: official Desktop synthetic conflict review, names, missing sources, destructive confirmation, retry/error persistence, pending cancellation, record choices, narrow and dark layouts; no personal Apple calls')

if __name__=='__main__':main()

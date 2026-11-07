"""Native list management with local, immutable and pending synthetic Apple categories."""
import json
import os
import sys
import tempfile
from pathlib import Path
from playwright.sync_api import expect,sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary
sys.path.insert(0,str(desktop.ROOT/'packages/stella-secretary'))
from business.worker import Business


def main():
    artifacts=Path(os.environ.get('STELLA_CHECK_ARTIFACTS','/private/tmp/stella-lists-manager'))
    artifacts.mkdir(parents=True,exist_ok=True);desktop.ARTIFACTS=artifacts
    desktop.PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(desktop.PACK)))
    apple_version=json.loads((desktop.ROOT/'packages/stella-apple-sync/package.json').read_text())['version']
    apple_pack=desktop.ROOT/f'.local/artifacts/stella-apple-sync-{apple_version}.tgz'
    desktop.verify_release()
    with tempfile.TemporaryDirectory(prefix='stella-lists-manager-') as temporary,sync_playwright() as playwright:
        home=Path(temporary);desktop.environment(home)
        profile=home/'dsh/profiles/desktop';profile.mkdir(parents=True);(home/'documents').mkdir()
        (profile/'package.json').write_text(json.dumps({'name':'lists-manager-review','private':True,'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}))
        (profile/'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        desktop.native_cli(home,'lists-install','add',str(desktop.PACK),str(apple_pack),'--ignore-scripts')
        fixture=Business(profile/'stella/business');owner=fixture.tasks
        groups=[owner.create_category({'name':name,'color':color}) for name,color in [('planA','#597ac5'),('共享计划','#48a28e'),('只读清单','#b58b54'),('待确认清单','#9b72b0')]]
        records=[owner.create_todo({'title':title,'notes':'保留原有备注和身份','category_id':group['id']}) for title,group in zip(['Python 课程练习','小组展示','共享阅读','待确认事项'],groups)]
        for index,group in enumerate(groups[1:],1):
            owner.apple_bind('category',group['id'],{'apple_id':'synthetic-'+str(index),'title':group['name'],'source_id':'account1','source_name':'iCloud','can_modify':index!=2,'can_rename':index==3,'can_delete':index==3})
        operation=owner.delete_category(groups[3]['id']);owner.apple_operation_error(operation['operation_id'],'Apple 暂时无法连接，请稍后重试。')
        fixture.meta.close()
        with desktop.desktop(playwright,home,'lists-manager') as (page,errors):
            page.set_viewport_size({'width':1440,'height':1050});open_secretary(page)
            business=lambda method,args=None:desktop.rpc(page,'stellaOrganizer/business',{'request':{'domain':'tasks','method':method,'arguments':args or {}}})
            page.get_by_role('button',name='清单管理',exact=True).click()
            dialog=page.get_by_role('dialog',name='清单管理',exact=True)
            search=dialog.get_by_role('textbox',name='搜索清单',exact=True)
            name=dialog.get_by_label('清单名称',exact=True)
            name.fill('学习计划草稿');dialog.get_by_role('button',name='共享计划',exact=True).click();expect(name).to_be_disabled()
            expect(dialog.get_by_role('button',name='删除清单：共享计划',exact=True)).to_be_disabled()
            expect(dialog).to_contain_text('可添加和编辑')
            dialog.get_by_role('button',name='planA',exact=True).click();expect(name).to_have_value('学习计划草稿')
            name.fill('学习计划');dialog.get_by_role('button',name='绿色',exact=True).click();dialog.get_by_role('button',name='保存清单',exact=True).click()
            expect(dialog.get_by_role('status')).to_have_text('清单已保存')
            saved=business('get_category',{'category_id':groups[0]['id']})
            assert saved['name']=='学习计划' and saved['color']=='#48a28e'
            assert business('get_todo',{'todo_id':records[0]['id']})['notes']=='保留原有备注和身份'
            dialog.screenshot(path=str(artifacts/'06-lists-manager.png'),animations='disabled')
            search.fill('共享');expect(dialog.locator('.stella-category-list > li')).to_have_count(1);search.fill('')
            dialog.get_by_role('button',name='新建清单',exact=True).click();name.fill('课程安排')
            accounts=dialog.get_by_label('Apple 账户',exact=True);expect(accounts).to_be_enabled();accounts.select_option('account1')
            dialog.get_by_role('button',name='紫色',exact=True).click()
            dialog.screenshot(path=str(artifacts/'07-new-list.png'),animations='disabled')
            dialog.get_by_role('button',name='保存清单',exact=True).click();expect(dialog.get_by_role('status')).to_have_text('清单已保存')
            created=next(item for item in business('categories') if item['name']=='课程安排');assert created['apple']['source_id']=='account1'
            dialog.get_by_role('button',name='新建清单',exact=True).click();name.fill('学习计划');dialog.get_by_role('button',name='保存清单',exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('已有同名清单');expect(name).to_have_value('学习计划')
            dialog.get_by_role('button',name='待确认清单',exact=True).click();expect(name).to_be_disabled()
            expect(dialog).to_contain_text('Apple 暂时无法连接')
            expect(dialog).to_contain_text('等待确认，暂不可更改')
            dialog.screenshot(path=str(artifacts/'08-list-pending.png'),animations='disabled')
            dialog.get_by_role('button',name='取消待处理操作',exact=True).click();expect(name).to_be_enabled()
            assert business('get_todo',{'todo_id':records[3]['id']})['title']=='待确认事项'
            # Only isolated, disposable records are deleted by this check.
            trash=business('create_category',{'payload':{'name':'临时清单','color':'#c45d67'}})
            doomed=business('create_todo',{'payload':{'title':'可删除的测试事务','category_id':trash['id']}})
            before_children=business('categories')
            child=business('add_subtask',{'parent_id':doomed['id'],'payload':{'title':'可删除的子事务'}})
            assert child['category_id']==trash['id'] and business('categories')==before_children
            dialog.get_by_role('button',name='临时清单',exact=True).click(timeout=10000)
            dialog.get_by_role('button',name='删除清单：临时清单',exact=True).click()
            expect(dialog.get_by_role('button',name='取消',exact=True)).to_be_focused();expect(dialog).to_contain_text('全部事务及其子事务和提醒')
            dialog.screenshot(path=str(artifacts/'09-list-delete-confirmation.png'),animations='disabled')
            dialog.get_by_role('button',name='取消',exact=True).click();assert business('get_todo',{'todo_id':doomed['id']})
            dialog.get_by_role('button',name='删除清单：临时清单',exact=True).click();dialog.get_by_role('button',name='确认删除清单',exact=True).click()
            expect(dialog.get_by_role('button',name='临时清单',exact=True)).to_have_count(0)
            assert not any(item['id'] in [doomed['id'],child['id']] for item in business('list_todos'))
            dialog.get_by_role('button',name='学习计划',exact=True).click()
            page.emulate_media(color_scheme='dark');page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            dialog.screenshot(path=str(artifacts/'10-lists-dark.png'),animations='disabled')
            page.emulate_media(color_scheme='light');page.set_viewport_size({'width':390,'height':844})
            assert dialog.evaluate('e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight}')
            dialog.get_by_role('button',name='删除清单：学习计划',exact=True).scroll_into_view_if_needed()
            expect(dialog.get_by_role('button',name='删除清单：学习计划',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'11-lists-narrow.png'),animations='disabled')
            page.keyboard.press('Escape');expect(dialog).to_have_count(0)
            assert len(business('list_todos'))==len(records)
            assert not errors,errors
    print('PASS: native lists manager search, draft retention, colors/create/account, duplicate errors, separate permissions, durable cancellation, cascading test deletion, narrow and dark; personal data untouched')

if __name__=='__main__':main()

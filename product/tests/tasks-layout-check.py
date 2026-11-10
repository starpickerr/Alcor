"""Task list design in the unchanged official Desktop, with isolated organizer records."""
import json
import os
import tempfile
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from datetime import datetime,timedelta
from pathlib import Path
from playwright.sync_api import expect,sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary


def main():
    artifacts=Path(os.environ.get('STELLA_CHECK_ARTIFACTS','/private/tmp/stella-tasks-layout'))
    artifacts.mkdir(parents=True,exist_ok=True);desktop.ARTIFACTS=artifacts
    desktop.PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(desktop.PACK)))
    baseline=os.environ.get('STELLA_TASKS_BASELINE')=='1'
    desktop.verify_release()
    with fresh_test_home(prefix='stella-tasks-layout-') as temporary,sync_playwright() as playwright:
        home=Path(temporary);desktop.environment(home)
        profile=home/'dsh/profiles/desktop';profile.mkdir(parents=True);(home/'documents').mkdir()
        initialize_fresh(temporary, home / 'dsh', profile)
        (profile/'package.json').write_text(json.dumps({'name':'tasks-layout-review','private':True,'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}))
        (profile/'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        desktop.native_cli(home,'tasks-layout-install','add',str(desktop.PACK),'--ignore-scripts')
        with desktop.desktop(playwright,home,'tasks-layout') as (page,errors):
            page.set_viewport_size({'width':1440,'height':1100})
            business=lambda method,args=None:desktop.rpc(page,'alcorOrganizer/business',{'request':{'domain':'tasks','method':method,'arguments':args or {}}})
            lists=[business('create_category',{'payload':{'name':name,'color':color}}) for name,color in [('planA','#597ac5'),('学习','#48a28e'),('工作','#b58b54'),('生活','#9b72b0')]]
            day=page.evaluate('new Date().toLocaleDateString("sv-SE")');now=datetime.fromisoformat(day)
            tasks=[]
            for title,notes,offset,time,priority,group in [
                ('提交计算机网络实验报告','检查测试结果，整理实验截图与说明。',-1,None,3,1),
                ('确认下周项目排期','与同伴核对分工和交付日期。',-1,None,2,2),
                ('完成 Python 练习','完成 turtle 图形练习并提交课程作业。',0,'23:59',3,0),
                ('准备小组课堂展示','整理阅读笔记，补充两张案例图。',0,None,2,1),
                ('整理本周阅读笔记','',0,None,0,0),
                ('每周阅读 30 分钟','',1,'20:30',0,0),
                ('预约牙齿检查','',3,None,0,3),
                ('整理论文选题资料','保留可以继续研究的三个方向。',None,None,1,1),
            ]:
                payload={'title':title,'notes':notes,'priority':priority,'category_id':lists[group]['id']}
                if offset is not None:payload.update(due_on=(now+timedelta(days=offset)).date().isoformat(),due_time=time)
                tasks.append(business('create_todo',{'payload':payload}))
            business('create_todo',{'payload':{'title':'整理桌面文件','status':'completed','category_id':lists[3]['id'],'due_on':day}})
            legacy=[{'id':'legacy-check','title':'保留旧检查数据','completed':False}]
            business('update_todo',{'todo_id':tasks[2]['id'],'payload':{'tags':['课程','Python'],'flagged':True,'url':'https://example.com/course','alarms':[{'offset':-900}],'checklist':legacy,'planned_on':day}})
            child=business('add_subtask',{'parent_id':tasks[2]['id'],'payload':{'title':'核对练习附件','notes':'检查提交所需文件。'}})
            open_secretary(page);page.get_by_role('button',name='刷新',exact=True).click()
            tasks_page=page.locator('.alcor-tasks-page');expect(tasks_page).to_contain_text('完成 Python 练习')
            page.screenshot(path=str(artifacts/('00-before.png' if baseline else '01-tasks-desktop.png')),animations='disabled')
            if baseline:return
            page.set_viewport_size({'width':1280,'height':1150})
            box=tasks_page.bounding_box();box['height']=min(box['height'],1150-box['y'])
            page.screenshot(path=str(artifacts/'01-task-list-content.png'),clip=box,animations='disabled')
            page.set_viewport_size({'width':1440,'height':1100})
            nav=page.get_by_role('navigation',name='事务清单',exact=True)
            nav.get_by_role('button',name='planA',exact=True).click()
            expect(page.get_by_role('button',name='预约牙齿检查',exact=True)).to_have_count(0)
            expect(page.get_by_role('button',name='完成 Python 练习',exact=True)).to_be_visible()
            page.get_by_role('textbox',name='快速添加事务',exact=True).fill('补充阅读笔记')
            page.get_by_role('button',name='添加事务',exact=True).click()
            expect(page.get_by_role('button',name='补充阅读笔记',exact=True)).to_be_visible()
            created=next(item for item in business('list_todos') if item['title']=='补充阅读笔记')
            assert created['category_id']==lists[0]['id']
            nav.get_by_role('button',name='全部清单',exact=True).click()
            page.get_by_role('textbox',name='搜索事务',exact=True).fill('Python')
            expect(tasks_page.locator('.alcor-task-card')).to_have_count(1)
            page.get_by_role('textbox',name='搜索事务',exact=True).fill('')
            card=page.locator(f'[data-task-id="{tasks[2]["id"]}"]')
            card.click(button='right');page.get_by_role('menuitem',name='显示简介',exact=True).click()
            expect(page.get_by_role('region',name='事务详情')).to_contain_text('planA')
            page.get_by_role('button',name='关闭详情').click()
            card.get_by_role('button',name='完成事务',exact=True).click()
            expect(card).to_have_attribute('data-completed','true')
            assert business('get_todo',{'todo_id':tasks[2]['id']})['status']=='completed'
            card.get_by_role('button',name='重新打开',exact=True).click()
            page.get_by_role('button',name='完成 Python 练习',exact=True).click()
            expect(page.locator('.alcor-task-description')).to_contain_text('turtle')
            panel=page.get_by_role('region',name='事务详情',exact=True)
            expect(page.get_by_role('dialog')).to_have_count(0)
            assert panel.bounding_box()['x']>=page.locator('.alcor-task-workspace').bounding_box()['x']+page.locator('.alcor-task-workspace').bounding_box()['width']-1
            expect(panel).to_contain_text('子事务进度')
            panel.locator('.alcor-task-children').get_by_role('button',name='核对练习附件',exact=True).click()
            expect(panel.get_by_role('navigation',name='事务路径')).to_contain_text('完成 Python 练习')
            panel.get_by_role('navigation',name='事务路径').get_by_role('button',name='完成 Python 练习',exact=True).click()
            panel.locator('.alcor-task-children').get_by_role('button',name='重新打开',exact=True).click()
            panel.locator('.alcor-task-children').get_by_role('button',name='完成事务',exact=True).click()
            expect(panel).to_contain_text('子事务进度 1/1')
            assert business('get_todo',{'todo_id':child['id']})['status']=='completed'
            page.locator('.alcor-business').evaluate('e=>e.scrollIntoView({block:"start"})')
            page.screenshot(path=str(artifacts/'02-task-details.png'),animations='disabled')
            page.get_by_role('region',name='事务详情',exact=True).screenshot(path=str(artifacts/'02-task-details-content.png'),animations='disabled')
            page.get_by_role('button',name='编辑详情',exact=True).click()
            form=page.get_by_role('form',name='编辑事务',exact=True)
            expect(form.get_by_label('事务清单',exact=True)).to_have_value(lists[0]['id'])
            expect(form.get_by_label('标题',exact=True)).to_be_focused()
            expect(form.get_by_role('button',name='保存事务',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'03-task-editor.png'),animations='disabled')
            page.get_by_role('region',name='编辑事务',exact=True).screenshot(path=str(artifacts/'03-task-editor-content.png'),animations='disabled')
            form.get_by_label('标题',exact=True).fill('尚未保存的练习草稿')
            page.get_by_role('region',name='编辑事务',exact=True).get_by_role('button',name='关闭详情',exact=True).click()
            discard=page.get_by_role('dialog',name='放弃未保存的修改？',exact=True)
            discard.get_by_role('button',name='继续编辑',exact=True).click()
            expect(form.get_by_label('标题',exact=True)).to_have_value('尚未保存的练习草稿')
            tasks_page.get_by_role('button',name='预约牙齿检查',exact=True).click()
            discard.get_by_role('button',name='放弃修改',exact=True).click()
            expect(panel).to_contain_text('预约牙齿检查')
            assert business('get_todo',{'todo_id':tasks[2]['id']})['title']=='完成 Python 练习'
            tasks_page.get_by_role('button',name='完成 Python 练习',exact=True).click()
            panel.get_by_role('button',name='编辑详情',exact=True).click()
            form.get_by_label('事务详情',exact=True).fill('完成 turtle 图形练习并提交课程作业。核对提交记录。')
            form.get_by_role('button',name='保存事务',exact=True).click()
            expect(panel).to_contain_text('核对提交记录')
            saved=business('get_todo',{'todo_id':tasks[2]['id']})
            assert saved['flagged'] and saved['planned_on']==day and json.loads(saved['checklist'])==legacy
            assert json.loads(saved['tags'])==['课程','Python'] and saved['alarms']==[{'offset':-900}] and saved['url']=='https://example.com/course'
            panel.get_by_role('button',name='删除',exact=True).click()
            deletion=page.get_by_role('dialog',name='删除事务',exact=True)
            expect(deletion).to_contain_text('子事务');deletion.get_by_role('button',name='取消',exact=True).click()
            expect(panel).to_be_visible()
            page.emulate_media(color_scheme='dark');page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            page.locator('.alcor-business').evaluate('e=>e.scrollIntoView({block:"start"})')
            page.screenshot(path=str(artifacts/'06-task-panel-dark.png'),animations='disabled')
            page.emulate_media(color_scheme='light');page.set_viewport_size({'width':390,'height':844})
            assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
            expect(panel.get_by_role('button',name='编辑详情',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'07-task-panel-narrow.png'),animations='disabled')
            panel.get_by_role('button',name='编辑详情',exact=True).click()
            expect(form.get_by_role('button',name='保存事务',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'08-task-editor-narrow.png'),animations='disabled')
            form.get_by_role('button',name='取消编辑',exact=True).click()
            panel.get_by_role('button',name='关闭详情',exact=True).click()
            page.set_viewport_size({'width':1440,'height':1100})
            page.emulate_media(color_scheme='dark');page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            page.screenshot(path=str(artifacts/'04-tasks-dark.png'),animations='disabled')
            page.emulate_media(color_scheme='light');page.set_viewport_size({'width':390,'height':844})
            assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
            expect(page.get_by_role('button',name='添加事务',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'05-tasks-narrow.png'),animations='disabled')
            card.get_by_role('button',name='更多操作：完成 Python 练习',exact=True).click()
            expect(page.get_by_role('menuitem',name='删除事务',exact=True)).to_be_visible();page.keyboard.press('Escape')
            page.get_by_role('tab',name='已完成',exact=False).click()
            expect(page.get_by_role('button',name='整理桌面文件',exact=True)).to_be_visible()
            disposable=business('create_todo',{'payload':{'title':'待删除的临时事务','category_id':lists[0]['id']}})
            page.get_by_role('button',name='刷新',exact=True).click()
            page.get_by_role('tab',name='全部事务',exact=False).click()
            page.get_by_role('button',name='待删除的临时事务',exact=True).click()
            panel.get_by_role('button',name='删除',exact=True).click()
            page.get_by_role('dialog',name='删除事务',exact=True).get_by_role('button',name='确认删除',exact=True).click()
            expect(panel).to_have_count(0)
            assert not any(row['id']==disposable['id'] for row in business('list_todos'))
            assert not errors,errors
    print('PASS: native task layout, category identity/quick create, search, context menu, completion, details/editor preservation, narrow and dark')

if __name__=='__main__':main()

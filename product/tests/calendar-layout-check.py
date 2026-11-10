"""Calendar layout and navigation on the official Desktop, using disposable local records."""
import json
import os
import sys
import tempfile
from fresh_profile_fixture import fresh_test_home, initialize_fresh
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import expect,sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary
sys.path.insert(0,str(desktop.ROOT/'packages/alcor-secretary'))
from business.worker import Business


def fixtures(profile):
    owner=Business(profile/'alcor/business');calendar=owner.calendar;tasks=owner.tasks
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date();start=today-timedelta(days=(today.weekday()+1)%7)
    groups=[calendar.create_category({'name':name,'color':color}) for name,color in [('课程','#3698bc'),('工作','#8370bb'),('生活','#b58754')]]
    for group in groups[:2]:
        calendar.apple_bind('category',group['id'],{'apple_id':'synthetic-'+group['id'],'title':group['name'],'source_id':'icloud','source_name':'iCloud','can_modify':True,'can_rename':True,'can_delete':True})
    holiday=calendar.create_category({'name':'中国大陆节假日','color':'#b275c9'})
    calendar.apple_bind('category',holiday['id'],{'apple_id':'synthetic-holidays','title':holiday['name'],'source_id':'icloud','source_name':'iCloud','can_modify':False,'can_rename':False,'can_delete':False})
    task_groups=[tasks.create_category({'name':name,'color':color}) for name,color in [('学习','#48a28e'),('工作','#b58b54'),('生活','#9b72b0')]]
    for group in task_groups[:2]:
        tasks.apple_bind('category',group['id'],{'apple_id':'synthetic-'+group['id'],'title':group['name'],'source_id':'icloud','source_name':'iCloud','can_modify':True,'can_rename':True,'can_delete':True})
    for title,group in zip(['整理论文选题资料','确认项目分工','预约牙齿检查'],task_groups):
        tasks.create_todo({'title':title,'category_id':group['id']})
    plan=tasks.create_category({'name':'planA','color':'#597ac5'})
    def event(title,day,hour,end,group,notes=''):
        date=(start+timedelta(days=day)).isoformat()
        return calendar.create_event({'title':title,'description':notes,'category_id':groups[group]['id'],'start_at':f'{date}T{hour}:00+08:00','end_at':f'{date}T{end}:00+08:00','repeat_timezone':'Asia/Shanghai','location':'教学楼 A301' if group==0 else ''})
    for title,day,hour,end,group in [('计算机网络',1,'09:00','11:00',0),('项目讨论',2,'10:00','11:00',1),('人工智能基础',3,'13:30','15:10',0),('阅读与写作',4,'14:00','15:30',2),('小组课堂展示',5,'09:55','12:00',0)]:event(title,day,hour,end,group)
    offset=(today-start).days
    chosen=event('Python 课程',offset,'09:00','11:00',0,'保留原有课程备注与地点。')
    overlapping=event('课程答疑',offset,'10:00','11:30',1)
    readonly=event('共享只读会议',offset,'15:00','16:00',1)
    calendar.apple_bind('item',readonly['id'],{'apple_id':'readonly-calendar-item','can_modify':False,'can_delete':False})
    day=today.isoformat();end=(today+timedelta(days=1)).isoformat()
    all_day=calendar.create_event({'title':'课程资料更新','category_id':groups[0]['id'],'all_day':True,'start_at':f'{day}T00:00:00+08:00','end_at':f'{end}T00:00:00+08:00','repeat_timezone':'Asia/Shanghai'})
    task=tasks.create_todo({'title':'提交课程练习','notes':'完成练习后提交到课程平台。','due_on':day,'due_time':'13:30','timezone':'Asia/Shanghai','category_id':plan['id']})
    point=tasks.create_todo({'title':'核对作业附件','due_on':day,'due_time':'14:30','timezone':'Asia/Shanghai','category_id':plan['id']})
    tasks.create_todo({'title':'准备展示资料','due_on':day,'timezone':'Asia/Shanghai','category_id':plan['id']})
    owner.meta.close()
    return dict(today=day,groups=groups,event=chosen,overlapping=overlapping,readonly=readonly,all_day=all_day,task=task,point=point)


def main():
    artifacts=Path(os.environ.get('STELLA_CHECK_ARTIFACTS','/private/tmp/stella-calendar-ui-review'))
    artifacts.mkdir(parents=True,exist_ok=True);desktop.ARTIFACTS=artifacts
    desktop.PACK=Path(os.environ.get('STELLA_SECRETARY_REVIEW_PACK',str(desktop.PACK)))
    baseline=os.environ.get('STELLA_CALENDAR_BASELINE')=='1'
    desktop.verify_release()
    with fresh_test_home(prefix='stella-calendar-layout-') as temporary,sync_playwright() as playwright:
        home=Path(temporary);desktop.environment(home)
        profile=home/'dsh/profiles/desktop';profile.mkdir(parents=True);(home/'documents').mkdir()
        initialize_fresh(temporary, home / 'dsh', profile)
        (profile/'package.json').write_text(json.dumps({'name':'calendar-layout-review','private':True,'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}))
        (profile/'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n')
        desktop.native_cli(home,'calendar-layout-install','add',str(desktop.PACK),'--ignore-scripts')
        data=fixtures(profile)
        with desktop.desktop(playwright,home,'calendar-layout') as (page,errors):
            page.set_viewport_size({'width':1440,'height':1100});open_secretary(page)
            business=lambda domain,method,args=None:desktop.rpc(page,'alcorOrganizer/business',{'request':{'domain':domain,'method':method,'arguments':args or {}}})
            page.get_by_role('button',name='日程',exact=True).click()
            main=page.locator('.alcor-calendar-surface')
            expect(main.get_by_role('button',name='课程资料更新',exact=False)).to_be_visible()
            page.screenshot(path=str(artifacts/('00-before-month.png' if baseline else '02-calendar-month.png')),animations='disabled')
            page.get_by_role('tab',name='周',exact=True).click()
            expect(main.locator('.alcor-time-column .alcor-timed-event').filter(has_text='Python 课程')).to_be_visible()
            page.screenshot(path=str(artifacts/('00-before-week.png' if baseline else '01-calendar-week.png')),animations='disabled')
            if baseline:return
            sidebar=page.locator('.alcor-calendar-sidebar')
            sidebar_style=sidebar.evaluate('e=>({width:e.getBoundingClientRect().width,background:getComputedStyle(e).backgroundColor})')
            page.get_by_role('button',name='事务',exact=True).click()
            task_sidebar=page.locator('.alcor-task-sidebar')
            assert task_sidebar.evaluate('e=>({width:e.getBoundingClientRect().width,background:getComputedStyle(e).backgroundColor})')==sidebar_style
            expect(task_sidebar.get_by_role('checkbox')).to_have_count(0)
            learning=task_sidebar.get_by_role('group',name='iCloud',exact=True).get_by_role('button',name='学习',exact=True)
            learning.click();expect(learning).to_have_attribute('aria-pressed','true')
            expect(page.get_by_role('button',name='整理论文选题资料',exact=True)).to_be_visible()
            expect(page.get_by_role('button',name='预约牙齿检查',exact=True)).to_have_count(0)
            task_sidebar.get_by_role('button',name='全部清单',exact=True).click()
            page.screenshot(path=str(artifacts/'09-unified-tasks.png'),animations='disabled')
            page.get_by_role('button',name='日程',exact=True).click()
            page.get_by_role('tab',name='周',exact=True).click()
            filters=page.get_by_role('navigation',name='日历筛选',exact=True)
            assert sidebar.locator('.alcor-month-compact').bounding_box()['height'] < 260
            assert sidebar.locator('.alcor-calendar-mini').bounding_box()['y'] > filters.bounding_box()['y']+filters.bounding_box()['height']-2
            expect(filters.get_by_role('group',name='iCloud',exact=True).get_by_role('checkbox',name='课程',exact=True)).to_be_checked()
            expect(filters.get_by_role('group',name='其他',exact=True).get_by_role('checkbox',name='事务',exact=True)).to_be_checked()
            expect(sidebar).to_be_visible();expect(main.locator('.alcor-current-time')).to_have_count(1)
            filters.get_by_role('checkbox',name='课程',exact=True).uncheck()
            expect(main.locator('.alcor-time-column .alcor-timed-event').filter(has_text='Python 课程')).to_have_count(0)
            expect(main.locator('.alcor-time-column .alcor-timed-event').filter(has_text='课程答疑')).to_be_visible()
            filters.get_by_role('checkbox',name='课程',exact=True).check()
            expect(filters.get_by_role('checkbox',name='中国大陆节假日',exact=True)).to_be_enabled()
            event=main.locator(f'.alcor-time-column [data-record-id="{data["event"]["id"]}"]')
            overlap=main.locator(f'.alcor-time-column [data-record-id="{data["overlapping"]["id"]}"]')
            assert event.bounding_box()['x']!=overlap.bounding_box()['x']
            event.click(button='right');page.get_by_role('menuitem',name='显示简介',exact=True).click()
            inspector=page.get_by_role('region',name='日程详情',exact=True)
            expect(inspector).to_contain_text('保留原有课程备注');expect(inspector).to_contain_text('教学楼 A301')
            expect(page.get_by_role('dialog')).to_have_count(0)
            panel_box=inspector.bounding_box();canvas_box=page.locator('.alcor-calendar-page').bounding_box()
            assert panel_box['x'] >= canvas_box['x']+canvas_box['width']-1
            expect(inspector.get_by_role('button',name='关闭详情',exact=True)).to_be_focused()
            page.screenshot(path=str(artifacts/'03-calendar-inspector.png'),animations='disabled')
            inspector.get_by_role('button',name='关闭详情',exact=True).click()
            readonly=main.locator(f'.alcor-time-column [data-record-id="{data["readonly"]["id"]}"]')
            readonly.click(button='right');expect(page.get_by_role('menuitem',name='编辑详情',exact=True)).to_be_disabled();page.keyboard.press('Escape')
            filters.get_by_role('button',name='隐藏全部日历',exact=True).click()
            filters.get_by_role('checkbox',name='事务',exact=True).check()
            expect(main.locator('.alcor-time-column .alcor-timed-event').filter(has_text='Python 课程')).to_have_count(0)
            task=main.locator(f'.alcor-time-column [data-record-id="{data["task"]["id"]}"]')
            task.get_by_role('button',name='完成事务：提交课程练习',exact=True).click()
            expect(task).to_have_attribute('data-completed','true')
            assert business('tasks','get_todo',{'todo_id':data['task']['id']})['status']=='completed'
            filters.get_by_role('button',name='显示全部日历',exact=True).click()
            page.get_by_role('tab',name='月',exact=True).click()
            more=main.get_by_role('button',name=f'{data["today"]} 查看更多日程',exact=True)
            more.click();expect(page.get_by_role('tab',name='日',exact=True)).to_have_attribute('aria-selected','true')
            expect(sidebar).to_contain_text('iCloud')
            page.screenshot(path=str(artifacts/'04-calendar-day.png'),animations='disabled')
            page.get_by_role('tab',name='列表',exact=True).click()
            main.get_by_role('button',name='Python 课程',exact=False).filter(has=page.locator('h3')).click()
            inspector.get_by_role('button',name='编辑详情',exact=True).click()
            editor=page.get_by_role('form',name='编辑日程',exact=True)
            expect(editor.get_by_label('地点',exact=True)).to_have_value('教学楼 A301')
            page.get_by_role('region',name='编辑日程',exact=True).screenshot(path=str(artifacts/'05-calendar-editor.png'),animations='disabled')
            editor.get_by_label('标题',exact=True).fill('未保存的课程草稿')
            panel=page.get_by_role('region',name='编辑日程',exact=True)
            panel.get_by_role('button',name='关闭详情',exact=True).click()
            discard=page.get_by_role('dialog',name='放弃未保存的修改？',exact=True)
            discard.get_by_role('button',name='继续编辑',exact=True).click()
            expect(editor.get_by_label('标题',exact=True)).to_have_value('未保存的课程草稿')
            assert business('calendar','get_event',{'id':data['event']['id']})['title']=='Python 课程'
            page.screenshot(path=str(artifacts/'10-calendar-edit-panel.png'),animations='disabled')
            main.get_by_role('button',name='课程答疑',exact=False).filter(has=page.locator('h3')).click()
            expect(discard).to_be_visible();discard.get_by_role('button',name='放弃修改',exact=True).click()
            expect(inspector).to_contain_text('课程答疑')
            inspector.get_by_role('button',name='关闭详情',exact=True).click()
            expect(main.get_by_role('button',name='课程答疑',exact=False).filter(has=page.locator('h3'))).to_be_focused()
            main.get_by_role('button',name='Python 课程',exact=False).filter(has=page.locator('h3')).click()
            inspector.get_by_role('button',name='编辑详情',exact=True).click()
            editor.get_by_label('标题',exact=True).fill('Python 课程 · 已调整')
            editor.get_by_label('结束',exact=True).fill(f'{data["today"]}T08:00')
            editor.get_by_role('button',name='保存日程',exact=True).click()
            expect(panel).to_contain_text('end_at must not precede start_at')
            alert=panel.locator('.alcor-notice[role=alert]')
            expect(alert).to_contain_text('end_at must not precede start_at')
            assert alert.bounding_box()['height'] < 100
            expect(editor.get_by_role('button',name='保存日程',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'14-calendar-validation-notice.png'),animations='disabled')
            expect(editor.get_by_label('标题',exact=True)).to_have_value('Python 课程 · 已调整')
            assert business('calendar','get_event',{'id':data['event']['id']})['title']=='Python 课程'
            editor.get_by_label('结束',exact=True).fill(f'{data["today"]}T11:00')
            editor.get_by_role('button',name='保存日程',exact=True).click()
            expect(panel).to_have_count(0)
            saved=business('calendar','get_event',{'id':data['event']['id']})
            assert saved['title']=='Python 课程 · 已调整' and saved['location']=='教学楼 A301'
            main.get_by_role('button',name='Python 课程 · 已调整',exact=False).filter(has=page.locator('h3')).click()
            page.set_viewport_size({'width':390,'height':844})
            assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
            expect(inspector.get_by_role('button',name='编辑详情',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'11-calendar-panel-narrow.png'),animations='disabled')
            inspector.get_by_role('button',name='编辑详情',exact=True).click()
            expect(editor.get_by_label('标题',exact=True)).to_be_focused()
            editor.get_by_label('标题',exact=True).fill('Python 课程 · 窄窗口修改')
            editor.get_by_role('button',name='保存日程',exact=True).click()
            expect(panel).to_have_count(0)
            page.set_viewport_size({'width':1440,'height':1100})
            page.get_by_role('tab',name='周',exact=True).click()
            main.locator(f'.alcor-time-column [data-record-id="{data["event"]["id"]}"] .alcor-calendar-open').click()
            inspector.get_by_role('button',name='编辑详情',exact=True).click()
            page.locator('.alcor-business').evaluate('e=>e.scrollIntoView({block:"start"})')
            expect(editor.get_by_role('button',name='保存日程',exact=True)).to_be_in_viewport()
            page.screenshot(path=str(artifacts/'12-calendar-edit-desktop.png'),animations='disabled')
            page.emulate_media(color_scheme='dark');page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            page.screenshot(path=str(artifacts/'13-calendar-panel-dark.png'),animations='disabled')
            page.emulate_media(color_scheme='light')
            panel.get_by_role('button',name='关闭详情',exact=True).click()
            page.get_by_role('tab',name='年',exact=True).click();expect(main.locator('.alcor-year-grid > section')).to_have_count(12)
            page.get_by_role('tab',name='周',exact=True).click()
            page.emulate_media(color_scheme='dark');page.wait_for_function('document.body.hasAttribute("data-ds-dark-theme")')
            mini=sidebar.locator('.alcor-calendar-mini').bounding_box()
            assert mini['y']+mini['height'] <= page.viewport_size['height']
            page.screenshot(path=str(artifacts/'06-calendar-dark.png'),animations='disabled')
            page.emulate_media(color_scheme='light');page.set_viewport_size({'width':390,'height':844})
            assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
            expect(sidebar).not_to_be_visible()
            page.screenshot(path=str(artifacts/'07-calendar-narrow.png'),animations='disabled')
            page.get_by_role('button',name='日历与日期',exact=False).click();expect(sidebar).to_be_visible()
            sidebar.get_by_role('button',name=data['today'],exact=True).click()
            expect(page.get_by_role('button',name='日历与日期',exact=False)).to_be_focused()
            page.get_by_role('button',name='日历与日期',exact=False).click()
            filters.get_by_role('button',name='隐藏全部日历',exact=True).click();filters.get_by_role('checkbox',name='课程',exact=True).check()
            page.get_by_role('button',name='日历与日期',exact=False).click();expect(sidebar).not_to_be_visible()
            expect(main.locator('.alcor-calendar-task')).to_have_count(0)
            page.get_by_role('tab',name='月',exact=True).click()
            assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
            page.screenshot(path=str(artifacts/'08-calendar-month-narrow.png'),animations='disabled')
            page.set_viewport_size({'width':1440,'height':1100})
            page.get_by_role('button',name='分类管理',exact=True).click();expect(page.get_by_role('dialog',name='分类管理')).to_contain_text('课程');page.keyboard.press('Escape')
            saved=business('calendar','get_event',{'id':data['event']['id']})
            assert saved['title']=='Python 课程 · 窄窗口修改' and saved['description']=='保留原有课程备注与地点。' and saved['category_id']==data['groups'][0]['id']
            assert not errors,errors
    print('PASS: official Desktop calendar sidebar/views/date navigation, overlapping events, task parity/completion, more-day navigation, context/readonly/details/editor, dark and narrow; isolated data only')

if __name__=='__main__':main()

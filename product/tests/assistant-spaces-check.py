"""Space navigation, memory settings and read-only QQ in an isolated official DSH."""
import json
import re
import os
import sys
import tempfile
import yaml
from threading import Event, Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop

ROOT = Path(os.environ.get('ALCOR_ASSISTANT_CHECK_ROOT', '/private/tmp/alcor-assistant-implementation'))
ARTIFACTS = Path(os.environ.get('STELLA_CHECK_ARTIFACTS', str(ROOT / 'ui')))


@contextmanager
def synthetic_qq_profiles():
    import websocket
    connection=websocket.create_connection('ws://127.0.0.1:36433/ws',header=['Authorization: Bearer synthetic-only'],suppress_origin=True)
    connection.settimeout(None)
    sent=[]
    historyReads=0
    def answer():
        nonlocal historyReads
        try:
            while packet:=connection.recv():
                row=json.loads(packet); action=row.get('action')
                if action and action.startswith('send_'):sent.append(action)
                data={'group_name':'纸样讨论组','member_count':3} if action=='get_group_info' else {'nickname':'林舟'} if action=='get_stranger_info' else {}
                if action=='get_group_msg_history':
                    historyReads+=1
                    bodies=['QUIET_GROUP_HISTORY']+(['PLATFORM_LIVE_UPDATE'] if historyReads>1 else [])
                    data={'messages':[{'message_seq':i+1,'user_id':10002,'time':1794247920+i,'sender':{'nickname':'阿棠'},'message':[{'type':'text','data':{'text':text}}]} for i,text in enumerate(bodies)]}
                connection.send(json.dumps({'status':'ok','retcode':0,'data':data,'echo':row.get('echo')}))
        except (OSError,websocket.WebSocketConnectionClosedException):
            pass
    thread=Thread(target=answer,daemon=True)
    thread.start()
    try:yield
    finally:
        connection.close();thread.join(timeout=2)
    assert not sent,'Opening desktop QQ history should never send a QQ message'


def mode(page, name):
    picker = page.get_by_role('button', name=re.compile('切换使用模式'))
    picker.wait_for()
    if not picker.get_attribute('aria-label').endswith(name):
        picker.click()
        page.get_by_role('menuitem', name=name, exact=True).click()


def current(page):
    return page.evaluate('JSON.parse(localStorage.getItem("dsh.sessions.current"))?.sessionId')


def selected(page):
    return page.evaluate('''() => {
      const id = JSON.parse(localStorage.getItem('dsh.sessions.current'))?.sessionId
      return window[Symbol.for('@alcor/secretary.workspace-navigation')].sessions.getSnapshot().byId[id]
    }''')


def new_alcor(page):
    before = current(page)
    page.locator('.alcor-sidebar-chat').click()
    page.wait_for_function('(before) => JSON.parse(localStorage.getItem("dsh.sessions.current"))?.sessionId !== before', arg=before)
    expect(page.locator('[data-composer-input][contenteditable="true"]')).to_be_visible()
    return current(page)


def settings(page):
    page.get_by_role('button', name=re.compile(r'^(Account menu|账号菜单|More|更多)$')).last.click()
    page.get_by_role('menuitem', name=re.compile(r'^(Settings|设置)')).click()
    dialog = page.get_by_role('dialog', name=re.compile(r'^(Settings|设置)$'))
    dialog.get_by_role('button', name='Alcor', exact=True).click()
    return dialog


@contextmanager
def delayed_model():
    # The fixture changes only the loopback model in the stopped synthetic Profile.
    assert ROOT.resolve().as_posix().startswith('/private/tmp/alcor-assistant-')
    config=ROOT/'official-dsh/profiles/spaces-check/cordis.patch.yml'
    original=config.read_bytes()
    marker='inflight-'+str(uuid4())
    arrival,release=Event(),Event()
    hits=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            turn=any(marker in str(m.get('content')) and m.get('role')=='user' for m in body.get('messages',[]))
            if turn and any(tool.get('function',{}).get('name')=='alcor_business' for tool in body.get('tools',[])):
                hits.append(True);arrival.set();release.wait(25)
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
            for delta,stop in [({'role':'assistant','content':'原会话任务已完成 '+marker},None),({},'stop')]:
                data=json.dumps({'id':'local-test','choices':[{'index':0,'delta':delta,'finish_reason':stop}]})
                self.wfile.write(('data: '+data+'\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        rows=yaml.safe_load(original)
        adapter=next(row for row in rows if row.get('id')=='llm-pi-ai')
        adapter['config']['providers']['synthetic']['baseURL']='http://127.0.0.1:'+str(server.server_port)+'/v1'
        config.write_text(json.dumps(rows))
        yield marker,arrival,release,hits
    finally:
        release.set();server.shutdown();server.server_close();thread.join();config.write_bytes(original)

def check(page, label, delayed=None):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_locator_handler(page.get_by_role('button', name='Continue', exact=True), lambda button: button.click())
    page.set_viewport_size({'width':1280,'height':900})
    mode(page, 'Alcor')
    restored=desktop.rpc(page,'alcorSecretary/deliverySettings',{'request':{}})
    assert next(row for row in restored['targets'] if row['route']['sessionId']=='onebot-private-10001')['until'] is None
    assert desktop.rpc(page,'alcorSecretary/memoryState',{'request':{}})['initiative'] is False
    first = new_alcor(page)
    row = selected(page)
    assert Path(row['cwd']).resolve() == (ROOT / 'official-dsh/profiles/spaces-check/alcor/workspaces/alcor').resolve(), row
    second = new_alcor(page)
    assert second != first
    project = ROOT / ('ui-project-'+str(uuid4())[:8])
    project.mkdir(exist_ok=True)
    result = page.evaluate('''async path => {
      const navigation = window[Symbol.for('@alcor/secretary.workspace-navigation')]
      const project = await navigation.createWorkspace({path, title:'Alcor '+path.split('/').pop()})
      await navigation.startSession(project.workspaceId)
      return project
    }''', str(project))
    assert Path(selected(page)['cwd']).resolve() == project.resolve()
    if delayed:
        marker,arrival,release,hits=delayed
        source=current(page)
        desktop.rpc(page,'session/prompt',{'request':{'sessionId':source,'requestId':str(uuid4()),'content':[{'type':'text','text':marker}],'mode':'queue'}})
        assert arrival.wait(12),'Original Alcor session did not start its request'
        mode(page,'DeepSeek Harness')
        release.set()
        mode(page,'Alcor')
        expect(page.get_by_text('原会话任务已完成 '+marker,exact=True).last).to_be_visible(timeout=20000)
        assert current(page)==source and len(hits)==1
        print('PASS switching spaces during a running model request returns to the original session and executes once',flush=True)
    new_alcor(page)
    assert Path(selected(page)['cwd']).resolve() != project.resolve()
    mode(page, 'DeepSeek Harness')
    previous=current(page)
    button = page.get_by_role('button', name='开始 DSH 对话', exact=True)
    if button.count():
        button.click()
    else:
        page.get_by_role('button', name=re.compile(r'^(New session|New Session|新对话|新会话|新建会话)')).first.click()
    page.wait_for_function('''previous => {
      const n=window[Symbol.for('@alcor/secretary.workspace-navigation')]
      const id=JSON.parse(localStorage.getItem('dsh.sessions.current'))?.sessionId
      return id!==previous && n.sessions.getSnapshot().byId[id]?.projectionValues?.agentPreset === 'standard'
    }''',arg=previous)
    assert Path(selected(page)['cwd']).resolve() == (ROOT / 'official-dsh/profiles/spaces-check/alcor/workspaces/dsh').resolve()
    assert page.get_by_text(result['title'],exact=True).count() == 0
    expect(page.locator('.alcor-qq-browser')).to_have_count(0)
    shared = page.evaluate('''async path => {
      const n=window[Symbol.for('@alcor/secretary.workspace-navigation')]
      const project=await n.createWorkspace({path,title:'DSH '+path.split('/').pop()})
      await n.startSession(project.workspaceId)
      return project
    }''',str(project))
    assert shared['workspaceId']==result['workspaceId']
    expect(page.get_by_text(shared['title'],exact=True)).to_be_visible()
    assert page.get_by_text(result['title'],exact=True).count()==0
    old_title=shared['title']
    shared['title']='改名 '+old_title
    page.evaluate('''async ({workspaceId,title})=>window[Symbol.for('@alcor/secretary.workspace-navigation')].renameWorkspace(workspaceId,title)''',shared)
    page.reload();expect(page.get_by_text(shared['title'],exact=True)).to_be_visible()
    page.evaluate('''async id=>window[Symbol.for('@alcor/secretary.workspace-navigation')].deleteWorkspace(id)''',shared['workspaceId'])
    expect(page.get_by_text(shared['title'],exact=True)).to_have_count(0)
    mode(page, 'Alcor')
    expect(page.get_by_text(result['title'],exact=True)).to_be_visible()
    assert page.get_by_text(shared['title'],exact=True).count()==0
    folder=page.get_by_role('treeitem',name='QQ',exact=True)
    expect(folder).to_be_visible()
    assert folder.evaluate("el => !!el.closest('[role=tree]')")
    assert page.locator('[data-row-key^="workspace:"]').filter(has_text=re.compile(r'^30001$')).count()==0
    if folder.get_attribute('aria-expanded')=='true':folder.click()
    expect(page.locator('.alcor-qq-browser section button')).to_have_count(0)
    folder.focus();folder.press('Enter')
    expect(folder).to_have_attribute('aria-expanded','true')
    page.screenshot(path=str(ARTIFACTS / (label + '-qq-workspaces.png')))
    page.locator('.alcor-qq-browser button[title*="30001"]').click()
    expect(page.locator('.alcor-qq-history article')).not_to_have_count(0)
    expect(page.get_by_text('QUIET_GROUP_HISTORY',exact=True)).to_be_visible()
    expect(page.get_by_text('PLATFORM_LIVE_UPDATE',exact=True)).to_be_visible(timeout=15000)
    assert page.locator('[data-composer-input]').count() == 0
    assert page.locator('.alcor-bear-compose').count() == 0
    assert 'PRIVATE_MEMORY_CANARY' not in page.locator('.alcor-qq-history').inner_text()
    info_tab=page.get_by_role('button',name='会话资料',exact=True)
    info_tab.click()
    profile_info=desktop.rpc(page,'alcorSecretary/qqConversation',{'request':{'sessionId':'onebot-group-30001'}})
    assert profile_info and profile_info['queue'] in ('queue','steer'),profile_info
    expect(page.get_by_text('忙时消息',exact=True)).to_be_visible()
    expect(page.get_by_text('回应方式',exact=True)).to_be_visible()
    message=page.locator('.alcor-qq-thread article button').last
    message.click()
    expect(page.locator('.alcor-qq-message-meta')).to_be_visible()
    message.click()
    expect(page.get_by_text('已连接',exact=True)).to_be_visible()
    expect(page.locator('.alcor-qq-identity')).to_contain_text('3 人')
    page.screenshot(path=str(ARTIFACTS / (label + '-qq-readonly.png')))
    page.set_viewport_size({'width':390,'height':844})
    frame=page.locator('[data-sidebar-drawer]')
    expect(frame).to_have_count(1)
    if frame.get_attribute('data-sidebar-collapsed'):
        toggle=page.locator('[data-shell-leading] button:visible').first
        if toggle.count():toggle.click()
        else:page.keyboard.press('Meta+b')
    page.locator('.alcor-qq-browser button[title*="10001"]').click()
    expect(page.locator('.alcor-sidebar[role="dialog"]')).to_have_count(0)
    expect(page.locator('.alcor-qq-identity h1')).to_have_text('林舟')
    info_tab.click()
    expect(page.get_by_text('插件管理员',exact=True)).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1')
    page.wait_for_function("() => document.querySelector('.alcor-sidebar').getBoundingClientRect().right<=1")
    header=page.locator('.alcor-qq-identity').bounding_box()
    leading=page.locator('[data-shell-leading]').bounding_box()
    assert not leading or header['y']>=leading['y']+leading['height'],(header,leading)
    page.screenshot(path=str(ARTIFACTS / (label + '-qq-narrow.png')),animations='disabled')
    page.set_viewport_size({'width':1280,'height':900})
    page.screenshot(path=str(ARTIFACTS / (label + '-qq-private.png')),animations='disabled')
    page.reload()
    expect(page.locator('.alcor-qq-history')).to_be_visible()
    assert page.locator('[data-composer-input]').count() == 0
    expect(page.get_by_role('treeitem',name='QQ',exact=True)).to_have_attribute('aria-expanded','true')
    print('PASS QQ is a native workspace row, the mixed group folder is hidden, and read-only history survives reload',flush=True)
    dialog = settings(page)
    tabs=dialog.get_by_role('navigation',name='Alcor 设置分类')
    pet_tab=tabs.get_by_role('button',name='小熊',exact=True)
    pet_tab.focus(); pet_tab.press('Enter')
    expect(dialog.get_by_role('heading',name='小熊宠物',exact=True)).to_be_visible()
    page.screenshot(path=str(ARTIFACTS / (label + '-pet.png')))
    tabs.get_by_role('button',name='项目空间',exact=True).click()
    finder=dialog.get_by_role('searchbox',name='查找当前空间项目',exact=True)
    if finder.count():finder.fill(result['title'])
    expect(dialog.get_by_text(result['title'],exact=True)).to_be_visible()
    assert dialog.get_by_text(shared['title'],exact=True).count()==0
    expect(tabs.get_by_role('button',name='人格与记忆',exact=True)).to_have_count(0)
    expect(dialog.get_by_label('角色设定',exact=True)).to_have_count(0)
    dialog.press('Escape')
    expect(dialog).not_to_be_visible()
    page.locator('.alcor-sidebar nav').get_by_role('button',name='人格',exact=True).click()
    persona = page.locator('.alcor-persona-page')
    expect(persona.get_by_role('heading',name='人格',exact=True)).to_be_visible()
    soul = persona.get_by_label('角色设定',exact=True)
    expect(soul).to_be_visible()
    examples = ['我喜欢动手试一个小办法；意见不同时，也会说清自己的理由。', '我喜欢先做一个小实验，和你一起看结果。']
    soul.fill(next(item for item in examples if item != soul.input_value()))
    if not persona.get_by_label('对方说', exact=True).count():
        persona.get_by_role('button', name='添加示例', exact=True).click()
    persona.get_by_label('对方说', exact=True).first.fill('要不要试试今晚看月亮？')
    persona.get_by_label('助手回应', exact=True).first.fill('等云散了，我想听你说说月亮像什么。')
    persona.get_by_role('button',name='保存人格',exact=True).click()
    expect(persona.get_by_text('已保存',exact=True)).to_be_visible()
    transfer = desktop.rpc(page, 'alcorSecretary/exportIdentity', {'request': {}})
    assert transfer['soul'] == soul.input_value()
    assert transfer['examples'][0] == {'user':'要不要试试今晚看月亮？', 'assistant':'等云散了，我想听你说说月亮像什么。'}
    for removed in ['导入人格','导出人格','迁移长期经历','档案口令']:
        expect(persona.get_by_text(removed,exact=True)).to_have_count(0)
    imported_name='岚舟' if transfer['name']!='岚舟' else '阿舟'
    persona.get_by_label('助手名称',exact=True).fill(imported_name)
    persona.get_by_role('button',name='保存人格',exact=True).click()
    expect(persona.get_by_text('已保存',exact=True)).to_be_visible()
    persona.get_by_role('tab',name='主动交流',exact=True).click()
    consent = persona.get_by_role('switch',name='允许 Alcor 主动跟进',exact=True)
    if consent.get_attribute('aria-checked') == 'true':consent.click()
    expect(consent).to_have_attribute('aria-checked','false')
    consent.click()
    expect(consent).to_have_attribute('aria-checked','true')
    delivery=persona.get_by_role('switch',name='桌面便笺',exact=True)
    if delivery.get_attribute('aria-checked')=='false':delivery.click()
    expect(delivery).to_have_attribute('aria-checked','true')
    delivery.click()
    expect(consent).to_have_attribute('aria-checked','true')
    expect(delivery).to_have_attribute('aria-checked','false')
    delivery.click()
    target=persona.locator('[data-channel-session="onebot-private-10001"]')
    if target.get_by_role('button',name='撤销授权',exact=True).count():
        target.get_by_role('button',name='撤销授权',exact=True).click()
        expect(target.get_by_role('button',name='允许主动发送',exact=True)).to_be_enabled()
    target.get_by_role('button',name='允许主动发送',exact=True).click()
    expect(target.get_by_text('持续授权，可随时撤销。',exact=True)).to_be_visible()
    assert next(row for row in desktop.rpc(page,'alcorSecretary/deliverySettings',{'request':{}})['targets'] if row['route']['sessionId']=='onebot-private-10001')['until'] is None
    target.get_by_role('button',name='撤销授权',exact=True).click()
    expect(consent).to_have_attribute('aria-checked','true')
    expect(target.get_by_role('button',name='允许主动发送',exact=True)).to_be_enabled()
    page.screenshot(path=str(ARTIFACTS / (label + '-channel-delivery-settings.png')))
    expect(persona.get_by_text('留意今天到期的事务',exact=True)).to_have_count(0)
    expect(persona.get_by_text('定时事务回顾',exact=True)).to_have_count(0)
    consent.click()
    expect(consent).to_have_attribute('aria-checked','false')
    persona.get_by_role('tab',name='长期记忆',exact=True).click()
    cards=persona.locator('.alcor-memory-card')
    assert cards.count()>=2
    expand=persona.get_by_role('button',name='展开全文',exact=True).first
    expand.click()
    expect(persona.get_by_role('button',name='收起',exact=True).first).to_have_attribute('aria-expanded','true')
    persona.get_by_role('button',name='收起',exact=True).first.click()
    page.screenshot(path=str(ARTIFACTS / (label + '-memory-cards.png')))
    record = persona.locator('.alcor-memory-row').filter(has_text='PRIVATE_MEMORY_CANARY').first
    record.get_by_role('button',name='管理',exact=True).click()
    editor = page.get_by_role('dialog',name='管理记忆',exact=True)
    editor.get_by_label('记忆内容').fill('UI_CORRECTED_CANARY')
    editor.get_by_role('button',name='保存更正',exact=True).click()
    expect(editor).not_to_be_visible()
    persona.locator('.alcor-memory-row').filter(has_text='UI_CORRECTED_CANARY').get_by_role('button',name='管理',exact=True).click()
    page.get_by_role('dialog',name='管理记忆',exact=True).get_by_role('button',name='忘记',exact=True).click()
    expect(page.get_by_role('dialog',name='管理记忆',exact=True)).not_to_be_visible()
    expect(persona.get_by_text('UI_CORRECTED_CANARY',exact=True)).to_have_count(0)
    persona.get_by_role('tab',name='人格设定',exact=True).click()
    soul.scroll_into_view_if_needed()
    page.screenshot(path=str(ARTIFACTS / (label + '-memory.png')))
    page.set_viewport_size({'width':390,'height':844})
    soul.scroll_into_view_if_needed()
    page.screenshot(path=str(ARTIFACTS / (label + '-memory-narrow.png')))
    assert soul.bounding_box()['width'] < 390
    page.emulate_media(color_scheme='dark')
    page.wait_for_function("""() => document.body.hasAttribute('data-ds-dark-theme') && getComputedStyle(document.querySelector('#alcor-identity-name')).backgroundColor === 'rgb(29, 29, 32)'""")
    page.screenshot(path=str(ARTIFACTS / (label + '-memory-dark.png')))
    page.set_viewport_size({'width':1280,'height':900})
    page.get_by_role('button', name='简报', exact=True).first.click()
    page.set_viewport_size({'width':1280,'height':900})
    page.emulate_media(color_scheme='light')
    for title in ['事务','日程','账单','简报','自动化']:
        page.get_by_role('button',name=title,exact=True).first.click()
        if title=='自动化':expect(page.get_by_text('自动化任务',exact=True).first).to_be_visible()
        else:expect(page.get_by_role('heading',name=title,exact=True).first).to_be_visible()
        if title=='事务': page.screenshot(path=str(ARTIFACTS/(label+'-tasks-layout.png')))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth+1'), title
        page.set_viewport_size({'width':1280,'height':900})
    print('PASS settings sections, project labels, shared project rename and removal, and five business pages at narrow widths',flush=True)
    assert not errors, errors
    print('PASS ' + label + ': distinct defaults, project ownership, fresh chats, QQ readonly/reload, memory edit/forget, narrow/dark, no page errors',flush=True)


def main():
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        if '--desktop' in sys.argv:
            authority=ROOT / 'official-dsh/profiles/spaces-check/alcor/memory/personal/alcor-authority.json'
            if authority.exists():
                fixture=json.loads(authority.read_text())
                fixture.update(lastDismissed=0,followups=[])
                fixture.pop('taskDismissedDay',None)
                authority.write_text(json.dumps(fixture))
            desktop.APP=Path(os.environ.get('STELLA_CHECK_DESKTOP_APP',str(Path.home() / 'Applications/DeepSeek Harness.app')))
            desktop.ARTIFACTS=ARTIFACTS
            desktop.verify_release()
            old_environment=desktop.environment
            def environment(home):
                result=old_environment(home)
                result['DSH_HOME']=str(ROOT / 'official-dsh')
                offset=datetime.now(timezone.utc).hour-12
                result['TZ']=f'Etc/GMT{offset:+}' if offset else 'UTC'
                return result
            desktop.environment=environment
            profile=ROOT / 'official-dsh/profiles/desktop'
            if not profile.exists():profile.symlink_to(ROOT / 'official-dsh/profiles/spaces-check',target_is_directory=True)
            with delayed_model() as delayed, desktop.desktop(p,ROOT,'assistant-spaces') as (page,errors):
                with synthetic_qq_profiles():
                    check(page,'official-desktop',delayed)
                assert not errors,errors
        else:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page()
            page.goto(json.loads((ROOT / 'browser-url.json').read_text())['url'])
            check(page,'official-host-browser')
            browser.close()

if __name__=='__main__':main()

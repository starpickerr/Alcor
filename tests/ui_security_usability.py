"""Production permission and MCP runtime controls, with a synthetic API."""
import json
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

base='http://127.0.0.1:5173'
grants=[]
revision=[1]
reject=[False]
owner_restricted=[False]
capabilities={'network':False,'credentials':False,'revision':0}
saved=[]


def backend(route):
    request=route.request
    path=urlparse(request.url).path
    data={'status':'success','sessions':[],'agents':[]}
    if path=='/api/sessions/access':
        if request.method=='POST':
            body=json.loads(request.post_data)
            saved.append(body)
            assert body['id']=='one'
            if reject[0]:
                data={'status':'error','message':'授权版本已变化，请刷新后重试'}
            elif body['action']=='enable_mcp':
                assert body['connection']=='mcp:synthetic' and body['revision']==str(revision[0])
                grants.append(dict(grant_id='grant-'+str(revision[0]),subject='session:default:one',resource={'kind':'connection','id':'mcp:synthetic'},operations=body['operations'],purposes=['interactive'],revoked=False,effective=True,revision=1,valid_until=None))
                revision[0]+=1
            elif body['action']=='revoke':
                next(row for row in grants if row['grant_id']==body['grant_id']).update(revoked=True,effective=False,revision=2)
                revision[0]+=1
        if data['status']=='success':
            data.update(grants=grants,revision=str(revision[0]),subjects=[dict(id='session:default:one',kind='session',subject='one')],available=[dict(kind='connection',id='mcp:synthetic',name='synthetic',operations=['read','write','delete','external_send','system_execute'])],restrictions=[],owner_restrictions=[dict(id='mcp:synthetic',allowed=False,scopes=[],revision=1)] if owner_restricted[0] else [],inherited=[])
    elif path=='/api/security/runtime':
        if request.method=='POST':
            body=json.loads(request.post_data)
            saved.append(body)
            if body['action']=='capabilities':
                assert body['digest']=='a'*64 and body['revision']==capabilities['revision']
                capabilities.update(network=body['network'],credentials=body['credentials'],revision=capabilities['revision']+1)
            else:
                assert body['action']=='prepare-package'
        data.update(isolation={'status':'enabled','reason':'Synthetic OS support'},runtimes=[dict(kind='mcp',id='synthetic',digest='a'*64,mode='stopped',running=False,reason='Server has not started.',capabilities=capabilities,package_launcher=True,can_rebuild=False)])
    route.fulfill(status=200,content_type='application/json',body=json.dumps(data),headers={'Access-Control-Allow-Origin':base,'Access-Control-Allow-Credentials':'true'})


with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1280,'height':900})
    errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.add_init_script("localStorage.setItem('stella_lang','zh')")
    page.route('http://127.0.0.1:9876/**',backend)
    page.goto(base)
    page.wait_for_load_state('networkidle')
    page.evaluate("async()=>{const m=await import('/src/__test__/SecurityAuthorizationHarness.tsx');m.mountSecurityUsabilityTest()}")
    section=page.get_by_role('region',name='当前对话的 MCP')
    expect(section.get_by_text('缺少资源授权',exact=True)).to_be_visible()
    owner_restricted[0]=True
    section.get_by_text('高级范围与撤销',exact=True).click()
    section.get_by_text('高级范围与撤销',exact=True).click()
    expect(section.get_by_text('连接访问受限',exact=True)).to_be_visible()
    expect(section.get_by_role('button',name='在当前对话使用',exact=True)).to_be_disabled()
    owner_restricted[0]=False
    section.get_by_text('高级范围与撤销',exact=True).click()
    section.get_by_text('高级范围与撤销',exact=True).click()
    expect(section.get_by_text('缺少资源授权',exact=True)).to_be_visible()
    section.get_by_role('button',name='在当前对话使用',exact=True).click()
    expect(section.get_by_role('button',name='已授权',exact=True)).to_be_disabled()
    assert saved[-1]['operations']==['read']
    section.get_by_label('使用范围').select_option('all')
    reject[0]=True
    section.get_by_role('button',name='在当前对话使用',exact=True).click()
    expect(section.get_by_role('alert')).to_contain_text('授权版本已变化')
    expect(section.get_by_label('使用范围')).to_have_value('all')
    reject[0]=False
    section.get_by_role('button',name='在当前对话使用',exact=True).click()
    expect(section.get_by_role('button',name='已授权',exact=True)).to_be_disabled()
    assert 'system_execute' in saved[-1]['operations']
    section.get_by_text('高级范围与撤销',exact=True).click()
    section.get_by_role('button',name='撤销授权',exact=True).first.click()
    expect(section.get_by_text('已撤销',exact=True)).to_be_visible()
    section.get_by_text('高级范围与撤销',exact=True).click()
    page.get_by_label('允许进程联网',exact=True).check()
    page.get_by_label('允许注入此服务的专用 env 凭据',exact=True).check()
    page.get_by_role('button',name='保存运行能力',exact=True).click()
    expect(page.get_by_text('已保存，旧进程已停止，下次连接时生效。',exact=True)).to_be_visible()
    assert saved[-1]['network'] and saved[-1]['credentials']
    page.get_by_role('button',name='准备固定版本包',exact=True).click()
    expect(page.get_by_text('包入口已准备，运行时使用固定环境。',exact=True)).to_be_visible()
    page.screenshot(path='/tmp/stella-security-usability-desktop.png',full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path='/tmp/stella-security-usability-narrow.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors,errors
    browser.close()
print('PASS: conversation scope, stale-save draft, revocation, runtime capabilities, fixed-package action, desktop/narrow layouts')

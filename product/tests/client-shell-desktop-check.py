"""Single-window native client acceptance using disposable profiles only."""
import argparse
import hashlib
import json
import os
import plistlib
from pathlib import Path
import signal
import subprocess
import time
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect
import official_desktop_check as desktop
import importlib.util
spec=importlib.util.spec_from_file_location('isolated',Path(__file__).with_name('isolated-environments-check.py'))
isolated=importlib.util.module_from_spec(spec);spec.loader.exec_module(isolated)

parser=argparse.ArgumentParser();parser.add_argument('--app',required=True);parser.add_argument('--config',required=True);args=parser.parse_args()
app=Path(args.app).resolve();config_file=Path(args.config).resolve();config=json.loads(config_file.read_text());base=config_file.parent
assert (base/'.client-shell-test').read_text().strip()=='disposable-single-window-check-v1'
for key in ['environmentRoot','workbenchDsh','workbenchHome','workbenchWorkspace','clientData']:
    assert Path(config[key]).resolve().is_relative_to(base), 'Test profiles must stay inside the explicitly owned directory'
isolated.APP=Path(config['officialApp']);isolated.ARTIFACTS=base
paths=isolated.prepare(Path(config['environmentRoot']))
fixture=base/'ecosystem-plugin'
if not fixture.exists():isolated.fixture_plugin(fixture)
title='Single-window isolated task '+uuid4().hex[:8]
original={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.iterdir()}
for kind in paths:
    isolated.native_cli(paths[kind],kind+'-probe-install','add',fixture)
    # No model credentials or model calls are needed to verify local drafts.
    (Path(paths[kind]['profile'])/'cordis.patch.yml').write_text('- id: ui-settings-models\n  config:\n    credentialOnboarding: false\n')


def start(playwright,phase):
    active=Path(config['clientData'])/'DevToolsActivePort'
    active.unlink(missing_ok=True)
    log=(base/(phase+'.log')).open('w')
    executable=plistlib.loads((app/'Contents/Info.plist').read_bytes())['CFBundleExecutable']
    process=subprocess.Popen([str(app/'Contents/MacOS'/executable),'--remote-debugging-port=0'],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        deadline=time.monotonic()+45
        while not active.exists():
            assert process.poll() is None,'Client exited before DevTools was ready'
            assert time.monotonic()<deadline,'Client startup timed out'
            time.sleep(.1)
        browser=playwright.chromium.connect_over_cdp('http://127.0.0.1:'+active.read_text().splitlines()[0])
        deadline=time.monotonic()+60
        control=browser.new_browser_cdp_session()
        while True:
            control.send('Target.getTargets')
            pages=[page for context in browser.contexts for page in context.pages]
            owner=next((page for page in pages if page.url.startswith('file:')),None)
            if owner:
                state=owner.evaluate('alcorShell.state()')
                assert 'failed' not in state['phases'].values(), 'An owned Host failed to boot'
                if all(value=='ready' for value in state['phases'].values()):
                    browser.close()
                    browser=playwright.chromium.connect_over_cdp('http://127.0.0.1:'+active.read_text().splitlines()[0])
                    control=browser.new_browser_cdp_session()
                    pages=[page for context in browser.contexts for page in context.pages]
                    if len(pages)==3:
                        owner=next(page for page in pages if page.url.startswith('file:'))
                        break
            assert time.monotonic()<deadline,'Both environments did not start'
            time.sleep(.1)
        content=[page for page in pages if page.url.startswith('dsh-app:')]
        alcor=next(page for page in content if page.evaluate('globalThis.__ALCOR_APPLICATION__')=='alcor')
        dsh=next(page for page in content if page!=alcor)
        return process,browser,owner,alcor,dsh,log
    except:
        process.send_signal(signal.SIGTERM);process.wait(timeout=25);log.close()
        raise


def stop(process,browser,log):
    browser.close();process.send_signal(signal.SIGTERM);process.wait(timeout=25);log.close()
    assert process.returncode==0,'Client did not stop gracefully'


def unavailable(page,endpoint):
    return page.evaluate('''async endpoint=>{const r=await fetch('/api/'+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({type:'client-request',rpcId:crypto.randomUUID(),method:endpoint,payload:{args:{}}})});return {status:r.status,body:await r.text()}}''',endpoint)

def dismiss_notices(page):
    for _ in range(4):
        if page.get_by_role('button',name='继续',exact=True).is_visible():
            page.get_by_role('button',name='继续',exact=True).click()
        if page.get_by_role('heading',name='开始使用',exact=True).is_visible():
            page.get_by_role('button',name='关闭',exact=True).click()
        page.wait_for_timeout(120)


with sync_playwright() as playwright:
    process,browser,owner,alcor,dsh,log=start(playwright,'initial')
    try:
        for page in [alcor,dsh]:assert page.evaluate('typeof globalThis.alcorShell')=='undefined','Content cannot control the client shell'
        owner.evaluate("Promise.all([alcorShell.switchMode('dsh'),alcorShell.switchMode('alcor'),alcorShell.switchMode('dsh')])")
        assert owner.evaluate('alcorShell.state()')['mode']=='dsh'
        assert alcor.evaluate("getComputedStyle(document.querySelector('[data-slot=\"shell.top\"]').parentElement.parentElement).paddingTop")=='0px'
        for page in [owner,alcor,dsh]:page.set_viewport_size({'width':720,'height':600})
        assert owner.locator('#mode').is_visible()
        assert alcor.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for page in [owner,alcor,dsh]:page.set_viewport_size({'width':1280,'height':856})
        owner.evaluate("alcorShell.switchMode('dsh')")
        dismiss_notices(dsh)
        expect(dsh.get_by_role('button',name='插件',exact=True)).to_be_visible(timeout=30000)
        assert unavailable(dsh,'alcorSecretary/overview')['status']==404
        assert unavailable(dsh,'alcorOrganizer/business')['status']==404
        assert desktop.rpc(alcor,'alcorSecretary/overview')
        bundles=desktop.rpc(dsh,'pluginManager/listBundles');assert not any(row['name'] in ['@alcor/launcher','@alcor/secretary'] for row in bundles if row.get('installed'))
        probes={kind:isolated.probe(page) for kind,page in [('alcor',alcor),('dsh',dsh)]}
        assert probes['alcor']['home']!=probes['dsh']['home']
        desktop.rpc(alcor,'alcorOrganizer/business',{'request':{'domain':'tasks','method':'create_todo','arguments':{'payload':{'title':title}}}})
        for kind,page in [('alcor',alcor),('dsh',dsh)]:
            owner.evaluate('(kind)=>alcorShell.switchMode(kind)',kind)
            dismiss_notices(page)
            page.get_by_role('button',name='新建会话',exact=True).first.click()
            dismiss_notices(page)
            page.locator('[contenteditable="true"][role="textbox"]').fill(kind+' unsent draft')
            page.evaluate('(kind)=>localStorage.setItem("single-window-check",kind)',kind)
        for kind,page in [('alcor',alcor),('dsh',dsh),('alcor',alcor)]:
            owner.evaluate('(kind)=>alcorShell.switchMode(kind)',kind)
            expect(page.locator('[contenteditable="true"][role="textbox"]')).to_have_text(kind+' unsent draft')
            assert page.evaluate('localStorage.getItem("single-window-check")')==kind
        assert len([page for context in browser.contexts for page in context.pages])==3,'Switching must not create another window or page'
        owner.evaluate("alcorShell.switchMode('dsh')")
        dsh.get_by_role('button',name='Environment probe',exact=True).click()
        expect(dsh.get_by_role('heading',name='Unchanged ecosystem plugin')).to_be_visible()
        dsh.screenshot(path=str(base/'workbench-plugin.png'))
        owner.evaluate("alcorShell.switchMode('alcor')")
        alcor.get_by_role('button',name='事务',exact=True).click()
        expect(alcor.get_by_role('button',name=title,exact=True)).to_be_visible()
        alcor.screenshot(path=str(base/'alcor-task.png'))
        owner.evaluate("alcorShell.switchMode('dsh')")
    finally:stop(process,browser,log)
    process,browser,owner,alcor,dsh,log=start(playwright,'restart')
    try:
        assert owner.evaluate('alcorShell.state()')['mode']=='dsh'
        for kind,page in [('alcor',alcor),('dsh',dsh)]:
            assert page.evaluate('localStorage.getItem("single-window-check")')==kind
            assert isolated.probe(page)['boots']==probes[kind]['boots']+1
        owner.evaluate("alcorShell.switchMode('alcor')")
        dismiss_notices(alcor)
        alcor.get_by_role('button',name='事务',exact=True).click();expect(alcor.get_by_role('button',name=title,exact=True)).to_be_visible()
        assert unavailable(dsh,'alcorSecretary/overview')['status']==404
    finally:stop(process,browser,log)
assert original=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.iterdir()},'Third-party package must remain unmodified'
print('PASS: one native window; top-level switch controller; isolated Host APIs and browser storage; unsent drafts retained on switch; unchanged plugin; task/mode/state survive graceful restart; no launcher plugin')

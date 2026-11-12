"""Single-window native client acceptance using disposable profiles only."""
import argparse
import hashlib
import json
import plistlib
from pathlib import Path
import signal
import socket
import subprocess
import time
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect
import official_desktop_check as desktop
import importlib.util
spec=importlib.util.spec_from_file_location('isolated',Path(__file__).with_name('isolated-environments-check.py'))
isolated=importlib.util.module_from_spec(spec)
spec.loader.exec_module(isolated)

parser=argparse.ArgumentParser()
parser.add_argument('--app',required=True)
parser.add_argument('--config',required=True)
args=parser.parse_args()
app=Path(args.app).resolve()
config_file=Path(args.config).resolve()
config=json.loads(config_file.read_text())
base=config_file.parent
assert (base/'.client-shell-test').read_text().strip()=='disposable-single-window-check-v1'
for key in ['environmentRoot','workbenchDsh','workbenchHome','workbenchWorkspace','clientData']:
    assert Path(config[key]).resolve().is_relative_to(base), 'Test profiles must stay inside the explicitly owned directory'
isolated.APP=Path(config['officialApp'])
isolated.ARTIFACTS=base
paths=isolated.prepare(Path(config['environmentRoot']))
fixture=base/'ecosystem-plugin'
if not fixture.exists():
    isolated.fixture_plugin(fixture)
title='Single-window isolated task '+uuid4().hex[:8]
original={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.iterdir()}
for kind in paths:
    isolated.native_cli(paths[kind],kind+'-probe-install','add',fixture)
    # No model credentials or model calls are needed to verify local drafts.
    (Path(paths[kind]['profile'])/'cordis.patch.yml').write_text('- id: ui-settings-models\n  config:\n    credentialOnboarding: false\n')
probe_data=Path(paths['alcor']['dsh'])/'plugins-data/environment-probe'
probe_data.mkdir(parents=True,exist_ok=True)
(probe_data/'options.json').write_text(json.dumps({'detachedChild':True}))
if isolated.PLUGIN_INFO.exists():
    isolated.native_cli(paths['alcor'],'legacy-info-install','add',isolated.PLUGIN_INFO)
if (Path(config['clientData'])/'alcor-extension-installed.json').exists():
    isolated.native_cli(paths['dsh'],'reset-test-extension','add',isolated.ROOT/'.local/artifacts/alcor-extension-0.1.0.tgz')
    manifest_file=Path(paths['dsh']['profile'])/'package.json'
    manifest=json.loads(manifest_file.read_text())
    selected=manifest['dsh']['profile']['bundles']
    if '@alcor/extension' not in selected:
        selected.append('@alcor/extension')
    manifest_file.write_text(json.dumps(manifest,indent=2)+'\n')


def start(playwright,phase,disabled=False):
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
                if state['phases']['dsh']=='ready' and state['phases']['alcor']==('disabled' if disabled else 'ready'):
                    browser.close()
                    browser=playwright.chromium.connect_over_cdp('http://127.0.0.1:'+active.read_text().splitlines()[0])
                    control=browser.new_browser_cdp_session()
                    pages=[page for context in browser.contexts for page in context.pages]
                    if len(pages)==(2 if disabled else 3):
                        owner=next(page for page in pages if page.url.startswith('file:'))
                        break
            assert time.monotonic()<deadline,'Both environments did not start'
            time.sleep(.1)
        content=[page for page in pages if page.url.startswith('dsh-app:')]
        alcor=next((page for page in content if page.evaluate('globalThis.__ALCOR_APPLICATION__')=='alcor'),None)
        assert bool(alcor)!=disabled
        dsh=next(page for page in content if page!=alcor)
        return process,browser,owner,alcor,dsh,log
    except:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=25)
        log.close()
        raise


def stop(process,browser,log):
    browser.close()
    process.send_signal(signal.SIGTERM)
    process.wait(timeout=25)
    log.close()
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


def reattach(playwright,browser):
    browser.close()
    active=Path(config['clientData'])/'DevToolsActivePort'
    deadline=time.monotonic()+15
    while True:
        browser=playwright.chromium.connect_over_cdp('http://127.0.0.1:'+active.read_text().splitlines()[0])
        pages=[page for context in browser.contexts for page in context.pages]
        owner=next(page for page in pages if page.url.startswith('file:'))
        content=[page for page in pages if page.url.startswith('dsh-app:')]
        alcor=next((page for page in content if page.evaluate('globalThis.__ALCOR_APPLICATION__')=='alcor'),None)
        dsh=next((page for page in content if page!=alcor),None)
        if alcor and dsh:
            return browser,owner,alcor,dsh
        browser.close()
        assert time.monotonic()<deadline,'Re-enabled native view was not discoverable'
        time.sleep(.1)


def extension_card(dsh):
    dsh.get_by_role('button',name='插件',exact=True).click()
    card=dsh.locator('[data-plugin-package="@alcor/extension"]')
    expect(card).to_be_visible(timeout=20000)
    expect(card.locator('.alcor-client-plugin-scope')).to_have_text('Alcor 环境')
    return card


def wait_phase(owner,phase,mode=None,timeout=60):
    deadline=time.monotonic()+timeout
    while True:
        state=owner.evaluate('alcorShell.state()')
        if state['phases']['alcor']==phase and (mode is None or state['mode']==mode):
            return state
        assert time.monotonic()<deadline,'Alcor transition did not settle: '+str(state)
        time.sleep(.1)


with sync_playwright() as playwright:
    process,browser,owner,alcor,dsh,log=start(playwright,'initial')
    try:
        for page in [alcor,dsh]:
            assert page.evaluate('typeof globalThis.alcorShell')=='undefined','Content cannot control the client shell'
        owner.evaluate("Promise.all([alcorShell.switchMode('dsh'),alcorShell.switchMode('alcor'),alcorShell.switchMode('dsh')])")
        assert owner.evaluate('alcorShell.state()')['mode']=='dsh'
        assert alcor.evaluate("getComputedStyle(document.querySelector('[data-slot=\"shell.top\"]').parentElement.parentElement).paddingTop")=='0px'
        for page in [owner,alcor,dsh]:
            page.set_viewport_size({'width':720,'height':600})
        assert owner.locator('#mode').is_visible()
        assert alcor.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for page in [owner,alcor,dsh]:
            page.set_viewport_size({'width':1280,'height':856})
        owner.evaluate("alcorShell.switchMode('dsh')")
        dismiss_notices(dsh)
        expect(dsh.get_by_role('button',name='插件',exact=True)).to_be_visible(timeout=30000)
        assert unavailable(dsh,'alcorSecretary/overview')['status']==404
        assert unavailable(dsh,'alcorOrganizer/business')['status']==404
        assert desktop.rpc(alcor,'alcorSecretary/overview')
        bundles=desktop.rpc(dsh,'pluginManager/listBundles')
        assert not any(row['name'] in ['@alcor/launcher','@alcor/secretary'] for row in bundles if row.get('installed'))
        assert any(row['name']=='@alcor/extension' and row['enabled'] for row in bundles)
        for page in [alcor,dsh]:
            assert not any(row['name']=='@alcor/plugin-info' and row.get('installed') for row in desktop.rpc(page,'pluginManager/listBundles'))
        card=extension_card(dsh)
        expect(card.get_by_role('switch')).to_be_checked()
        expect(dsh.locator('[data-plugin-package="dsh-environment-probe"] .alcor-client-plugin-scope')).to_have_text('DSH 环境')
        probes={kind:isolated.probe(page) for kind,page in [('alcor',alcor),('dsh',dsh)]}
        assert probes['alcor']['home']!=probes['dsh']['home']
        desktop.rpc(alcor,'alcorOrganizer/business',{'request':{'domain':'tasks','method':'create_todo','arguments':{'payload':{'title':title}}}})
        for kind,page in [('alcor',alcor),('dsh',dsh)]:
            print('Checking unsent draft:',kind,flush=True)
            owner.evaluate('(kind)=>alcorShell.switchMode(kind)',kind)
            dismiss_notices(page)
            page.screenshot(path=str(base/(kind+'-before-draft.png')))
            (base/(kind+'-before-draft.txt')).write_text(page.locator('body').inner_text())
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
    finally:
        stop(process,browser,log)
    process,browser,owner,alcor,dsh,log=start(playwright,'restart')
    try:
        assert owner.evaluate('alcorShell.state()')['mode']=='dsh'
        for kind,page in [('alcor',alcor),('dsh',dsh)]:
            assert page.evaluate('localStorage.getItem("single-window-check")')==kind
            assert isolated.probe(page)['boots']==probes[kind]['boots']+1
        owner.evaluate("alcorShell.switchMode('alcor')")
        dismiss_notices(alcor)
        alcor.get_by_role('button',name='事务',exact=True).click()
        expect(alcor.get_by_role('button',name=title,exact=True)).to_be_visible()
        assert unavailable(dsh,'alcorSecretary/overview')['status']==404
        child=json.loads((probe_data/'child.json').read_text())['pid']
        origin=alcor.evaluate('globalThis.__DSH_TRANSPORT__.streamBaseUrl')
        from urllib.parse import urlsplit
        port=urlsplit(origin).port
        result=desktop.rpc(dsh,'pluginManager/setBundleEnabled',{'name':'@alcor/extension','enabled':False})
        assert result['application']!='failed',result
        print('Native Alcor disable:',result['application'],flush=True)
        wait_phase(owner,'disabled','dsh')
        control=browser.new_browser_cdp_session()
        deadline=time.monotonic()+10
        while len([row for row in control.send('Target.getTargets')['targetInfos'] if row['type']=='page' and row['url'].startswith('dsh-app:')])!=1:
            assert time.monotonic()<deadline,'Disabling must destroy the private view'
            time.sleep(.1)
        try:
            child_state=subprocess.check_output(['/bin/ps','-p',str(child),'-o','stat='],text=True).strip()
        except subprocess.CalledProcessError:
            child_state='exited'
        (base/'disabled-child-state.txt').write_text(child_state+'\n')
        assert child_state=='exited' or child_state.startswith('Z'),'Detached plugin child still running: '+child_state
        with socket.socket() as connection:
            assert connection.connect_ex(('127.0.0.1',port))!=0,'Disabled Alcor API still listening'
        assert unavailable(dsh,'alcorSecretary/overview')['status']==404
    finally:
        stop(process,browser,log)
    process,browser,owner,alcor,dsh,log=start(playwright,'disabled-restart',disabled=True)
    try:
        assert owner.evaluate('alcorShell.state()')['mode']=='dsh'
        card=extension_card(dsh)
        expect(card.get_by_role('switch')).not_to_be_checked()
        assert isolated.probe(dsh)['boots']==probes['dsh']['boots']+2
        removed=desktop.rpc(dsh,'pluginManager/removeBundle',{'name':'@alcor/extension'})
        assert removed['application']!='failed',removed
        assert not any(row['name']=='@alcor/extension' and row.get('installed') for row in desktop.rpc(dsh,'pluginManager/listBundles'))
    finally:
        stop(process,browser,log)
    process,browser,owner,alcor,dsh,log=start(playwright,'uninstalled-restart',disabled=True)
    try:
        assert not any(row['name']=='@alcor/extension' and row.get('installed') for row in desktop.rpc(dsh,'pluginManager/listBundles')),'Uninstalled Alcor was automatically reinstalled'
        installed=desktop.rpc(dsh,'pluginManager/installBundle',{'spec':str(isolated.ROOT/'.local/artifacts/alcor-extension-0.1.0.tgz')})
        assert installed['application']!='failed',installed
        wait_phase(owner,'ready')
        browser,owner,alcor,dsh=reattach(playwright,browser)
        owner.evaluate("alcorShell.switchMode('alcor')")
        dismiss_notices(alcor)
        alcor.get_by_role('button',name='事务',exact=True).click()
        expect(alcor.get_by_role('button',name=title,exact=True)).to_be_visible()
        owner.evaluate("alcorShell.switchMode('dsh')")
        card=extension_card(dsh)
        card.get_by_role('switch').click()
        wait_phase(owner,'disabled')
        card=extension_card(dsh)
        expect(card.get_by_role('switch')).not_to_be_checked()
        card.get_by_role('switch').click()
        wait_phase(owner,'ready')
        browser,owner,alcor,dsh=reattach(playwright,browser)
        assert unavailable(dsh,'alcorSecretary/overview')['status']==404
        assert isolated.probe(dsh)['boots']==probes['dsh']['boots']+3,'Alcor toggles restarted the workbench'
        owner.evaluate("alcorShell.switchMode('alcor')")
        print('Final re-enable state:',owner.evaluate('alcorShell.state()'),flush=True)
        alcor.screenshot(path=str(base/'before-reenabled-task.png'))
        alcor.get_by_role('button',name='事务',exact=True).click()
        expect(alcor.get_by_role('button',name=title,exact=True)).to_be_visible()
        alcor.screenshot(path=str(base/'reenabled-task.png'))
    finally:
        stop(process,browser,log)
assert original=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in fixture.iterdir()},'Third-party package must remain unmodified'
print('PASS: one native window; native Alcor plugin enable/disable/uninstall/reinstall; disabled and uninstalled survive restart; detached child and API stop; data restored on re-enable; built-in scope badges without manager plugin; workbench unaffected; unchanged ecosystem plugin')

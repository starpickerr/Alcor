"""Two unchanged official Desktops, separate homes, and one unchanged standard plugin."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / '.local/isolated-desktop-check'
APP = Path(os.environ.get('STELLA_CHECK_DESKTOP_APP', str(Path.home() / 'Applications/DeepSeek Harness.app')))
VERSION = json.loads((ROOT / 'packages/alcor-secretary/package.json').read_text())['version']
PACK = ROOT / f'.local/artifacts/alcor-secretary-{VERSION}.tgz'
LAUNCHER = ROOT / '.local/artifacts/alcor-launcher-0.1.0.tgz'
INFO_VERSION = json.loads((ROOT / 'packages/alcor-plugin-info/package.json').read_text())['version']
PLUGIN_INFO = ROOT / f'.local/artifacts/alcor-plugin-info-{INFO_VERSION}.tgz'


def fixture_plugin(directory):
    directory.mkdir()
    (directory / 'package.json').write_text(json.dumps({
        'name': 'dsh-environment-probe', 'version': '1.0.0', 'type': 'module',
        'exports': {'.': './index.js', './client': './client.js', './package.json': './package.json'},
        'dsh': {'bundle': {'patch': './cordis.patch.yml'}, 'client': {'platform': 'web', 'inject': ['slots'], 'external': ['react']}},
    }))
    (directory / 'cordis.patch.yml').write_text('- insert:\n    - id: environment-probe\n      name: dsh-environment-probe\n')
    (directory / 'index.js').write_text('''import {mkdirSync,readFileSync,writeFileSync,existsSync} from 'node:fs'
import {spawn} from 'node:child_process'
import {join} from 'node:path'
export const inject=['webServer','profileContext','tools']
export function apply(ctx){
  const directory=join(ctx.profileContext.home,'plugins-data','environment-probe')
  mkdirSync(directory,{recursive:true})
  const file=join(directory,'boots.json')
  let boots=0
  try{boots=JSON.parse(readFileSync(file,'utf8')).boots}catch(error){if(error.code!=='ENOENT')throw error}
  writeFileSync(file,JSON.stringify({boots:++boots}))
  const options=join(directory,'options.json')
  if(existsSync(options) && JSON.parse(readFileSync(options,'utf8')).detachedChild){
    const child=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{detached:true,stdio:'ignore'})
    child.unref();writeFileSync(join(directory,'child.json'),JSON.stringify({pid:child.pid}))
  }
  ctx.tools.register({name:'environment_probe',description:'Synthetic test probe',parameters:{},
    output:{schema:{type:'string'},render:(_args,value)=>[{type:'text',text:value}]},execute:async()=>'probe'})
  ctx.effect(()=>ctx.webServer.register({kind:'exact',path:'/environment-probe',handler:(_req,res)=>{
    res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify({boots,home:ctx.profileContext.home}))
  }}))
}
''')
    (directory / 'client.js').write_text('''window.__ModuleLoader__.load({id:'dsh-environment-probe',factory(require){
const React=require('react')
return {inject:['slots'],apply(ctx){
  ctx.slots.inject('sidebar.panellist',()=>ctx.slots.register({name:'sidebar.panellist',id:'environment-probe',label:()=> 'Environment probe'},()=>null))
  ctx.slots.inject('main',()=>ctx.slots.register({name:'main',key:'environment-probe'},()=>React.createElement('h1',null,'Unchanged ecosystem plugin')))
}}
}})
''')


def prepare(root):
    script = '''import {prepareEnvironment,environmentFor} from './scripts/product/environments.mjs';
      const root=process.argv[1],result={};
      for(const kind of ['alcor','dsh']){const paths=await prepareEnvironment(root,kind);result[kind]={...paths,env:environmentFor(paths)}}
      console.log(JSON.stringify(result));'''
    return json.loads(subprocess.check_output(['node', '--input-type=module', '-e', script, str(root)], cwd=ROOT, text=True))


def native_cli(paths, phase, *args):
    cli = APP / 'Contents/Resources/runtime/cli/bin/dsh'
    prefix = [str(cli)]
    env = paths['env'].copy()
    if os.name == 'nt':
        prefix = [str(APP / 'DeepSeek Harness.exe'), '--expose-internals',
                  str(APP / 'resources/app.asar/dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js')]
        env['ELECTRON_RUN_AS_NODE'] = '1'
    result = subprocess.run([*prefix, 'plugin', '--profile', 'desktop', *map(str, args), *(['--ignore-scripts'] if args[0]=='add' else [])],
                            cwd=paths['workspace'], env=env, capture_output=True, encoding='utf-8', errors='replace', timeout=180)
    (ARTIFACTS / f'{phase}.log').write_text(desktop.redact(result.stdout + result.stderr))
    assert result.returncode == 0, f'{phase}: ' + desktop.redact(result.stderr)[-3000:]


@contextmanager
def running(playwright, paths, phase):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    with (ARTIFACTS / f'{phase}.log').open('w') as log:
        process = subprocess.Popen([str(APP / 'Contents/MacOS/DeepSeek Harness'), '--user-data-dir=' + paths['userData'],
                                    '--remote-debugging-port=' + str(port)], cwd=paths['workspace'], env=paths['env'],
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        browser = None
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                assert process.poll() is None, f'{phase}: official Desktop exited'
                try:
                    with urlopen(f'http://127.0.0.1:{port}/json/version', timeout=1) as response:
                        endpoint = json.load(response)['webSocketDebuggerUrl']
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError(f'{phase}: Desktop debugger unavailable')
            browser = playwright.chromium.connect_over_cdp(endpoint)
            page = desktop.enter_workspace(browser.contexts[0], process)
            try:
                yield page, process
            except BaseException:
                if not page.is_closed():
                    page.screenshot(path=str(ARTIFACTS / f'{phase}-failed.png'))
                    (ARTIFACTS / f'{phase}-failed.txt').write_text(page.locator('body').inner_text())
                raise
        finally:
            if browser:
                browser.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def probe(page):
    return page.evaluate("async()=>{const r=await fetch('/environment-probe');return {status:r.status,...await r.json()}}")


def unavailable(page, endpoint):
    return page.evaluate('''async endpoint=>{
      const r=await fetch('/api/'+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({type:'client-request',rpcId:crypto.randomUUID(),method:endpoint,payload:{args:{}}})});
      return {status:r.status,body:await r.text()}
    }''', endpoint)


def stop_launched(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def main():
    assert PACK.is_file() and LAUNCHER.is_file() and PLUGIN_INFO.is_file(), 'Build the launcher and plugin-info archives first'
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    desktop.APP = APP
    desktop.verify_release()
    before = desktop.digest(APP / 'Contents/Resources/app.asar')
    with tempfile.TemporaryDirectory(prefix='alcor-two-desktops-', dir='/private/tmp') as temporary, sync_playwright() as playwright:
        parent = Path(temporary)
        plugin = parent / 'standard-plugin'
        fixture_plugin(plugin)
        original = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in plugin.iterdir()}
        environments = prepare(parent / 'owned')
        native_cli(environments['dsh'], 'install-launcher', 'add', LAUNCHER)
        record = Path(environments['alcor']['base']) / '.desktop-process.json'
        cold_pid = None
        try:
            with running(playwright, environments['dsh'], 'cold-native-launcher') as (dsh, _):
                dsh.get_by_role('button', name='Alcor', exact=True).click()
                dsh.get_by_role('button', name='打开 Alcor', exact=True).click()
                expect(dsh.get_by_role('status')).to_have_text('已在独立窗口打开。', timeout=90000)
                cold_pid = json.loads(record.read_text())['pid']
                manifest = json.loads((Path(environments['alcor']['profile']) / 'package.json').read_text())
                source = manifest['dependencies']['@alcor/secretary']
                assert source.startswith('file:')
                saved = (Path(environments['alcor']['profile']) / source[5:]).resolve()
                assert saved.is_relative_to(Path(environments['alcor']['packages']))
                assert hashlib.sha256(saved.read_bytes()).digest() == hashlib.sha256(PACK.read_bytes()).digest()
                stop_launched(cold_pid)
                cold_pid = None
        finally:
            if cold_pid:
                stop_launched(cold_pid)
        native_cli(environments['alcor'], 'install-plugin-info', 'add', PLUGIN_INFO)
        for kind, paths in environments.items():
            native_cli(paths, f'install-probe-{kind}', 'add', plugin)
        with running(playwright, environments['alcor'], 'alcor') as (alcor, alcor_process), running(playwright, environments['dsh'], 'dsh') as (dsh, dsh_process):
            expect(alcor.get_by_role('button', name='事务', exact=True)).to_be_visible(timeout=30000)
            expect(dsh.get_by_role('button', name='事务', exact=True)).to_have_count(0)
            expect(alcor.get_by_role('button', name='切换使用模式，当前 Alcor')).to_have_count(0)
            alcor.get_by_role('button', name='插件', exact=True).click()
            expect(alcor.locator('.alcor-plugin-scope').first).to_have_text('Alcor 环境')
            expect(alcor.locator('.alcor-scope-options')).to_have_count(0)
            assert alcor_process.pid != dsh_process.pid
            for kind, page in [('alcor', alcor), ('dsh', dsh)]:
                state = probe(page)
                assert state == {'status': 200, 'boots': 1, 'home': environments[kind]['dsh']}, state
                expect(page.get_by_role('button', name='Environment probe', exact=True)).to_be_visible()
                page.get_by_role('button', name='Environment probe', exact=True).click()
                expect(page.get_by_role('heading', name='Unchanged ecosystem plugin')).to_be_visible()
                page.screenshot(path=str(ARTIFACTS / f'{kind}-plugin.png'))
            bundles = desktop.rpc(dsh, 'pluginManager/listBundles')
            assert not any(p['name'].startswith('@alcor/') and p['name']!='@alcor/launcher' and p.get('installed') for p in bundles)
            rejected = unavailable(dsh, 'alcorSecretary/overview')
            assert rejected['status'] != 200 or '"ok":false' in rejected['body'].replace(' ', ''), rejected
            created = desktop.rpc(alcor, 'alcorOrganizer/business', {'request': {'domain': 'tasks', 'method': 'create_todo',
                                    'arguments': {'payload': {'title': 'Isolated application task'}}}})
            assert created
            assert probe(dsh)['boots'] == 1
        with running(playwright, environments['alcor'], 'alcor-restart') as (alcor, _):
            assert probe(alcor)['boots'] == 2
            expect(alcor.get_by_role('button', name='事务', exact=True)).to_be_visible(timeout=30000)
            alcor.get_by_role('button', name='事务', exact=True).click()
            expect(alcor.get_by_text('Isolated application task', exact=True)).to_be_visible()
            alcor.screenshot(path=str(ARTIFACTS / 'alcor-restart.png'))
        assert json.loads((Path(environments['dsh']['dsh']) / 'plugins-data/environment-probe/boots.json').read_text())['boots'] == 1
        counter = Path(environments['alcor']['dsh']) / 'plugins-data/environment-probe/boots.json'
        launched = None
        try:
            with running(playwright, environments['dsh'], 'native-launcher') as (dsh, _):
                dsh.get_by_role('button', name='Alcor', exact=True).click()
                dsh.get_by_role('button', name='打开 Alcor', exact=True).click()
                expect(dsh.get_by_role('status')).to_have_text('已在独立窗口打开。', timeout=45000)
                launched = json.loads(record.read_text())['pid']
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if json.loads(counter.read_text())['boots'] == 3:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError('Native launcher did not start its independent application')
                dsh.get_by_role('button', name='打开 Alcor', exact=True).click()
                expect(dsh.get_by_role('status')).to_have_text('已在独立窗口打开。', timeout=45000)
                assert json.loads(record.read_text())['pid'] == launched
                assert json.loads(counter.read_text())['boots'] == 3
                dsh.screenshot(path=str(ARTIFACTS / 'native-launcher.png'))
            os.kill(launched, 0)  # Closing the workbench must leave the independent Alcor running.
        finally:
            if launched:
                stop_launched(launched)
        native_cli(environments['alcor'], 'remove-probe-alcor', 'remove', 'dsh-environment-probe')
        with running(playwright, environments['dsh'], 'dsh-after-other-uninstall') as (dsh, _):
            assert probe(dsh)['boots'] == 3
            expect(dsh.get_by_role('button', name='Environment probe', exact=True)).to_be_visible()
        assert original == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in plugin.iterdir()}
    assert desktop.digest(APP / 'Contents/Resources/app.asar') == before
    print('PASS: two unchanged official Desktops; native launcher and focus; Alcor survives workbench close; independent plugins/data/restart/uninstall; unchanged ecosystem plugin; DSH lacks Alcor business API')


if __name__ == '__main__':
    main()

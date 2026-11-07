"""Packaged skills -> native DSH skill tool -> real ledger; synthetic model/data only."""
import json
import re
import tempfile
import threading
import time
from uuid import uuid4
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary


class Provider(BaseHTTPRequestHandler):
    phase = 0
    failure = None
    def log_message(self, *_args): pass
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        names = {tool['function']['name'] for tool in payload.get('tools', [])}
        delta, finish = {'content': 'Synthetic title'}, 'stop'
        try:
            if 'stella_overview' in names:
                assert 'skill' in names, 'Missing native skill tool: '+','.join(sorted(names))
                text = json.dumps(payload['messages'], ensure_ascii=False)
                if self.phase == 0:
                    expected=['stella-bills','stella-tasks','stella-calendar','stella-briefing','stella-automation','stella-elearning','stella-mail','stella-apple-sync']
                    assert all(name in text for name in expected), 'Missing advertised skills: '+','.join(name for name in expected if name not in text)
                    assert '新版使用整数 amount_minor' not in text, 'Full bodies must not be preloaded'
                    name, args = 'skill', {'name':'stella-bills'}
                elif self.phase == 1:
                    content = payload['messages'][-1]['content']
                    assert '<skill_content name="stella-bills">' in content
                    assert 'amount_minor' in content
                    examples = [json.loads(raw) for raw in re.findall(r'```json\n([\s\S]*?)\n```', content)]
                    args = next(row for row in examples if row['method'] == 'create_ledger')
                    name = 'stella_business'
                elif self.phase == 2:
                    saved = json.loads(payload['messages'][-1]['content'])['result']
                    assert saved['amount_minor'] == 1390 and saved['currency'] == 'CNY'
                    delta = {'content':'已按插件 Skill 记账：咖啡 13.90 元。'}
                    name = None
                else:
                    raise AssertionError('Unexpected business turn')
                type(self).phase += 1
                if name:
                    delta={'tool_calls':[{'index':0,'id':'skill-workflow-'+str(self.phase),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
                    finish='tool_calls'
        except Exception as error:
            type(self).failure = repr(error)
            self.send_error(500)
            return
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        for choice in [{'delta':{'role':'assistant'},'finish_reason':None},{'delta':delta,'finish_reason':None},{'delta':{},'finish_reason':finish}]:
            data={'id':'skill-native-test','object':'chat.completion.chunk','created':1,'model':'synthetic','choices':[{'index':0,**choice}]}
            self.wfile.write(('data: '+json.dumps(data)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()


def main():
    desktop.verify_release();desktop.ARTIFACTS.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-plugin-skills-') as temporary,sync_playwright() as playwright:
            home=Path(temporary);env=desktop.environment(home)
            docs=home/'documents';docs.mkdir()
            profile=Path(env['DSH_HOME'])/'profiles/desktop';profile.mkdir(parents=True)
            (profile/'package.json').write_text(json.dumps({'name':'native-skill-test','private':True,'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}))
            (profile/'cordis.patch.yml').write_text(f'''- id: workspace-controller
  config:
    documentsDirectory: {docs}
- id: agent-default-model
  config:
    provider: synthetic
    model: synthetic
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: synthetic
            name: Synthetic
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
''')
            credential=Path(env['DSH_HOME'])/'.credentials.yaml';credential.write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n');credential.chmod(0o600)
            packages=[]
            for package in ['secretary','elearning','mail','apple-sync']:
                version=json.loads((desktop.ROOT/f'packages/stella-{package}/package.json').read_text())['version']
                packages.append(str(desktop.ROOT/f'.local/artifacts/stella-{package}-{version}.tgz'))
            desktop.native_cli(home,'skills-install','add',*packages,'--ignore-scripts')
            with desktop.desktop(playwright,home,'plugin-skills') as (page,errors):
                open_secretary(page)
                created=desktop.rpc(page,'session/create',{'request':{'cwd':str(docs),'agentPreset':'stella'}})
                desktop.rpc(page,'session/selectModel',{'request':{'sessionId':created['sessionId'],'provider':'synthetic','model':'synthetic'}})
                desktop.rpc(page,'session/prompt',{'request':{'sessionId':created['sessionId'],'requestId':str(uuid4()),'mode':'queue','clientTimeZone':'Asia/Shanghai','content':[{'type':'text','text':'使用插件的记账技能，记录 2026-11-06 09:00 的咖啡支出，人民币 13.90 元。'}]}})
                deadline=time.monotonic()+40
                while Provider.phase<3 and time.monotonic()<deadline and not Provider.failure:
                    page.wait_for_timeout(100)
                if Provider.phase<3:
                    events=desktop.rpc(page,'session/events',{'request':{'address':{'kind':'session','sessionId':created['sessionId']},'maxMessages':20}})
                    (desktop.ARTIFACTS/'skill-session-events.json').write_text(json.dumps(events,ensure_ascii=False))
                assert not Provider.failure,Provider.failure
                assert Provider.phase==3,f'Native skill/model flow did not complete: phase={Provider.phase}'
                rows=desktop.rpc(page,'stellaOrganizer/business',{'request':{'domain':'bills','method':'list_ledger','arguments':{'year':2026,'month':11}}})
                assert len(rows)==1 and rows[0]['amount_minor']==1390 and rows[0]['name']=='咖啡'
                page.get_by_role('button',name='账单',exact=True).click()
                page.get_by_label('账单年份',exact=True).select_option('2026')
                expect(page.get_by_role('button',name='编辑账单：咖啡',exact=True)).to_contain_text('13.90')
                assert not errors,errors
            print('PASS: official native DSH discovers all 9 packaged skills, loads only the requested guide through skill, executes its example and saves the correct page ledger; synthetic model only')
    finally:server.shutdown()


if __name__=='__main__':main()

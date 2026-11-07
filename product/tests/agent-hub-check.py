"""AgentHub workbench on the unchanged official Desktop; synthetic models and temporary data."""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import expect, sync_playwright
import official_desktop_check as desktop
from web_check import open_secretary


class Provider(BaseHTTPRequestHandler):
    calls = []
    lock = threading.Lock()
    active = 0
    maximum = 0
    release = threading.Event()

    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.headers.get('Authorization') == 'Bearer synthetic-test-key'
        prompt = '\n'.join(str(message.get('content', '')) for message in payload['messages'])
        delegated = 'HUB_NATIVE_SENTINEL' in prompt and '当前步骤：' in prompt
        summary = '当前步骤：汇总' in prompt
        with Provider.lock:
            Provider.calls.append(payload)
            if delegated:
                Provider.active += 1
                Provider.maximum = max(Provider.maximum, Provider.active)
        try:
            if delegated:
                Provider.release.wait(50)
            output = 'Synthetic title'
            if delegated:
                output = ('## 协作结论\n\n综合研究与评估，建议先整理需求，再分阶段实施。\n\n- **优先处理**：任务状态与结果留存\n- **后续完善**：异常恢复与独立重试\n\n[参考资料](https://example.com/research)\n\n<img src=x onerror="window.__hubUnsafe=true">' if summary else
                          '## 评估结论\n\n建议分阶段实施，保留失败重试和历史结果。' if '当前步骤：评估' in prompt else
                          '## 研究结论\n\n任务需要清晰的分工、真实进度与统一结果入口。')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for choice in [{'delta': {'role': 'assistant'}, 'finish_reason': None}, {'delta': {'content': output}, 'finish_reason': None}, {'delta': {}, 'finish_reason': 'stop'}]:
                item = {'id': 'hub-synthetic', 'object': 'chat.completion.chunk', 'created': 1, 'model': 'stella-mock', 'choices': [{'index': 0, **choice}]}
                self.wfile.write(('data: ' + json.dumps(item) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if delegated:
                with Provider.lock:
                    Provider.active -= 1


def wait(page, predicate, message):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        page.wait_for_timeout(100)
    raise AssertionError(message)


def seed(profile):
    sys.path.insert(0, str(desktop.ROOT / 'packages/stella-agent-hub'))
    from business.agent_hub.domain.service import AgentHubService
    from business.agent_hub.domain.store import AgentHubStore
    from business.migration import import_stella, import_jobs
    target = AgentHubStore(profile / 'agent-hub/agent-hub.sqlite3')
    import_stella(target, None)
    import_jobs(target, [{'id':'legacy-native-job','title':'旧委托记录','prompt':'Retain original output','parentSessionId':'old-parent','createdAt':'2026-11-01T00:00:00Z','status':'completed','output':'Legacy output','diagnostic':''}])
    service = AgentHubService(AgentHubStore(profile / 'stella/business/agent-hub.sqlite3'))
    task = service.create_task({'title': '待恢复的方案评估', 'goal': 'HUB_NATIVE_SENTINEL 保留研究结论，完成评估并汇总。', 'participants': [{'nativeAgentId': 'stella', 'displayName': name} for name in ['研究助手', '评估助手', '汇总助手']]}, str(uuid4()))
    steps = [{'stepId': key, 'title': title, 'participantId': task['participants'][index]['participantId'], 'dependsOn': deps} for index, (key, title, deps) in enumerate([('research', '梳理需求', []), ('review', '评估方案风险', []), ('summary', '汇总全部结果', ['research', 'review'])])]
    service.set_workflow(task['taskId'], steps, task['revision'], str(uuid4()))
    claim = service.claim_step(task['taskId'], 'research')
    service.sync_attempt(task['taskId'], 'research', claim['executionId'], {'status': 'succeeded', 'reply': '已保存的研究结论，不应重新执行。'})
    claim = service.claim_step(task['taskId'], 'review')
    service.sync_attempt(task['taskId'], 'review', claim['executionId'], {'status': 'failed', 'summary': '连接中断（合成测试），请重试本步骤。'})
    return task['taskId']


def check_old_owner(playwright):
    with tempfile.TemporaryDirectory(prefix='hub-old-owner-') as temporary:
        home = Path(temporary)
        profile = Path(desktop.environment(home)['DSH_HOME']) / 'profiles/desktop'
        profile.mkdir(parents=True); (home / 'documents').mkdir()
        (profile / 'package.json').write_text(json.dumps({'name':'hub-compatibility-check','private':True,'dsh':{'profile':{'bundles':['@deepseek-ai/dsh-base','@deepseek-ai/dsh-web-app']}}}))
        (profile / 'cordis.patch.yml').write_text(f'- id: workspace-controller\n  config:\n    documentsDirectory: {home / "documents"}\n- id: session-telemetry-otel\n  disabled: true\n')
        old_pack = Path(os.environ.get('STELLA_OLD_SECRETARY_PACK', str(desktop.ROOT / '.local/artifacts/stella-secretary-0.1.55.tgz')))
        desktop.native_cli(home,'hub-old-owner-install','add',str(old_pack),'--ignore-scripts')
        desktop.native_cli(home,'hub-old-owner-add','add',str(desktop.PACK),'--ignore-scripts')
        with desktop.desktop(playwright,home,'hub-old-owner') as (page,errors):
            open_secretary(page)
            result = page.evaluate('''async()=>{ const response=await fetch('/api/agentHub/read',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({type:'client-request',rpcId:'old-owner-check',method:'agentHub/read',payload:{args:{request:{}}}})});return {status:response.status,body:await response.text()} }''')
            assert result['status'] != 200 or json.loads(result['body'])['result']['ok'] is False, result
            assert not (profile / 'agent-hub/agent-hub.sqlite3').exists(), 'Refuse migration while old Stella still owns collaboration'
            entries = desktop.rpc(page, 'pluginManager/listPlugins')
            entry = next(item for item in entries if item['moduleName'] == '@stella/agent-hub')
            assert entry['fiberPhase'] == 'failed' and '支持独立 AgentHub' in entry.get('diagnostic', ''), entry
            (desktop.ARTIFACTS / 'hub-old-owner-diagnostic.json').write_text(json.dumps({'rpc':result,'plugin':entry,'targetDatabaseCreated':False},ensure_ascii=False,indent=2))


def main():
    desktop.PACK = Path(os.environ.get('STELLA_AGENT_HUB_PACK',str(desktop.ROOT/'.local/artifacts/stella-agent-hub-0.1.0.tgz')))
    desktop.verify_release(); desktop.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='stella-hub-native-') as temporary, sync_playwright() as playwright:
            home = Path(temporary); env = desktop.environment(home)
            profile = Path(env['DSH_HOME']) / 'profiles/desktop'; profile.mkdir(parents=True)
            (home / 'documents').mkdir()
            (profile / 'package.json').write_text(json.dumps({'name': 'hub-native-check', 'private': True, 'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']}}}))
            (profile / 'cordis.patch.yml').write_text(f'''- id: workspace-controller
  config:
    documentsDirectory: {home / 'documents'}
- id: agent-default-model
  config:
    provider: synthetic
    model: stella-mock
- id: llm-pi-ai
  config:
    providers:
      synthetic:
        api: openai-completions
        apiKeyEnv: STELLA_SYNTHETIC_KEY
        baseURL: http://127.0.0.1:{server.server_port}/v1
        models:
          - id: stella-mock
            name: Synthetic
            contextWindow: 32000
            maxTokens: 4096
            input: [text]
- id: session-telemetry-otel
  disabled: true
''')
            credential = Path(env['DSH_HOME']) / '.credentials.yaml'
            credential.write_text('version: 1\nrefs:\n  STELLA_SYNTHETIC_KEY: synthetic-test-key\n'); credential.chmod(0o600)
            failed_id = seed(profile)
            desktop.native_cli(home, 'hub-install', 'add', str(desktop.PACK), '--ignore-scripts')
            completed_job = None
            with desktop.desktop(playwright, home, 'hub-workspace') as (page, errors):
                page.set_viewport_size({'width':1440,'height':1000})
                read = lambda: desktop.rpc(page, 'agentHub/read', {'request': {}})['jobs']
                legacy = desktop.rpc(page, 'agentHub/cancelJob', {'request': {'id':'legacy-native-job'}})
                assert legacy['status']=='completed' and legacy['output']=='Legacy output'
                hub = lambda: page.get_by_role('button', name='AgentHub', exact=True).click()
                hub(); page.get_by_role('heading', name='AgentHub', exact=True).wait_for()
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '01-hub-empty.png'))
                page.get_by_role('button', name='新建协作任务', exact=True).click()
                modal = page.get_by_role('dialog', name='新建协作任务', exact=True)
                modal.get_by_label('任务标题', exact=True).fill('AgentHub 工作台优化')
                modal.get_by_label('目标', exact=True).fill('HUB_NATIVE_SENTINEL 梳理现有设计，提出可实施的工作台优化方案。')
                before = len(Provider.calls)
                modal.get_by_role('button', name='创建任务', exact=True).click()
                page.get_by_label('助手 1 分工', exact=True).fill('梳理现有功能与用户需求')
                page.get_by_label('助手 2 分工', exact=True).fill('评估方案的风险与实施顺序')
                page.get_by_role('button', name='添加助手', exact=True).click()
                expect(page.get_by_role('button', name='添加助手', exact=True)).to_be_disabled()
                page.get_by_label('助手 3 分工', exact=True).fill('独立核对遗漏')
                page.locator('.stella-hub-agent-editor').last.get_by_role('button', name='移除', exact=True).click()
                page.reload(); hub()
                expect(page.get_by_label('助手 2 分工', exact=True)).to_have_value('评估方案的风险与实施顺序')
                assert len(Provider.calls) == before, 'Draft creation/edit/reload must never call the model'
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '02-hub-draft.png'))
                page.get_by_role('button', name='协作设置', exact=True).click()
                page.get_by_role('dialog', name='协作设置', exact=True).get_by_role('button', name='开始开发对话', exact=True).click()
                page.locator('[data-composer-input]').wait_for(); hub()
                page.get_by_role('button', name='发送并执行', exact=True).click()
                wait(page, lambda: Provider.maximum >= 2, 'Native child agents did not execute concurrently')
                wait(page, lambda: any(job['title'] == 'AgentHub 工作台优化' and job['status'] == 'running' for job in read()), 'Job did not become running')
                page.get_by_role('button', name='刷新任务', exact=True).click()
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '03-hub-running.png'))
                page.get_by_role('tab', name='交流', exact=True).click()
                stream = page.get_by_role('list', name='协作交流记录', exact=True)
                expect(stream.locator('[data-kind="started"]')).to_have_count(2)
                expect(stream).to_contain_text('HUB_NATIVE_SENTINEL')
                expect(page.locator('.stella-hub-chat-working')).to_contain_text('助手正在执行')
                active = next(job for job in read() if job['title'] == 'AgentHub 工作台优化')
                recipient = active['collaboration']['participants'][0]['id']
                desktop.rpc(page,'agentHub/hubSend',{'request':{'id':active['id'],'body':'请保留依据与尚未解决的问题。','recipientIds':[recipient],'intent':'message','requestId':str(uuid4())}})
                page.get_by_role('button', name='刷新交流', exact=True).click()
                expect(stream).to_contain_text('请保留依据与尚未解决的问题。')
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '09-hub-chat-running.png'))
                Provider.release.set()
                completed_job = wait(page, lambda: next((job for job in read() if job['title'] == 'AgentHub 工作台优化' and job['status'] == 'completed'), None), 'Native workflow did not complete')
                page.get_by_role('button', name='刷新任务', exact=True).click()
                expect(stream.locator('[data-kind="reply"]')).to_have_count(3, timeout=15000)
                expect(stream).to_contain_text('研究结论')
                expect(stream).to_contain_text('评估结论')
                expect(stream).to_contain_text('协作结论')
                expect(stream.locator('img')).to_have_count(0)
                assert not page.evaluate('Boolean(window.__hubUnsafe)')
                expect(page.locator('.stella-hub-chat-working')).to_have_count(0)
                activity = desktop.rpc(page, 'agentHub/hubActivity', {'request': {'id': active['id']}})
                assert [row['id'] for row in activity] == [row['id'] for row in sorted(activity, key=lambda row: row['createdAt'])]
                page.get_by_label('查看参与者', exact=True).select_option(recipient)
                expect(stream.locator('[data-kind="reply"]')).to_have_count(1)
                expect(stream).to_contain_text('请保留依据与尚未解决的问题。')
                expect(page.get_by_role('button', name='查看此助手原生会话', exact=True)).to_be_visible()
                page.get_by_label('查看参与者', exact=True).select_option('all')
                stream.evaluate('e=>e.scrollTop=0')
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '10-hub-chat-completed.png'))
                page.emulate_media(color_scheme='dark')
                expect(page.locator('body')).to_have_attribute('data-ds-dark-theme', '')
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '11-hub-chat-dark.png'))
                page.set_viewport_size({'width':390,'height':844})
                if page.get_by_role('dialog', name='Stella 秘书导航', exact=True).count(): page.keyboard.press('Escape')
                assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
                page.get_by_role('button', name='跳至最新', exact=True).click()
                expect(stream.locator('li').last).to_be_in_viewport()
                stream.evaluate('e=>e.scrollTop=0')
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '12-hub-chat-narrow.png'))
                page.set_viewport_size({'width':1440,'height':1000});page.emulate_media(color_scheme='light')
                page.get_by_role('tab', name='概览', exact=True).click()
                expect(page.locator('.stella-hub-result h2')).to_have_text('协作结论')
                expect(page.locator('.stella-hub-result strong')).to_have_count(2)
                expect(page.locator('.stella-hub-result a')).to_have_attribute('href', 'https://example.com/research')
                expect(page.locator('.stella-hub-result img')).to_have_count(0)
                assert not page.evaluate('Boolean(window.__hubUnsafe)')
                assert page.evaluate('sessionStorage.getItem("stella.agent-hub.draft.v1")') is None
                assert len({step['attempts'][0]['childId'] for step in completed_job['collaboration']['steps']}) == 3
                summaries = [call for call in Provider.calls if '当前步骤：汇总' in json.dumps(call, ensure_ascii=False)]
                assert summaries and all(text in json.dumps(summaries[-1], ensure_ascii=False) for text in ['研究结论', '评估结论'])
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '04-hub-completed.png'))
                page.emulate_media(color_scheme='dark'); expect(page.locator('body')).to_have_attribute('data-ds-dark-theme', '')
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '05-hub-dark.png'))
                page.get_by_role('tab', name='过程', exact=True).click()
                expect(page.get_by_role('button', name='查看原生会话', exact=True)).to_have_count(3)
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '06-hub-process.png'))
                page.set_viewport_size({'width': 390, 'height': 844})
                if page.get_by_role('dialog', name='Stella 秘书导航', exact=True).count():
                    page.keyboard.press('Escape')
                assert page.locator('.stella-hub-mobile').bounding_box()['height'] < 90
                for tab in ['任务', '工作台']:
                    page.get_by_role('tab', name=tab, exact=True).click()
                    assert page.locator('body').evaluate('e=>e.scrollWidth<=e.clientWidth')
                expect(page.get_by_role('button', name='查看原生会话', exact=True).first).to_be_visible()
                (desktop.ARTIFACTS / 'narrow-layout.json').write_text(json.dumps(page.locator('.stella-hub-mobile').evaluate('e=>({padding:getComputedStyle(e).padding,clearance:getComputedStyle(e).getPropertyValue("--dsh-frame-leading-clearance"),box:e.getBoundingClientRect().toJSON()})')))
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '07-hub-narrow.png'))
                page.set_viewport_size({'width': 1440, 'height': 1000}); page.emulate_media(color_scheme='light')
                page.get_by_role('tab', name='交流', exact=True).click()
                page.get_by_label('交流操作', exact=True).select_option('persona')
                composer = page.get_by_role('combobox', name='交流消息', exact=True)
                composer.fill('@')
                expect(page.get_by_role('listbox', name='选择 Agent', exact=True)).to_be_visible()
                composer.press('Enter')
                composer.press_sequentially('始终列出尚未解决的问题。')
                before_persona = len(Provider.calls)
                page.get_by_role('button', name='更新人设', exact=True).click()
                expect(page.locator('.stella-hub-chat-composer')).to_contain_text('人设已更新，下一次执行生效。')
                assert len(Provider.calls)==before_persona, 'Persona updates must not start a model call'
                page.get_by_label('交流操作', exact=True).select_option('task')
                composer.fill('@')
                composer.press('Enter')
                composer.press_sequentially('补充核对遗漏。')
                page.get_by_role('button', name='分配并执行', exact=True).click()
                completed_job = wait(page, lambda: next((job for job in read() if job['id']==completed_job['id'] and len(job['collaboration']['steps'])==4 and job['status']=='completed'), None), 'Mentioned task did not complete')
                assert [len(step['attempts']) for step in completed_job['collaboration']['steps']]==[1,1,1,1], 'New assignments must not replay completed work'
                assert any('始终列出尚未解决的问题' in json.dumps(call,ensure_ascii=False) for call in Provider.calls[before_persona:]), 'Next execution must receive the updated persona'
                page.get_by_role('button', name='刷新交流', exact=True).click()
                expect(stream.locator('[data-kind="reply"]')).to_have_count(4)
                activity = desktop.rpc(page, 'agentHub/hubActivity', {'request': {'id':completed_job['id']}})
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '15-hub-mentions.png'))
                page.get_by_role('tab', name='过程', exact=True).click()
                page.get_by_label('搜索协作任务', exact=True).fill('不存在的任务')
                expect(page.locator('.stella-hub-task-list button')).to_have_count(0)
                page.get_by_label('搜索协作任务', exact=True).fill('')
                page.get_by_label('筛选协作任务', exact=True).select_option('attention')
                expect(page.locator('.stella-hub-task-list button')).to_have_count(1)
                page.locator('.stella-hub-task-list button').click()
                expect(page.get_by_role('button', name='重试此步骤', exact=True)).to_have_count(1)
                page.screenshot(animations='disabled', path=str(desktop.ARTIFACTS / '08-hub-retry.png'))
                page.get_by_role('button', name='重试此步骤', exact=True).click()
                recovered = wait(page, lambda: next((job for job in read() if job['id'] == failed_id and job['status'] == 'completed'), None), 'Retry did not continue to summary')
                assert [len(step['attempts']) for step in recovered['collaboration']['steps']] == [1, 2, 1]
                assert recovered['collaboration']['steps'][0]['output'] == '已保存的研究结论，不应重新执行。'
                page.get_by_role('button', name='刷新任务', exact=True).click()
                page.get_by_label('筛选协作任务', exact=True).select_option('all')
                page.get_by_role('button', name='再建任务', exact=True).click()
                expect(page.get_by_label('助手 1 分工', exact=True)).to_have_value('梳理需求')
                page.get_by_role('button', name='放弃草稿', exact=True).click()
                page.get_by_role('dialog', name='放弃草稿？', exact=True).get_by_role('button', name='继续编辑', exact=True).click()
                expect(page.get_by_label('助手 1 分工', exact=True)).to_be_visible()
                Provider.release.clear()
                page.get_by_role('button', name='发送并执行', exact=True).click()
                cancel_job = wait(page, lambda: next((job for job in read() if job['title'] == '待恢复的方案评估' and job['id'] != failed_id and job['status'] == 'running'), None), 'Copy did not start')
                page.get_by_role('button', name='取消执行', exact=True).click()
                page.get_by_role('dialog', name='取消执行？', exact=True).get_by_role('button', name='确认取消', exact=True).click()
                Provider.release.set()
                wait(page, lambda: next((job for job in read() if job['id'] == cancel_job['id'] and job['status'] == 'cancelled'), None), 'Cancellation did not finish')
                assert not errors, errors
            with desktop.desktop(playwright, home, 'hub-restart') as (page, errors):
                page.get_by_role('button', name='AgentHub', exact=True).click()
                jobs = desktop.rpc(page, 'agentHub/read', {'request': {}})['jobs']
                restored = next(job for job in jobs if job['id'] == completed_job['id'])
                assert restored == completed_job, 'Restart must retain the exact workflow and result'
                restored_activity = desktop.rpc(page, 'agentHub/hubActivity', {'request': {'id': completed_job['id']}})
                assert restored_activity == activity, 'Formal messages and original times must survive restart'
                page.locator('.stella-hub-task-list button').filter(has_text='AgentHub 工作台优化').click()
                page.get_by_role('tab', name='交流', exact=True).click()
                expect(page.get_by_role('list', name='协作交流记录', exact=True).locator('[data-kind="reply"]')).to_have_count(4)
                assert len(jobs) == 4 and not errors, errors
            secretary_pack = Path(os.environ['STELLA_SECRETARY_REVIEW_PACK'])
            desktop.native_cli(home,'hub-with-stella','add',str(secretary_pack),'--ignore-scripts')
            with desktop.desktop(playwright,home,'hub-with-stella') as (page,errors):
                open_secretary(page)
                expect(page.locator('.stella-sidebar').get_by_role('button',name='AgentHub',exact=True)).to_have_count(0)
                expect(page.locator('.stella-sidebar').get_by_role('button',name='Agent 协作',exact=True)).to_have_count(0)
                page.screenshot(animations='disabled',path=str(desktop.ARTIFACTS/'13-stella-without-hub.png'))
                page.get_by_role('button',name='切换使用模式，当前 Stella',exact=True).click()
                page.get_by_role('menuitem',name='DeepSeek Harness',exact=True).click()
                page.get_by_role('button',name='AgentHub',exact=True).click()
                expect(page.get_by_role('heading',name='AgentHub',exact=True)).to_be_visible()
                page.screenshot(animations='disabled',path=str(desktop.ARTIFACTS/'14-dsh-hub-entry.png'))
                assert not errors,errors
            desktop.native_cli(home,'hub-remove','remove','@stella/agent-hub')
            with desktop.desktop(playwright,home,'hub-disabled') as (page,errors):
                expect(page.get_by_role('button',name='AgentHub',exact=True)).to_have_count(0)
                assert (profile/'agent-hub/agent-hub.sqlite3').is_file()
                open_secretary(page)
                expect(page.get_by_role('heading',name='事务',exact=True)).to_be_visible()
                assert not errors,errors
            desktop.native_cli(home,'hub-reinstall','add',str(desktop.PACK),'--ignore-scripts')
            with desktop.desktop(playwright,home,'hub-reinstalled') as (page,errors):
                jobs=desktop.rpc(page,'agentHub/read',{'request':{}})['jobs']
                assert len(jobs)==4
                assert next(job for job in jobs if job['id']==completed_job['id'])==completed_job
                assert not errors,errors
            check_old_owner(playwright)
        print('PASS: official Desktop; draft safety, bounded native parallel agents, dependency summary, safe Markdown, formal conversation timeline and participant filters, history/search/filter, retry without replay, cancellation, restart, light/dark/narrow; personal profile untouched')
    finally:
        Provider.release.set(); server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()

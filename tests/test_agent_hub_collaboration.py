"""Collaboration uses real workers and synthetic CLI/model outputs, never accounts."""
import json
from pathlib import Path
import sys
import time

import pytest

from common.runtime_identity import RuntimeIdentity, use_identity
from core.plugins.caller import caller_origin_scope
from core.plugins.page_host import PluginPageHost
from core.services import agent_hub_gateway as gateway
from core.services import agent_hub_collaboration as hub
from core.services.agent_hub_events import classify_output


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv('STELLA_AGENT_HUB_PLUGIN', '1')
    monkeypatch.setenv('STELLA_AGENT_HUB_PLUGIN_ROOT', str(Path(__file__).resolve().parents[2] / 'Stella-Plugins/plugins/agent-hub'))
    monkeypatch.setenv('STELLA_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('common.state_dir.agent_hub_db', lambda **_: tmp_path / 'hub.db')
    monkeypatch.setattr('common.state_dir.executions_dir', lambda **_: tmp_path / 'executions')
    monkeypatch.setattr(gateway, 'apply_user_preferences', lambda: {})
    host = PluginPageHost()
    channel = host.bridges.open(plugin_id='stella.agent-hub', user_id='desktop', instance_id='test', generation=1, page_id='agent-hub.main')
    def invoke(method, **params):
        return host.invoke(channel_nonce=channel.channel_nonce,plugin_id=channel.plugin_id,page_id=channel.page_id,
                           generation=channel.generation,method=method,input=params)
    yield invoke
    for stop in list(hub._WORKERS.values()):
        stop.set()
    deadline = time.monotonic() + 3
    while hub._WORKERS and time.monotonic() < deadline:
        time.sleep(.02)
    gateway.shutdown_agent_hub_session()


def provider(page, tmp_path, local='codex-cli'):
    binary = tmp_path / local
    binary.write_text(f'''#!{sys.executable}
import json,sys,time
if '--version' in sys.argv:
    print('synthetic-cli 1')
else:
    print(json.dumps({{'type':'tool_call','text':'process only','argv':sys.argv[1:]}}),flush=True)
    time.sleep(.1)
    print(json.dumps({{'type':'item.completed','item':{{'type':'agent_message','text':'正式结论'}}}}) if {local!r} == 'codex-cli' else json.dumps({{'type':'result','result':'正式结论','is_error':False}}),flush=True)
''')
    binary.chmod(0o755)
    return page('upsert_provider',provider={'providerLocalId':local,'enabled':True,'binaryPath':str(binary)})


def create(page, people):
    return page('create_task',payload={'title':'协作验收','goal':'完成目标','participants':people},request_id='create')


def flow(page, task, steps):
    return page('set_workflow',task_id=task['taskId'],steps=steps,expected_revision=task['revision'],request_id='flow')


def wait_task(page, task_id, expected='succeeded'):
    deadline=time.monotonic()+8
    last=None
    while time.monotonic()<deadline:
        last=page('refresh_task',task_id=task_id)
        if last['status']==expected:
            return last
        if last['status'] in {'failed','outcome_unknown'} and expected=='succeeded':
            break
        time.sleep(.05)
    pytest.fail(f'task did not reach {expected}: {last}')


@pytest.mark.parametrize('local',['codex-cli','claude-code','cursor-cli'])
def test_page_executes_without_stella_and_splits_output(page,tmp_path,local):
    p=provider(page,tmp_path,local)
    task=create(page,[{'providerConfigId':p['providerConfigId'],'persona':'检查边界','modelSelection':{'mode':'explicit','modelId':'test-model'}}])
    pid=task['participants'][0]['participantId']
    task=flow(page,task,[{'stepId':'one','title':'执行','participantId':pid,'dependsOn':[]}])
    page('start_task',task_id=task['taskId'])
    task=wait_task(page,task['taskId'])
    replies=[m for m in task['messages'] if m['kind']=='reply']
    assert [m['body'] for m in replies]==['正式结论']
    attempt=task['steps'][0]['attempts'][0]
    activity=page('task_activity',task_id=task['taskId'],execution_id=attempt['executionId'])
    assert activity['snapshot']['modelSelection']['modelId']=='test-model'
    assert any('process only' in e['safePayload'].get('text','') for e in activity['events'])
    process = '\n'.join(e['safePayload'].get('text','') for e in activity['events'])
    assert '--model' in process and 'test-model' in process and '\\u68c0\\u67e5\\u8fb9\\u754c' in process
    assert page('refresh_task',task_id=task['taskId'])['messages']==task['messages']
    with pytest.raises(PermissionError):
        page('task_activity',task_id=task['taskId'],execution_id='foreign')


def test_serial_parallel_join_and_background_collection(page,tmp_path):
    p=provider(page,tmp_path)
    task=create(page,[{'providerConfigId':p['providerConfigId']}])
    pid=task['participants'][0]['participantId']
    task=flow(page,task,[{'stepId':sid,'title':sid,'participantId':pid,'dependsOn':deps}
                         for sid,deps in [('plan',[]),('left',['plan']),('right',['plan']),('review',['left','right'])]])
    page('start_task',task_id=task['taskId'])
    # No page observation is needed for scheduling or collection.
    deadline=time.monotonic()+8
    with use_identity(RuntimeIdentity(user_id='desktop')),caller_origin_scope('desktop'):
        while time.monotonic()<deadline:
            stored=gateway.invoke_ok('get_task',{'task_id':task['taskId']})
            if stored['status']=='succeeded':
                break
            time.sleep(.05)
    assert stored['status']=='succeeded',stored
    assert len([m for m in stored['messages'] if m['kind']=='reply'])==4
    attempts={s['stepId']:s['attempts'][0] for s in stored['steps']}
    assert attempts['left']['createdAt']>attempts['plan']['createdAt']
    assert '正式结论' in attempts['review']['goal']


def test_only_structured_final_events_are_replies():
    assert classify_output('codex-cli','mixed log and answer')[0]=='process'
    assert classify_output('claude-code',json.dumps({'type':'assistant','message':{'content':'draft'}}))[0]=='process'
    assert classify_output('cursor-cli',json.dumps({'type':'result','result':'bad','is_error':True}))[0]=='process'
    assert classify_output('codex-cli',json.dumps({'type':'item.completed','item':'untrusted'}))[0]=='process'


def test_bridge_cannot_inject_core_execution_facts(page):
    with pytest.raises(PermissionError,match='not allowed'):
        page('sync_attempt',task_id='t',result={'status':'succeeded'})
    with pytest.raises(PermissionError,match='not allowed'):
        page('provider_start',task={})


def test_native_and_mixed_tasks_use_selected_model_and_persona(page,tmp_path,monkeypatch):
    from agent.protocol.agent import Agent
    from bridge.agent_bridge import AgentLLMModel
    from agent.registry import get_agent_registry
    seen=[]
    def fake_run(self, goal, **kwargs):
        seen.append((self.system_prompt,self.model._session_model,self.work_root,[tool.name for tool in self.tools]))
        kwargs['on_event']({'type':'tool_execution_start','data':{'tool':'read'}})
        return '原生正式结果'
    monkeypatch.setattr(Agent,'run_stream',fake_run)
    # No model transport is invoked; constructing the real adapter still verifies overrides.
    assert AgentLLMModel
    native_id=get_agent_registry().default_agent_id
    p=provider(page,tmp_path)
    task=create(page,[{'nativeAgentId':native_id,'persona':'审查角色','modelSelection':{'mode':'explicit','modelId':'native-test'}},
                      {'providerConfigId':p['providerConfigId']}])
    native,external=task['participants']
    task=flow(page,task,[{'stepId':'native','title':'规划','participantId':native['participantId'],'dependsOn':[]},
                         {'stepId':'external','title':'实施','participantId':external['participantId'],'dependsOn':['native']}])
    page('start_task',task_id=task['taskId'])
    task=wait_task(page,task['taskId'])
    assert [m['body'] for m in task['messages'] if m['kind']=='reply']==['原生正式结果','正式结论']
    assert '审查角色' in seen[0][0] and seen[0][1]=='native-test'
    assert {'read','write','edit','bash'}<=set(seen[0][3])
    assert Path(seen[0][2]).is_dir()


def test_cancel_and_retry_preserve_history(page,tmp_path):
    p=provider(page,tmp_path)
    binary=Path(p['binaryPath'])
    binary.write_text(f'#!{sys.executable}\nimport time\ntime.sleep(30)\n')
    task=create(page,[{'providerConfigId':p['providerConfigId']}])
    task=flow(page,task,[{'stepId':'one','title':'执行','participantId':task['participants'][0]['participantId'],'dependsOn':[]}])
    page('start_task',task_id=task['taskId'])
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        task=page('refresh_task',task_id=task['taskId'])
        if task['steps'][0]['attempts'] and task['steps'][0]['attempts'][-1]['status']=='running':
            break
        time.sleep(.02)
    task=page('cancel_task',task_id=task['taskId'])
    task=wait_task(page,task['taskId'],'cancelled')
    assert task['steps'][0]['attempts'][0]['status']=='cancelled'
    provider(page, tmp_path)
    deadline=time.monotonic()+2
    while task['taskId'] in hub._WORKERS and time.monotonic()<deadline:
        time.sleep(.02)
    page('start_task',task_id=task['taskId'],step_id='one',retry=True)
    task=wait_task(page,task['taskId'])
    assert len(task['steps'][0]['attempts'])==2
    assert task['steps'][0]['attempts'][0]['status']=='cancelled'


def test_output_cursor_handles_utf8_half_lines_and_replacement(tmp_path):
    from core.security.delegated_runtime import DelegatedProcesses
    file=tmp_path/'output'
    file.write_bytes('开始\n半'.encode())
    info=file.stat()
    manager=DelegatedProcesses(object())
    manager.runs['ref']={'execution_id':'e','status':'running','outputPath':str(file),'outputIdentity':(info.st_dev,info.st_ino)}
    first=manager.observe('ref','e',0)
    assert first[0]['text']=='开始\n'
    cursor=first[0]['cursor']
    assert manager.observe('ref','e',cursor)==[]
    with file.open('ab') as stream:
        stream.write('行\n'.encode())
    second=manager.observe('ref','e',cursor)
    assert second[0]['text']=='半行\n'
    file.unlink()
    file.symlink_to(tmp_path/'outside')
    with pytest.raises(OSError):
        manager.observe('ref','e',second[0]['cursor'])

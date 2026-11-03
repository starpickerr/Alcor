"""Trusted execution transport; task ownership and scheduling rules stay in the plugin."""
from contextvars import copy_context
import json
import threading
import time

from core.services import agent_hub_gateway as gateway
from core.services.agent_hub_execution import AgentHubExecution

_LOCK = threading.RLock()
_WORKERS = {}
_NATIVE_CANCEL = {}
_EXECUTION = AgentHubExecution()


def _invoke(method, **params):
    return gateway.invoke_ok(method, params)


def _authorize(operation, params):
    from core.security.service import SecurityService
    reason = SecurityService.authorize_operation('plugin:stella.agent-hub', operation, params)
    if reason:
        raise PermissionError(reason)


def native_participants():
    from agent.registry import get_agent_registry
    return [{'nativeAgentId': p.id, 'displayName': p.name, 'agentId': 'stella'}
            for p in get_agent_registry().list() if p.enabled]


def _native_run(svc, claim):
    from agent.registry import get_agent_registry
    from bridge.agent_bridge import AgentLLMModel
    from bridge.bridge import Bridge
    from agent.protocol.agent import Agent
    from core.workspace.service import WorkspaceService
    from core.security.service import SecurityService
    from agent.protocol.cancel import AgentCancelledError
    eid, snap = claim['executionId'], claim['snapshot']
    profile = get_agent_registry().get(snap['nativeAgentId'])
    if profile is None or not profile.enabled:
        raise ValueError('native participant is unavailable')
    selection = snap['modelSelection']
    model = AgentLLMModel(Bridge(), bot_type=profile.bot_type or 'chat')
    model.set_session_override(selection.get('providerId'), selection.get('modelId') if selection['mode'] == 'explicit' else profile.model)
    lease = WorkspaceService.allocate_lease(work_root=svc.work_root, execution_id=eid,
                                            attempt_id=svc.get(eid)['attemptId'], source_project=None)
    svc.attach_workspace(eid, lease['workspaceLeaseRef'], lease['path'])
    svc.transition(eid, 'running')
    cancel = _NATIVE_CANCEL[eid]
    deadline = time.monotonic() + svc.get(eid)['budget']['maxWallTime']
    binding = SecurityService.policy_binding()
    from agent.runtime.tool_invocation import _current_authorization, permission_limit_scope
    from agent.permission.security_policy import intersect, resolve
    from core.plugins.caller import caller_origin_scope
    from common.runtime_identity import current_identity
    identity = current_identity()
    parent_policy = _current_authorization(None, identity.agent_id, identity.session_id, None, False)[1]['policy']
    parent_policy = intersect(parent_policy, resolve('workspace-write'))
    from agent.tools import Read, Write, Edit, Ls, SearchFiles, Bash
    tools = [kind({'cwd': lease['path']}) for kind in (Read, Write, Edit, Ls, SearchFiles, Bash)]
    class TaskAgent(Agent):
        fixed_toolset = True
        def get_full_system_prompt(self, skill_filter=None):
            return self.system_prompt
        def check_context_protection(self):
            super().check_context_protection()
            if cancel.is_set() or time.monotonic() >= deadline:
                raise AgentCancelledError('task cancelled or wall budget exceeded')
            if binding != SecurityService.policy_binding():
                raise PermissionError('participant policy changed')
    # This fresh task agent receives shared messages only; no private chat,
    # global persona files or another participant's memory is loaded.
    agent = TaskAgent(system_prompt='你是任务内协作 Agent。完成当前步骤，正式结果用中文回复。'
                      '\n人设不改变权限或审批：\n' + snap['persona'],
                      model=model, tools=tools, workspace_dir=lease['path'], max_steps=20,
                      enable_skills=False, skip_context_files=True, output_mode='logger')
    agent.work_root = lease['path']
    def event(value):
        if value.get('type') in {'tool_execution_start', 'tool_execution_end', 'error'}:
            svc.append_event(eid, 'process', {'text': json.dumps(value, ensure_ascii=False, default=str)[:8000]})
    try:
        with caller_origin_scope('agent'), permission_limit_scope(parent_policy, (lease['path'],)):
            answer = agent.run_stream(claim['goal'], on_event=event, clear_history=True, cancel_event=cancel,
                                      allow_empty_response=True)
        if cancel.is_set():
            svc.transition(eid, 'cancelled')
        else:
            if answer:
                svc.append_event(eid, 'reply_candidate', {'text': answer[:65536]})
            svc.append_event(eid, 'model', {'modelId': model.model})
            svc.transition(eid, 'succeeded', summary='Stella 步骤已完成')
    except AgentCancelledError:
        svc.transition(eid, 'cancelled' if cancel.is_set() else 'failed', summary='执行已停止')
    finally:
        _NATIVE_CANCEL.pop(eid, None)


def _sync_unlocked(task_id):
    task = _invoke('get_task', task_id=task_id)
    svc = _EXECUTION._execution_service()
    for step in task['steps']:
        if not step['attempts']:
            continue
        attempt = step['attempts'][-1]
        if attempt['status'] not in {'queued', 'running', 'cancel_requested', 'waiting_approval', 'interrupted'}:
            continue
        eid = attempt['executionId']
        try:
            row = svc.get(eid)
        except KeyError:
            result = {'status': 'outcome_unknown', 'summary': '执行启动中断，请确认结果后显式重试。'}
        else:
            if attempt['snapshot'].get('providerConfigId'):
                row = _EXECUTION._observe_execution(svc, eid)
            events = svc._store.events(eid)
            replies = [e['safePayload']['text'] for e in events if e['type'] == 'reply_candidate']
            models = [e['safePayload']['modelId'] for e in events if e['type'] == 'model']
            result = {k: row.get(k) for k in ('status', 'attemptId', 'summary', 'error', 'artifactRefs')}
            result['cursor'] = events[-1]['sequence'] if events else 0
            if replies and row['status'] == 'succeeded':
                result['reply'] = replies[-1]
            if models:
                result['actualModel'] = models[-1]
        task = _invoke('sync_attempt', task_id=task_id, step_id=step['stepId'], execution_id=eid, result=result)
    return task


def _sync(task_id):
    with _LOCK:
        return _sync_unlocked(task_id)


def _launch(task_id, step_id, retry=False):
    claim = _invoke('claim_step', task_id=task_id, step_id=step_id, retry=retry)
    svc = _EXECUTION._execution_service()
    snap = claim['snapshot']
    if snap.get('providerConfigId'):
        result = _EXECUTION._submit({'goal': claim['goal'], 'provider_config_id': snap['providerConfigId'],
                                     'run_config': snap, 'execution_id': claim['executionId']})
        if result['status'] in {'failed', 'outcome_unknown'}:
            _invoke('sync_attempt', task_id=task_id, step_id=step_id, execution_id=claim['executionId'], result=result)
    else:
        created = svc.create(owner_plugin_id='stella.agent-hub', task_kind='general.delegate',
                             goal=claim['goal'], execution_id=claim['executionId'],
                             acceptance={'hubRunConfig': snap},
                             budget={'maxWallTime': 180, 'maxAttempts': 1, 'maxConcurrent': 3})
        _NATIVE_CANCEL[claim['executionId']] = threading.Event()
        def run():
            try:
                _native_run(svc, claim)
            except Exception as exc:
                row = svc.get(created['executionId'])
                if row['status'] in {'queued', 'running', 'cancel_requested'}:
                    svc.transition(created['executionId'], 'cancelled' if row['status'] == 'cancel_requested'
                                   else 'failed', error={'message': str(exc)})
            finally:
                _NATIVE_CANCEL.pop(created['executionId'], None)
        threading.Thread(target=copy_context().run, args=(run,), name='hub-stella-step', daemon=True).start()
    return claim


def start_task(task_id, step_id=None, retry=False):
    _authorize('system_execute', {'task_id': task_id, 'step_id': step_id, 'retry': retry})
    if type(retry) is not bool:
        raise ValueError('retry must be boolean')
    with _LOCK:
        if task_id in _WORKERS:
            raise ValueError('任务正在运行')
        # Validate the task before starting a detached worker.
        task = _invoke('get_task', task_id=task_id)
        if step_id:
            _launch(task_id, step_id, retry)
        elif not task['steps']:
            raise ValueError('请先添加流程步骤')
        stop = threading.Event()
        _WORKERS[task_id] = stop
        def schedule():
            deadline = time.monotonic() + 1800
            try:
                while not stop.is_set() and time.monotonic() < deadline:
                    _authorize('system_execute', {'task_id': task_id})
                    current = _sync(task_id)
                    if current['cancelled']:
                        return
                    statuses = {s['stepId']: s['attempts'][-1]['status'] if s['attempts'] else 'pending'
                                for s in current['steps']}
                    active = sum(v in {'queued', 'running', 'waiting_approval', 'cancel_requested'} for v in statuses.values())
                    launched = False
                    for step in current['steps']:
                        if active >= 3:
                            break
                        if statuses[step['stepId']] != 'pending' or any(statuses[d] != 'succeeded' for d in step['dependsOn']):
                            continue
                        with _LOCK:
                            if stop.is_set():
                                return
                            _launch(task_id, step['stepId'])
                        active += 1
                        launched = True
                    if not active and not launched:
                        return
                    stop.wait(.4)
                if not stop.is_set():
                    cancel_task(task_id)
            except Exception as exc:
                from common.log import logger
                logger.warning('Agent Hub scheduler stopped: %s', type(exc).__name__)
                current = _invoke('get_task', task_id=task_id)
                for step in current['steps']:
                    if step['attempts'] and step['attempts'][-1]['status'] in {'queued', 'running', 'waiting_approval'}:
                        eid = step['attempts'][-1]['executionId']
                        if eid in _NATIVE_CANCEL:
                            _NATIVE_CANCEL[eid].set()
                        elif step['attempts'][-1]['snapshot'].get('providerConfigId'):
                            _EXECUTION._cancel(eid)
                        _invoke('sync_attempt', task_id=task_id, step_id=step['stepId'],
                                execution_id=step['attempts'][-1]['executionId'],
                                result={'status': 'outcome_unknown', 'summary': '调度中断，请查看执行过程并确认后重试。',
                                        'error': {'message': str(exc)}})
            finally:
                with _LOCK:
                    _WORKERS.pop(task_id, None)
        threading.Thread(target=copy_context().run, args=(schedule,), name='hub-task-scheduler', daemon=True).start()
    return _invoke('get_task', task_id=task_id)


def cancel_task(task_id):
    _authorize('write', {'task_id': task_id})
    with _LOCK:
        task = _invoke('mark_cancelled', task_id=task_id)
        if task_id in _WORKERS:
            _WORKERS[task_id].set()
    for step in task['steps']:
        if not step['attempts']:
            continue
        attempt = step['attempts'][-1]
        if attempt['status'] not in {'queued', 'running', 'waiting_approval', 'cancel_requested'}:
            continue
        eid = attempt['executionId']
        if eid in _NATIVE_CANCEL:
            _NATIVE_CANCEL[eid].set()
            _EXECUTION._execution_service().request_cancel(eid)
        elif attempt['snapshot'].get('providerConfigId'):
            _EXECUTION._cancel(eid)
    return _sync(task_id)


def task_activity(task_id, execution_id, after_sequence=0):
    _authorize('read', {'task_id': task_id, 'execution_id': execution_id})
    if type(after_sequence) is not int or after_sequence < 0:
        raise ValueError('invalid event cursor')
    task = _invoke('get_task', task_id=task_id)
    attempt = next((a for s in task['steps'] for a in s['attempts'] if a['executionId'] == execution_id), None)
    if attempt is None:
        raise PermissionError('execution does not belong to task')
    svc = _EXECUTION._execution_service()
    events = svc._store.events(execution_id, after_sequence)[:100]
    # File paths stay in Core. The page sees bounded event data and artifact refs.
    return {'events': events, 'nextSequence': events[-1]['sequence'] if events else after_sequence,
            'snapshot': attempt['snapshot'], 'status': svc.get(execution_id)['status']}


def refresh_task(task_id):
    _authorize('read', {'task_id': task_id})
    return _sync(task_id)


def adopt_proposal(task_id, execution_id, expected_revision, request_id):
    activity = task_activity(task_id, execution_id)
    events = _EXECUTION._execution_service()._store.events(execution_id)
    candidates = [e['safePayload']['text'] for e in events if e['type'] == 'reply_candidate']
    if not candidates or activity['status'] != 'succeeded':
        raise ValueError('没有可采用的正式流程提案')
    body = candidates[-1].strip()
    if body.startswith('```'):
        body = '\n'.join(body.splitlines()[1:-1])
    proposal = json.loads(body)
    return _invoke('set_workflow', task_id=task_id, steps=proposal['steps'],
                   expected_revision=expected_revision, request_id=request_id)

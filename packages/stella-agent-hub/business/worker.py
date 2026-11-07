"""Private AgentHub process; no Stella secretary services or personal sources."""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from business.agent_hub.domain.service import AgentHubService
from business.agent_hub.domain.store import AgentHubStore
from business.agent_hub.entry import handle_invoke
from business.migration import import_stella, import_jobs
import business.agent_hub.entry as entry


def main():
    root = Path(sys.argv[1])
    service = None
    for line in sys.stdin:
        request = json.loads(line)
        try:
            action, payload = request['action'], request.get('input', {})
            if action == 'initialize':
                if service is None:
                    store = AgentHubStore(root / 'agent-hub.sqlite3')
                    import_stella(store, sys.argv[2] if len(sys.argv) > 2 else '')
                    service = AgentHubService(store)
                    entry._service = lambda _: service
                    with store._connect() as db:
                        tasks = [json.loads(row[0]) for row in db.execute('SELECT document_json FROM hub_tasks')]
                    for task in tasks:
                        for step in task['steps']:
                            for attempt in step['attempts']:
                                if attempt['status'] in ('queued', 'running', 'cancel_requested', 'waiting_approval', 'interrupted'):
                                    service.sync_attempt(task['taskId'], step['stepId'], attempt['executionId'], {'status': 'outcome_unknown', 'summary': '上次执行结果未知；请核对原生会话后显式重试。'})
                value = True
            elif service is None:
                raise ValueError('AgentHub 尚未初始化。')
            elif action == 'invoke':
                if payload.get('domain') != 'agent_hub':
                    raise ValueError('AgentHub 不提供 Stella 业务访问。')
                args = payload.get('arguments') or {}
                result = handle_invoke({'method': payload['method'], 'input': args, 'idempotencyKey': payload.get('request_id'), '_hostDataHandle': {
                    'hubCoordinator': True, 'executionTask': args.get('task'), 'executionSandbox': {'mode': 'core', **(args.get('task') or {}), 'outputDirectory': str(root / 'executions')},
                }})
                if result['status'] != 'succeeded':
                    raise ValueError(result.get('error', {}).get('message') or '协作操作未完成。')
                value = result.get('value')
            elif action == 'import_jobs':
                import_jobs(service.store, payload['jobs']); value = True
            elif action == 'legacy_jobs':
                with service.store._connect() as db:
                    rows = db.execute('SELECT document_json FROM legacy_jobs WHERE id=?', (payload['id'],)) if payload.get('id') else db.execute('SELECT document_json FROM legacy_jobs')
                    value = [json.loads(row[0]) for row in rows]
            else:
                raise ValueError('Unknown AgentHub action')
            response = {'id': request['id'], 'ok': True, 'value': value}
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            response = {'id': request['id'], 'ok': False, 'error': str(error)}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()

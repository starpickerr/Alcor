from __future__ import annotations

import inspect
import json
import os
from contextlib import closing
import sqlite3
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from apple_sync.calendar import AppleCalendarSync
from apple_sync.reminders import AppleReminderSync
from apple_sync.bridge import AppleCalendarBridge


class Peer:
    def __init__(self, domain, call):
        self.domain = domain
        self.call = call

    def __getattr__(self, method):
        return lambda *args, **kwargs: self.call({'domain': self.domain, 'method': method, 'args': args, 'kwargs': kwargs})


class AppleWorker:
    def __init__(self, root, peer, bridge=None):
        verification = os.environ.get('DSH_ALCOR_VERIFY_NO_APPLE')
        if verification not in (None, '1'):
            raise RuntimeError('Invalid Apple verification isolation setting')
        # Process-scoped acceptance isolation; never read a model or Remote argument.
        self.verification_no_apple = verification == '1'
        self.root = Path(root)
        if not self.verification_no_apple:
            self.root.mkdir(parents=True, exist_ok=True)
        self.peer = peer
        self.bridge = None if self.verification_no_apple else bridge or AppleCalendarBridge()
        self.managers = {}

    def initialize(self, legacy_directory=None):
        if self.verification_no_apple:
            return True
        if self.managers:
            return True
        for kind, filename, cls, port, domain in (
            ('calendar', 'apple-calendar.sqlite3', AppleCalendarSync, 'calendar_port', 'calendar'),
            ('reminders', 'apple-reminders.sqlite3', AppleReminderSync, 'todo_port', 'tasks'),
        ):
            target = self.root / filename
            if legacy_directory and not target.exists():
                source = Path(legacy_directory) / filename
                if source.is_file():
                    try:
                        with closing(sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True)) as original, closing(sqlite3.connect(target)) as destination, destination:
                            original.backup(destination)
                    except Exception:
                        target.unlink(missing_ok=True)
                        raise
            manager = cls(bridge=self.bridge, db_path=target, **{port: Peer(domain, self.peer)})
            manager.continue_approved_delete = lambda operation_id, kind=kind: self._continue_approved_delete(kind, operation_id)
            manager.finish_approved_delete = lambda plan, kind=kind: self._continue_approved_delete(kind,plan['operation_id'],finish_plan=plan)
            self.managers[kind] = manager
        for manager in self.managers.values():
            manager.prime_bindings()
            manager.resume_auto()
        return True

    def invoke(self, method, arguments=None):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        arguments = dict(arguments or {})
        if (method in ('one_time', 'transfer_batch') and arguments.get('direction') == 'export'
                or method in ('resolve_conflict', 'resolve_category_conflict') and arguments.get('choice') in ('keep_alcor', 'keep_apple')
                or method in ('sync', 'start_sync') and any(arguments.get(key, False) is not False for key in ('confirm_deletions', 'confirm_local_deletions'))):
            raise PermissionError('此操作缺少真实会话的具体单次审批；页面或 confirm 参数不能授予权限。')
        manager = self.managers[arguments.pop('kind', 'calendar')]
        methods = ('status', 'request_access', 'start_sync', 'configure', 'replace_links', 'disconnect', 'set_auto', 'items', 'lookup_item', 'preview', 'preview_items', 'one_time', 'sync', 'resolve_conflict','resolve_category_conflict','cancel_operation','sync_range', 'preview_batch', 'transfer_batch', 'prepare_delete_snapshot')
        if method == 'catalog':
            return [{'name': name, 'parameters': str(inspect.signature(getattr(manager, name)))} for name in methods if hasattr(manager, name)]
        if method not in methods or not hasattr(manager, method):
            raise ValueError('Unknown Apple operation')
        return getattr(manager, method)(**arguments)

    def preview_category_rebuild(self,kind,apple_id,source_id):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        manager=self.managers[kind]
        owner=manager.todos if kind=='reminders' else manager.calendar
        from apple_sync.category_rebuild import preview
        return preview(manager,owner,kind,apple_id,source_id)

    def rebuild_category(self,kind,apple_id,source_id,snapshot):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        manager=self.managers[kind]
        owner=manager.todos if kind=='reminders' else manager.calendar
        from apple_sync.category_rebuild import rebuild
        with manager._lock:
            return rebuild(manager,owner,kind,apple_id,source_id,snapshot)
    def prepare_approved_export(self, method, arguments, operation_id):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        from apple_sync.approved_export import prepare
        kind = arguments.get('kind', 'calendar')
        return prepare(self.managers[kind], kind, method, arguments, operation_id)

    def run_approved_export(self, plan):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        from apple_sync.approved_export import run
        def authorize(operation_id, fingerprint, local_id):
            return self.peer({'route': 'approved_export', 'operation_id': operation_id,
                              'plan_fingerprint': fingerprint, 'local_id': local_id}) is True
        receipt_only = isinstance(plan, dict) and plan.get('mode') == 'receipt_finish'
        if receipt_only:
            if set(plan) != {'mode', 'plan'}:
                raise ValueError('invalid conflict receipt finish envelope')
            plan = plan['plan']
        kind = plan['kind']
        if plan.get('mode') == 'conflict':
            from apple_sync.bound_conflict import run as run_conflict
            context = {'kind': kind, 'operation_id': plan['operation_id'], 'plan_fingerprint': plan['plan_fingerprint'],
                       'local_id': plan['items'][0]['local_id']}
            def owner_call(request):
                if receipt_only and request['method'] != 'apple_verify_bound_conflict':
                    raise PermissionError('receipt verification cannot mutate owner state')
                return self.peer({'route': 'bound_conflict_owner', **context, 'method': request['method'],
                                  'args': list(request['args']), 'kwargs': request['kwargs']})
            owner = Peer('tasks' if kind == 'reminders' else 'calendar', owner_call)
            return run_conflict(self.managers[kind], owner, kind, plan, authorize, receipt_only=receipt_only)
        if receipt_only:
            raise ValueError('receipt finish requires a bound conflict plan')
        return run(self.managers[kind], kind, plan, authorize)

    def prepare_item_restore(self, kind, identity, operation_id):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        from apple_sync.item_restore import prepare
        manager=self.managers[kind]
        def owner_call(request):
            return self.peer({'route':'item_restore_prepare','kind':kind,'operation_id':operation_id,
                              'local_id':identity,'method':request['method'],'args':list(request['args']),'kwargs':request['kwargs']})
        owner=Peer('tasks' if kind=='reminders' else 'calendar',owner_call)
        with manager._lock:
            return prepare(manager,owner,kind,identity,operation_id)

    def run_item_restore(self, kind, plan):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        from apple_sync.item_restore import run,finish_prepared
        manager=self.managers[kind]
        receipt_finish=isinstance(plan,dict) and plan.get('mode')=='receipt_finish'
        if receipt_finish:
            if set(plan)!={'mode','plan'}:
                raise ValueError('invalid restore receipt finish envelope')
            plan=plan['plan']
        context={'kind':kind,'operation_id':plan['operation_id'],'plan_fingerprint':plan['plan_fingerprint'],'local_id':plan['local_id']}
        def owner_call(request):
            return self.peer({'route':'item_restore_owner',**context,'method':request['method'],'args':list(request['args']),'kwargs':request['kwargs']})
        owner=Peer('tasks' if kind=='reminders' else 'calendar',owner_call)
        with manager._lock:
            state=(finish_prepared(manager,owner,kind,plan) if receipt_finish else
                   run(manager,owner,kind,plan,lambda:self.peer({'route':'item_restore',**context}) is True))
            return {'status':'success','local_id':plan['local_id'],'state':state}

    def prepare_approved_delete(self, domain, operation_id, local_plan=None, category_id=None):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        from apple_sync.approved_delete import prepare
        kind = 'reminders' if domain == 'tasks' else 'calendar'
        def prepare_owner(action, **arguments):
            return self.peer({'route':'approved_delete_prepare','action':action,'args':arguments})
        return prepare(self.managers[kind], domain, operation_id, local_plan, category_id=category_id, prepare_owner=prepare_owner)

    def run_approved_delete(self, plan):
        if self.verification_no_apple:
            raise RuntimeError('Apple sync is disabled for this verification process')
        from apple_sync.approved_delete import run
        def authorize(operation_id, fingerprint, local_id):
            return self.peer({'route': 'approved_delete', 'operation_id': operation_id,
                              'plan_fingerprint': fingerprint, 'local_id': local_id}) is True
        def owner_action(action, **arguments):
            return self.peer({'route': 'approved_delete_owner', 'action': action,
                              'operation_id': plan['operation_id'], 'plan_fingerprint': plan['plan_fingerprint'],
                              'local_id': plan['local_plan']['id'], 'args': arguments})
        return run(self.managers[plan['kind']], plan, authorize, owner_action)

    def _continue_approved_delete(self, kind, operation_id, *, finish_plan=None):
        domain='tasks' if kind=='reminders' else 'calendar'
        request={'route':'approved_delete_continue','phase':'finish_begin' if finish_plan else 'begin','domain':domain,'operation_id':operation_id}
        if finish_plan is not None:
            request['plan']=finish_plan
        plan=self.peer(request)
        if plan is None:
            return None
        try:
            if plan.get('kind')!=kind or plan.get('domain')!=domain or plan.get('operation_id')!=operation_id:
                raise ValueError('confirmed deletion continuation target changed')
            return self.run_approved_delete(plan)
        finally:
            self.peer({'route':'approved_delete_continue','phase':'end','operation_id':operation_id,
                       'plan_fingerprint':plan['plan_fingerprint'],'local_id':plan['local_plan']['id']})

    def close(self):
        for manager in self.managers.values():
            manager.stop_auto()
        if hasattr(self.bridge, 'close'):
            self.bridge.close()


def main():
    pending = {}
    lock = threading.Lock()
    output = threading.Lock()
    stopping = threading.Event()

    def emit(value):
        with output:
            print(json.dumps(value, ensure_ascii=False), flush=True)

    def peer(input):
        if stopping.is_set():
            raise RuntimeError('Apple sync is stopping')
        identity = str(uuid4())
        ready = threading.Event()
        receipt = {}
        with lock:
            pending[identity] = (ready, receipt)
        try:
            emit({'type': 'peer', 'id': identity, 'input': input})
            if not ready.wait(30):
                raise RuntimeError('Business operation timed out; verify saved data before retrying')
            if not receipt.get('ok'):
                error = {'KeyError': KeyError, 'ValueError': ValueError}.get(receipt.get('errorType'), RuntimeError)
                raise error(receipt.get('error') or 'Business connection stopped')
            return receipt.get('value')
        finally:
            with lock:
                pending.pop(identity, None)

    worker = AppleWorker(sys.argv[1], peer)

    def execute(request):
        try:
            action = request['action']
            if action not in ('initialize', 'invoke', 'preview_category_rebuild', 'rebuild_category', 'prepare_approved_export', 'run_approved_export', 'prepare_approved_delete', 'run_approved_delete', 'prepare_item_restore', 'run_item_restore'):
                raise ValueError('Unknown Apple worker action')
            value = getattr(worker, action)(**request.get('input', {}))
            emit({'id': request['id'], 'ok': True, 'value': value})
        except Exception as error:
            traceback.print_exc(file=sys.stderr)
            emit({'id': request['id'], 'ok': False, 'error': str(error), 'errorType': type(error).__name__})

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        for line in sys.stdin:
            request = json.loads(line)
            if 'replyTo' in request:
                with lock:
                    entry = pending.get(request['replyTo'])
                    if entry:
                        entry[1].update(request)
                        entry[0].set()
            else:
                executor.submit(execute, request)
    finally:
        stopping.set()
        worker.close()
        with lock:
            for ready, receipt in pending.values():
                ready.set()
        executor.shutdown(wait=True)
        worker.close()


if __name__ == '__main__':
    main()

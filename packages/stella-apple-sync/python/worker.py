from __future__ import annotations

import inspect
import json
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
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.peer = peer
        self.bridge = bridge or AppleCalendarBridge()
        self.managers = {}

    def initialize(self, legacy_directory=None):
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
                        with sqlite3.connect(f'{source.resolve().as_uri()}?mode=ro', uri=True) as original, sqlite3.connect(target) as destination:
                            original.backup(destination)
                    except Exception:
                        target.unlink(missing_ok=True)
                        raise
            manager = cls(bridge=self.bridge, db_path=target, **{port: Peer(domain, self.peer)})
            self.managers[kind] = manager
        for manager in self.managers.values():
            manager.resume_auto()
        return True

    def invoke(self, method, arguments=None):
        arguments = dict(arguments or {})
        manager = self.managers[arguments.pop('kind', 'calendar')]
        methods = ('status', 'request_access', 'start_sync', 'configure', 'replace_links', 'disconnect', 'set_auto', 'items', 'lookup_item', 'preview', 'preview_items', 'one_time', 'sync', 'resolve_conflict', 'preview_batch', 'transfer_batch')
        if method == 'catalog':
            return [{'name': name, 'parameters': str(inspect.signature(getattr(manager, name)))} for name in methods if hasattr(manager, name)]
        if method not in methods or not hasattr(manager, method):
            raise ValueError('Unknown Apple operation')
        return getattr(manager, method)(**arguments)

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
            if action not in ('initialize', 'invoke'):
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

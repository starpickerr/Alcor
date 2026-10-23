"""Broker task rows without exposing Core's shared scheduler database to workers."""
from contextlib import closing, contextmanager
from hashlib import sha256
import json
import os
import tempfile
from pathlib import Path
import sqlite3

_COLUMNS = 'task_id,owner_agent_id,owner_user_id,enabled,next_run_at,data'
_SCHEMA = '''CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY, owner_agent_id TEXT NOT NULL,
    owner_user_id TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL,
    next_run_at TEXT, data TEXT NOT NULL)'''
_WRITES = frozenset({'create_task', 'update_task', 'delete_task', 'enable_task'})


def task_records_root(user_id):
    from common.runtime_identity import identity_scope
    from common.state_dir import user_root
    # Outside the worker's general private runtime directory: one Agent's
    # transaction copy must not become another Agent's implicit read root.
    with identity_scope(agent_id=None, user_id=user_id or 'local-default'):
        root = user_root() / 'plugins' / 'automation-records'
    return root


def task_records_path(source, agent_id, user_id):
    from core.plugins.storage import bound_data_handle
    root = task_records_root(user_id)
    key = sha256((str(Path(source).resolve()) + '\0' + agent_id + '\0' + (user_id or '')).encode()).hexdigest()
    handle = bound_data_handle('stella.automation', {'dbPath': str(root / key / 'tasks.sqlite')})
    return Path(handle['dbPath'])


def is_task_records_path(path, user_id):
    import re
    try:
        parts = Path(path).absolute().relative_to(task_records_root(user_id).absolute()).parts
    except ValueError:
        return False
    return len(parts) == 2 and re.fullmatch('[0-9a-f]{64}', parts[0]) is not None and parts[1] == 'tasks.sqlite'


class TaskRecords:
    def __init__(self, db, path, snapshot_fd, agent_id, user_id, method, payload, before):
        self.db, self.path, self.snapshot_fd = db, path, snapshot_fd
        self.agent_id, self.user_id = agent_id, user_id
        self.method, self.payload, self.before = method, payload, before

    def commit(self, result):
        if result['status'] != 'succeeded' or self.method not in _WRITES:
            return result
        from core.plugins.caller import remaining_invocation_seconds
        remaining_invocation_seconds()
        try:
            if self.path.is_symlink():
                raise ValueError('Worker replaced task storage with a symlink')
            # Pin the Core-created inode. Reopening a worker-controlled pathname
            # could turn Core into a deputy for reading another database.
            # Deserialize from the pinned file handle, rather than a Unix-only
            # descriptor pathname or the worker-controlled storage pathname.
            with os.fdopen(os.dup(self.snapshot_fd), 'rb') as snapshot:
                snapshot.seek(0)
                contents = snapshot.read()
            with closing(sqlite3.connect(':memory:')) as copy:
                copy.deserialize(contents)
                rows = copy.execute('SELECT ' + _COLUMNS + ' FROM tasks').fetchall()
            after = {row[0]: row for row in rows}
            for row in rows:
                data = json.loads(row[5])
                if (not isinstance(data, dict) or not isinstance(row[0], str) or not row[0]
                        or row[1:3] != (self.agent_id, self.user_id) or data.get('id') != row[0]
                        or data.get('owner_agent_id') != self.agent_id
                        or data.get('owner_user_id', '') != self.user_id
                        or int(data.get('enabled', True)) != row[3]
                        or data.get('next_run_at') != row[4]):
                    raise ValueError('Worker task record identity or fields changed')
            changed = {key for key in self.before.keys() | after.keys() if self.before.get(key) != after.get(key)}
            target = (self.payload.get('task', {}).get('id') or (result.get('value') or {}).get('id')
                      if self.method == 'create_task' else self.payload.get('task_id'))
            if changed - {target}:
                raise ValueError('Worker changed tasks outside the requested target')
            if self.method == 'create_task' and target in self.before:
                raise ValueError('Task already exists')
            if self.method != 'create_task' and target not in self.before:
                raise ValueError('Task not found')
            if self.method == 'delete_task' and target in after or self.method != 'delete_task' and target not in after:
                raise ValueError('Worker task change does not match the requested operation')
            for key in changed:
                existing = self.db.execute('SELECT owner_agent_id,owner_user_id FROM tasks WHERE task_id=?', (key,)).fetchone()
                if existing and tuple(existing) != (self.agent_id, self.user_id):
                    raise ValueError('Task ID is unavailable')
                if key not in after:
                    self.db.execute('DELETE FROM tasks WHERE task_id=? AND owner_agent_id=? AND owner_user_id=?',
                                    (key, self.agent_id, self.user_id))
                else:
                    self.db.execute('INSERT INTO tasks(' + _COLUMNS + ') VALUES (?,?,?,?,?,?) '
                        'ON CONFLICT(task_id) DO UPDATE SET enabled=excluded.enabled,next_run_at=excluded.next_run_at,data=excluded.data',
                        after[key])
            remaining_invocation_seconds()
            self.db.commit()
        except (ValueError, TypeError, KeyError, sqlite3.Error):
            self.db.rollback()
            return {'status': 'failed', 'sideEffects': 'none',
                    'error': {'code': 'invalid_argument', 'message': 'Task transaction was rejected; no business data changed.'}}
        return result


@contextmanager
def task_records(source, agent_id, user_id, method, payload):
    from core.plugins.locks import package_lock
    from core.plugins.management import default_store
    source = Path(source)
    source.parent.mkdir(parents=True, exist_ok=True)
    path = task_records_path(source, agent_id, user_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = 'automation-records:' + sha256(str(path).encode()).hexdigest()
    # The copy lock serializes readers too, including a second Core process.
    with package_lock(default_store().root, key, exclusive=True):
        db = sqlite3.connect(source, timeout=10)
        snapshot_fd = None
        try:
            db.execute(_SCHEMA)
            db.commit()
            if method in _WRITES:
                db.execute('BEGIN IMMEDIATE')
            before = {row[0]: row for row in db.execute('SELECT ' + _COLUMNS +
                ' FROM tasks WHERE owner_agent_id=? AND owner_user_id=?', (agent_id, user_id))}
            # Build outside the writable worker root, then publish atomically.
            # Core never writes through a pathname a worker can redirect.
            with tempfile.TemporaryDirectory(prefix='.task-copy-', dir=path.parent.parent) as folder:
                prepared = Path(folder) / 'tasks.sqlite'
                with closing(sqlite3.connect(prepared)) as copy, copy:
                    copy.execute(_SCHEMA)
                    copy.executemany('INSERT INTO tasks(' + _COLUMNS + ') VALUES (?,?,?,?,?,?)', before.values())
                snapshot_fd = os.open(prepared, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                for suffix in ('-wal', '-shm', '-journal'):
                    Path(str(path) + suffix).unlink(missing_ok=True)
                os.replace(prepared, path)
            yield TaskRecords(db, path, snapshot_fd, agent_id, user_id, method, payload, before)
        finally:
            db.rollback()
            db.close()
            if snapshot_fd is not None:
                os.close(snapshot_fd)

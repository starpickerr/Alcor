"""One-time, read-only import from the retired Stella collaboration owner."""
import json
import sqlite3
import tempfile
from pathlib import Path


def import_stella(store, legacy_directory):
    with store._connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS hub_imports (source TEXT PRIMARY KEY)')
        db.execute('CREATE TABLE IF NOT EXISTS legacy_jobs (id TEXT PRIMARY KEY, document_json TEXT NOT NULL)')
        if db.execute('SELECT 1 FROM hub_imports WHERE source=?', ('stella-sqlite',)).fetchone():
            return
    source = Path(legacy_directory) / 'agent-hub.sqlite3' if legacy_directory else None
    if source is None or not source.is_file():
        return
    with tempfile.TemporaryDirectory(prefix='hub-import-', dir=store.db_path.parent) as temporary:
        snapshot = sqlite3.connect(str(Path(temporary) / 'snapshot.sqlite3'))
        try:
            with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as original:
                original.backup(snapshot)
            if snapshot.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('旧协作数据库校验失败，原数据未修改。')
            with store.lock, store._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for table, key in [('provider_configs', 'provider_config_id'), ('external_runs', 'execution_id'), ('hub_tasks', 'task_id'), ('hub_requests', 'request_id')]:
                    columns = [row[1] for row in snapshot.execute(f'PRAGMA table_info({table})')]
                    if not columns:
                        continue
                    target_columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
                    if set(columns) - target_columns:
                        raise ValueError('旧协作数据结构更新，停止迁移以保留原数据。')
                    for row in snapshot.execute(f'SELECT * FROM {table}'):
                        existing = db.execute(f'SELECT {",".join(columns)} FROM {table} WHERE {key}=?', (row[columns.index(key)],)).fetchone()
                        if existing and tuple(existing) != tuple(row):
                            raise ValueError('协作记录标识冲突，停止导入，未覆盖已有记录。')
                        if not existing:
                            db.execute(f'INSERT INTO {table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', tuple(row))
                db.execute('INSERT INTO hub_imports VALUES (?)', ('stella-sqlite',))
        finally:
            snapshot.close()


def import_jobs(store, jobs):
    with store.lock, store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for job in jobs:
            marker = 'stella-kv:' + job['id']
            if db.execute('SELECT 1 FROM hub_imports WHERE source=?', (marker,)).fetchone():
                continue
            saved = dict(job)
            if saved['status'] in ('starting', 'running'):
                saved['status'] = 'interrupted'
                saved['diagnostic'] = (saved.get('diagnostic', '') + '\n旧执行结果尚未确认，请先查看原生会话。').strip()[:4096]
            document = json.dumps(saved, ensure_ascii=False)
            existing = db.execute('SELECT document_json FROM legacy_jobs WHERE id=?', (job['id'],)).fetchone()
            if existing and json.loads(existing[0]) != saved:
                raise ValueError('旧委托标识冲突，未覆盖现有数据。')
            if not existing:
                db.execute('INSERT INTO legacy_jobs VALUES (?,?)', (job['id'], document))
            db.execute('INSERT INTO hub_imports VALUES (?)', (marker,))

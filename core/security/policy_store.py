"""Transactional subject policies, colocated with existing authorization facts."""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3


def policy_path():
    from core.plugins.authz import _authorization_path
    return _authorization_path()


class RunPolicyStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else policy_path()

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS run_policies (
                user TEXT NOT NULL, agent TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT NOT NULL,
                revision INTEGER NOT NULL, body TEXT NOT NULL, original TEXT NOT NULL,
                PRIMARY KEY(user,agent,kind,subject))''')
            db.execute('BEGIN IMMEDIATE')
            os.chmod(self.path, 0o600)
            yield db

    @staticmethod
    def _read(db, key):
        row = db.execute('SELECT revision,body FROM run_policies WHERE user=? AND agent=? AND kind=? AND subject=?', key).fetchone()
        if row is None:
            return None
        return {**json.loads(row[1]), 'policy_revision': row[0]}

    def get(self, key):
        with self.transaction() as db:
            return self._read(db, key)

    def ensure(self, key, policy):
        with self.transaction() as db:
            current = self._read(db, key)
            if current is not None:
                return current
            body = json.dumps(policy, ensure_ascii=False, allow_nan=False)
            db.execute('INSERT INTO run_policies VALUES (?,?,?,?,1,?,?)', (*key, body, body))
            return {**deepcopy(policy), 'policy_revision': 1}

    def update(self, key, change, *, expected_revision=None):
        with self.transaction() as db:
            current = self._read(db, key)
            revision = current['policy_revision'] if current else 0
            if expected_revision is not None and revision != expected_revision:
                raise ValueError('Run policy revision changed; refresh and retry')
            updated = change(current)
            updated.pop('policy_revision', None)
            body = json.dumps(updated, ensure_ascii=False, allow_nan=False)
            db.execute('''INSERT INTO run_policies VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(user,agent,kind,subject) DO UPDATE SET revision=excluded.revision,body=excluded.body''',
                       (*key, revision + 1, body, body))
            return {**updated, 'policy_revision': revision + 1}

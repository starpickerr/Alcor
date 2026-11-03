"""Core-owned durable intent, settings revisions and lifecycle operation facts.

Reading management state never imports or starts a plugin. SQLite transactions
serialize writers across processes; runtime state is distinct from user intent.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import uuid


class RevisionConflict(ValueError):
    pass


class ManagementStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / 'management' / 'state.sqlite'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS source_revisions (plugin TEXT NOT NULL, user TEXT NOT NULL,
                    revision INTEGER NOT NULL, PRIMARY KEY(plugin,user));
                CREATE TABLE IF NOT EXISTS instances (
                    plugin TEXT NOT NULL, user TEXT NOT NULL,
                    intent TEXT NOT NULL DEFAULT 'enabled', state TEXT NOT NULL DEFAULT 'stopped',
                    error TEXT, PRIMARY KEY(plugin,user));
                CREATE TABLE IF NOT EXISTS settings (
                    plugin TEXT NOT NULL, user TEXT NOT NULL, settings_id TEXT NOT NULL,
                    category TEXT NOT NULL, revision INTEGER NOT NULL, value TEXT NOT NULL,
                    PRIMARY KEY(plugin,user,settings_id,category));
                CREATE TABLE IF NOT EXISTS settings_secret_fields (
                    plugin TEXT NOT NULL, user TEXT NOT NULL, settings_id TEXT NOT NULL,
                    category TEXT NOT NULL, field TEXT NOT NULL,
                    PRIMARY KEY(plugin,user,settings_id,category,field));
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, plugin TEXT NOT NULL, user TEXT NOT NULL, kind TEXT NOT NULL,
                    stage TEXT NOT NULL, facts TEXT NOT NULL, error TEXT);
            ''')

    @contextmanager
    def _db(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.row_factory = sqlite3.Row
            yield db

    def has_instance(self, plugin_id: str, user_id: str) -> bool:
        with self._db() as db:
            return db.execute('SELECT 1 FROM instances WHERE plugin=? AND user=?', (plugin_id, user_id)).fetchone() is not None

    def runtime_state(self, plugin_id: str, user_id: str) -> str:
        with self._db() as db:
            row = db.execute('SELECT state FROM instances WHERE plugin=? AND user=?', (plugin_id, user_id)).fetchone()
        return row[0] if row else 'stopped'

    def users(self, plugin_id: str) -> list[str]:
        with self._db() as db:
            return [row[0] for row in db.execute('SELECT user FROM instances WHERE plugin=? ORDER BY user', (plugin_id,))]

    def instance(self, plugin_id: str, user_id: str) -> dict:
        with self._db() as db:
            row = db.execute('SELECT * FROM instances WHERE plugin=? AND user=?', (plugin_id, user_id)).fetchone()
        if row is None:
            return {'pluginId': plugin_id, 'userId': user_id, 'intent': 'enabled', 'state': 'stopped', 'error': None}
        return {'pluginId': plugin_id, 'userId': user_id, 'intent': row['intent'],
                'state': row['state'] if row['intent'] == 'enabled' else 'stopped', 'error': row['error']}

    def set_intent(self, plugin_id: str, user_id: str, intent: str) -> dict:
        if intent not in {'enabled', 'disabled', 'removed'}:
            raise ValueError('invalid plugin intent')
        previous = self.instance(plugin_id, user_id)['intent']
        with self._db() as db:
            db.execute('''INSERT INTO instances(plugin,user,intent) VALUES (?,?,?)
                ON CONFLICT(plugin,user) DO UPDATE SET intent=excluded.intent,
                state=CASE WHEN excluded.intent='enabled' THEN instances.state ELSE 'stopped' END''',
                       (plugin_id, user_id, intent))
        if previous != intent:
            self.bump_source_revision(plugin_id, user_id)
        return self.instance(plugin_id, user_id)

    def set_runtime(self, plugin_id: str, user_id: str, state: str, *, error: str | None = None):
        if state not in {'stopped', 'validating', 'starting', 'active', 'degraded', 'blocked', 'failed', 'maintenance', 'draining'}:
            raise ValueError('invalid runtime state')
        with self._db() as db:
            db.execute('''INSERT INTO instances(plugin,user,state,error) VALUES (?,?,?,?)
                ON CONFLICT(plugin,user) DO UPDATE SET state=excluded.state,error=excluded.error''',
                       (plugin_id, user_id, state, error))

    def source_revision(self, plugin_id: str, user_id: str) -> int:
        with self._db() as db:
            row = db.execute('SELECT revision FROM source_revisions WHERE plugin=? AND user=?', (plugin_id, user_id)).fetchone()
        return row[0] if row else 1

    def bump_source_revision(self, plugin_id: str, user_id: str):
        with self._db() as db:
            db.execute('INSERT INTO source_revisions VALUES (?,?,2) ON CONFLICT(plugin,user) DO UPDATE SET revision=revision+1',
                       (plugin_id, user_id))

    def settings(self, plugin_id, user_id, settings_id, category) -> dict:
        key = (plugin_id, user_id, settings_id, category)
        with self._db() as db:
            row = db.execute('SELECT revision,value FROM settings WHERE plugin=? AND user=? AND settings_id=? AND category=?',
                             key).fetchone()
            secrets = [item[0] for item in db.execute('''SELECT field FROM settings_secret_fields
                WHERE plugin=? AND user=? AND settings_id=? AND category=? ORDER BY field''', key)]
        return {'revision': row['revision'] if row else 1,
                'values': json.loads(row['value']) if row else {}, 'secretFields': secrets}

    def secret_categories(self, plugin_id, user_id):
        with self._db() as db:
            return [tuple(row) for row in db.execute('''SELECT DISTINCT settings_id,category
                FROM settings_secret_fields WHERE plugin=? AND user=? ORDER BY settings_id,category''', (plugin_id, user_id))]

    def credential_plugins(self, user_id):
        with self._db() as db:
            return [row[0] for row in db.execute('SELECT DISTINCT plugin FROM settings_secret_fields WHERE user=? ORDER BY plugin', (user_id,))]

    def save_settings(self, plugin_id, user_id, settings_id, category, changes: dict, *, expected_revision: int,
                      secret_fields=()) -> dict:
        if not isinstance(changes, dict) or not changes:
            raise ValueError('settings changes must be a nonempty object')
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError('invalid settings revision')
        key = (plugin_id, user_id, settings_id, category)
        with self._db() as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,value FROM settings WHERE plugin=? AND user=? AND settings_id=? AND category=?', key).fetchone()
            revision = row['revision'] if row else 1
            if revision != expected_revision:
                raise RevisionConflict('settings changed; reload and merge the retained draft')
            previous = json.loads(row['value']) if row else {}
            secrets = set(secret_fields) | {item[0] for item in db.execute('''SELECT field FROM settings_secret_fields
                WHERE plugin=? AND user=? AND settings_id=? AND category=?''', key)}
            values = {**previous, **changes}
            if secrets:
                from core.security.control import require_owner
                from core.security import credential_values, env_store
                import sys
                require_owner()
                if any(not isinstance(value, str) for field, value in changes.items() if field in secrets):
                    raise ValueError('secret settings must be strings')
                if sys.platform != 'darwin' and any(changes.get(field) for field in secrets):
                    raise PermissionError('Protected plugin credential storage requires macOS Keychain')
                names = {field: json.dumps([*key, field], separators=(',', ':')) for field in secrets}
                before = {names[field]: previous[field] for field in secrets if isinstance(previous.get(field), str)}
                selected = {names[field]: values[field] for field in secrets
                            if isinstance(values.get(field), str) and values[field]}

            def commit(saved):
                for field in secrets:
                    if field in values and not isinstance(values[field], str):
                        continue  # An unrelated malformed field remains hidden and individually repairable.
                    values.pop(field, None)
                    if names[field] in saved:
                        values[field] = saved[names[field]]
                db.execute('''INSERT INTO settings VALUES (?,?,?,?,?,?) ON CONFLICT(plugin,user,settings_id,category)
                    DO UPDATE SET revision=excluded.revision,value=excluded.value''',
                           (*key, revision + 1, json.dumps(values, ensure_ascii=False, allow_nan=False)))
                db.execute('DELETE FROM settings_secret_fields WHERE plugin=? AND user=? AND settings_id=? AND category=?', key)
                db.executemany('INSERT INTO settings_secret_fields VALUES (?,?,?,?,?)',
                               [(*key, field) for field in secrets if field in values])
                # Retirement is safe only after SQLite has actually published the new references.
                db.commit()

            if secrets:
                credential_values.publish(self.path, selected, before, env_store.keychain_for, commit)
            else:
                commit({})
        return self.settings(*key)

    def begin_operation(self, plugin_id, user_id, kind, facts: dict) -> dict:
        operation_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,NULL)',
                       (operation_id, plugin_id, user_id, kind, 'staged', json.dumps(facts, allow_nan=False)))
        return self.operation(operation_id)

    def advance(self, operation_id, stage, facts: dict | None = None, *, error: str | None = None) -> dict:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT facts FROM operations WHERE id=?', (operation_id,)).fetchone()
            if row is None:
                raise KeyError(operation_id)
            merged = {**json.loads(row['facts']), **(facts or {})}
            db.execute('UPDATE operations SET stage=?,facts=?,error=? WHERE id=?',
                       (stage, json.dumps(merged, allow_nan=False), error, operation_id))
        return self.operation(operation_id)

    def operation(self, operation_id) -> dict:
        with self._db() as db:
            row = db.execute('SELECT * FROM operations WHERE id=?', (operation_id,)).fetchone()
        if row is None:
            raise KeyError(operation_id)
        return {'operationId': row['id'], 'pluginId': row['plugin'], 'userId': row['user'],
                'kind': row['kind'], 'stage': row['stage'], 'facts': json.loads(row['facts']), 'error': row['error']}

    def unfinished(self, plugin_id: str | None = None) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT id FROM operations WHERE stage NOT IN ('completed','failed')").fetchall()
        operations = [self.operation(row['id']) for row in rows]
        return [op for op in operations if plugin_id is None or op['pluginId'] == plugin_id]


def default_store() -> ManagementStore:
    from common.state_dir import shared_root

    return ManagementStore(shared_root() / 'plugin-packages')

"""Atomic, reversible control-store migration. Original business stores stay intact."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import uuid

from core.security.policy_store import RunPolicyStore
from core.security.run_policy import from_legacy


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


class PermissionMigration:
    def __init__(self, *, sources=None):
        self.sources = sources or discover_subjects
        self.store = RunPolicyStore()

    @staticmethod
    def _user():
        from common.runtime_identity import current_identity
        return current_identity().user_id or 'local-default'

    @staticmethod
    def _table(db):
        db.execute('CREATE TABLE IF NOT EXISTS permission_migrations (user TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def _state(self, db):
        self._table(db)
        row = db.execute('SELECT body FROM permission_migrations WHERE user=?', (self._user(),)).fetchone()
        return json.loads(row[0]) if row else None

    def _plan(self):
        entries = []
        seen = set()
        for source in self.sources():
            kind, subject, agent = source['kind'], source['subject'], source['agent']
            if kind not in {'template', 'session', 'task'} or not all(isinstance(value, str) and value for value in (subject, agent)):
                raise ValueError('invalid trusted migration subject')
            key = (self._user(), agent, kind, subject)
            if key in seen:
                raise ValueError('duplicate migration subject')
            seen.add(key)
            current = self.store.get(key)
            entries.append({'kind': kind, 'subject': subject, 'agent': agent, 'key': key,
                'policy': from_legacy(source['legacy']), 'existing_revision': current['policy_revision'] if current else 0})
        return sorted(entries, key=lambda row: row['key'])

    def preview(self):
        entries = self._plan()
        with self.store.transaction() as db:
            state = self._state(db)
            from core.security.resource_migration import inputs
            resources = inputs(db, self._user()) if not state or not state.get('resource_version') else None
        fingerprint = sha256(_json({'subjects': entries, 'resources': resources}).encode()).hexdigest()
        return {'fingerprint': fingerprint, 'pending': sum(not row['existing_revision'] for row in entries),
                'needs_confirmation': sum(row['policy'].get('migration_status') != 'complete' for row in entries),
                'status': state['status'] if state else 'not_applied',
                'subjects': [{key: row[key] for key in ('kind', 'subject', 'agent', 'existing_revision')} for row in entries]}

    def _apply_subject(self, db, entry):
        key = tuple(entry['key'])
        if self.store._read(db, key) is not None:
            return None
        body = _json(entry['policy'])
        db.execute('INSERT INTO run_policies VALUES (?,?,?,?,1,?,?)', (*key, body, body))
        return {'key': key, 'revision': 1, 'body': body}

    def apply(self, fingerprint):
        entries = self._plan()
        with self.store.transaction() as db:
            state = self._state(db)
            from core.security.resource_migration import inputs, apply as migrate_resources
            resources = inputs(db, self._user()) if not state or not state.get('resource_version') else None
            if sha256(_json({'subjects': entries, 'resources': resources}).encode()).hexdigest() != fingerprint:
                raise ValueError('Migration inputs changed; preview again')
            if state and state['status'] == 'applied':
                return {key: state[key] for key in ('status', 'migration_id')}
            created = []
            for entry in entries:
                live = self.store._read(db, tuple(entry['key']))
                if (live['policy_revision'] if live else 0) != entry['existing_revision']:
                    raise ValueError('Migration policy changed; preview again')
                row = self._apply_subject(db, entry)
                if row:
                    created.append(row)
            if resources is not None:
                migrate_resources(db, self._user(), entries, resources)
            original_resources = resources if resources is not None else (state or {}).get('resource_originals')
            state = {'status': 'applied', 'resource_version': 1, 'resource_originals': original_resources,
                     'migration_id': str(uuid.uuid4()), 'created': created,
                     'original_inputs': entries, 'source_fingerprint': fingerprint}
            db.execute('INSERT INTO permission_migrations VALUES (?,?) ON CONFLICT(user) DO UPDATE SET body=excluded.body',
                       (self._user(), _json(state)))
        return {key: state[key] for key in ('status', 'migration_id')}

    def verify(self):
        with self.store.transaction() as db:
            state = self._state(db)
            if not state:
                return {'status': 'not_applied', 'valid': False}
            missing = sum(self.store._read(db, tuple(row['key'])) is None for row in state['created'])
            return {'status': state['status'], 'valid': state['status'] == 'applied' and not missing,
                    'migration_id': state['migration_id'], 'missing': missing}

    def recover(self):
        with self.store.transaction() as db:
            state = self._state(db)
            if not state or state['status'] == 'recovered':
                return {'status': 'recovered'}
            for row in state['created']:
                live = self.store._read(db, tuple(row['key']))
                expected = {**json.loads(row['body']), 'policy_revision': row['revision']}
                if live != expected:
                    raise ValueError('Migrated policies changed; recovery cannot erase later owner edits')
            for row in state['created']:
                db.execute('DELETE FROM run_policies WHERE user=? AND agent=? AND kind=? AND subject=?', row['key'])
            state['status'] = 'recovered'
            db.execute('UPDATE permission_migrations SET body=? WHERE user=?', (_json(state), self._user()))
        return {'status': 'recovered', 'migration_id': state['migration_id']}


def _metadata_json(path):
    if not Path(path).is_file():
        return {}
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Invalid legacy permission metadata; repair it before migration')
    return value


def _task_policy(task, agent):
    from agent.permission.policy import global_mode
    from agent.permission.security_policy import _legacy_resolve
    action = task.get('action') or {}
    legacy = _legacy_resolve(global_mode(), project_dir=action.get('execution_project_dir'),
        session_id=action.get('execution_session_id') or action.get('source_session_id'), agent_id=agent)
    if task.get('run_permission_mode') in {'read-only', 'workspace-write', 'full-access'}:
        legacy = {**legacy, 'preset': task['run_permission_mode'], 'rules': {}}
    if task.get('run_policy_version') == 1:
        return {'error': 'Missing host-owned task policy'}  # Never repair an interrupted new task by widening.
    return legacy


def discover_subjects():
    """Read identifiers and permission metadata, without loading messages or run results."""
    from common.runtime_identity import current_identity, identity_scope
    from common.state_dir import memory_index_db, scheduler_file
    from agent.registry import get_agent_registry
    from agent.permission.policy import global_mode
    from agent.permission.security_policy import _legacy_resolve
    from agent.workspace import session_prefs, project_store
    user = current_identity().user_id or 'local-default'
    owners = ('', 'local-default') if user == 'local-default' else (user, user)
    registry = get_agent_registry()
    yield {'kind': 'template', 'subject': 'default', 'agent': '*', 'legacy': _legacy_resolve(global_mode())}
    prefs = _metadata_json(session_prefs._store_file()).get('sessions') or {}
    projects = _metadata_json(project_store._store_file()).get('sessions') or {}
    for profile in registry.list():
        with identity_scope(agent_id=profile.id):
            ids = set()
            for key in set(prefs) | set(projects):
                owner, separator, session = key.partition('::')
                if separator and owner in {profile.id, 'default' if profile.id == registry.default_agent_id else profile.id}:
                    ids.add(session)
            path = memory_index_db(ensure=False)
            if path.is_file():
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'").fetchone():
                        ids.update(row[0] for row in db.execute('SELECT session_id FROM sessions'))
            for session in sorted(ids):
                project = project_store.get_project_dir(session, profile.id)
                yield {'kind': 'session', 'subject': session, 'agent': profile.id,
                       'legacy': _legacy_resolve(global_mode(), project_dir=project, session_id=session, agent_id=profile.id)}
            file = scheduler_file()
            db_path = file.with_name('automation.db')
            tasks = []
            if db_path.is_file():
                with closing(sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)) as db:
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
                        tasks = [dict(id=row[0], owner_user_id=user, run_permission_mode=row[1], run_policy_version=row[2], action={
                            'source_session_id': row[3], 'execution_session_id': row[4], 'execution_project_dir': row[5]})
                            for row in db.execute('''SELECT task_id,json_extract(data,'$.run_permission_mode'),
                                json_extract(data,'$.run_policy_version'),json_extract(data,'$.action.source_session_id'),
                                json_extract(data,'$.action.execution_session_id'),json_extract(data,'$.action.execution_project_dir')
                                FROM tasks WHERE owner_agent_id=? AND owner_user_id IN (?,?)''',
                                (profile.id, *owners))]
            else:
                # The legacy scheduler owns this JSON format; only policy fields
                # reach the migration plan, never task prompts or deliveries.
                tasks = list((_metadata_json(file).get('tasks') or {}).values())
            for task in tasks:
                if (task.get('owner_user_id', '') not in owners or
                        task.get('owner_agent_id') not in {None, '', profile.id}):
                    continue
                yield {'kind': 'task', 'subject': str(task['id']), 'agent': profile.id, 'legacy': _task_policy(task, profile.id)}


def ensure_migrated():
    migration = PermissionMigration()
    state = migration.verify()
    if state['status'] == 'applied':
        if not state['valid']:
            raise PermissionError('Permission migration is incomplete; verify or recover before editing')
        return state
    return migration.apply(migration.preview()['fingerprint'])


def resource_authority_migrated():
    migration = PermissionMigration()
    with migration.store.transaction() as db:
        migration._table(db)
        row = db.execute("SELECT json_extract(body,'$.resource_version') FROM permission_migrations WHERE user=?", (migration._user(),)).fetchone()
        return bool(row and row[0] == 1)

"""Owner-issued resource grants. Capability policies never create these records."""
from datetime import datetime, timezone
import json
import uuid

from core.security.policy_store import RunPolicyStore
from core.security.run_policy import OPERATIONS


def _expiry(value):
    date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('expiry requires timezone')
    return date


def _live(row):
    return not row['revoked'] and (row['valid_until'] is None or
                                  _expiry(row['valid_until']) > datetime.now(timezone.utc))


class ResourceGrantStore(RunPolicyStore):
    """Transactional exact resources, with live, attenuating parent references."""

    @staticmethod
    def _rows(db):
        db.execute('CREATE TABLE IF NOT EXISTS resource_grants (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        return {key: json.loads(body) for key, body in db.execute('SELECT id,body FROM resource_grants')}

    @staticmethod
    def _write(db, row):
        db.execute('INSERT INTO resource_grants VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                   (row['grant_id'], json.dumps(row, allow_nan=False)))

    @staticmethod
    def _valid(row, rows, seen=None):
        seen = set() if seen is None else seen
        if row['grant_id'] in seen or not _live(row):
            return False
        seen.add(row['grant_id'])
        parent = row.get('parent_grant_id')
        if not parent:
            return True
        parent = rows.get(parent)
        return bool(parent and parent['user_id'] == row['user_id'] and parent['resource'] == row['resource'] and
                    set(row['operations']) <= set(parent['operations']) and
                    set(row['purposes']) <= set(parent['purposes']) and
                    (parent['valid_until'] is None or (row['valid_until'] is not None and
                     _expiry(row['valid_until']) <= _expiry(parent['valid_until']))) and
                    ResourceGrantStore._valid(parent, rows, seen))

    def issue(self, *, user_id, subject, resource, operations, purposes=('interactive',), valid_until=None):
        operations, purposes = sorted(set(operations)), sorted(set(purposes))
        if not user_id or not subject or set(resource) != {'kind', 'id'} or not all(
                isinstance(value, str) and value for value in resource.values()):
            raise ValueError('exact resource and subject required')
        if not operations or not set(operations) <= set(OPERATIONS) or not purposes or not set(purposes) <= {'interactive', 'background'}:
            raise ValueError('invalid operations or purposes')
        if valid_until is not None and _expiry(valid_until) <= datetime.now(timezone.utc):
            raise ValueError('expiry is in the past')
        row = dict(grant_id=str(uuid.uuid4()), user_id=user_id, subject=subject, resource=dict(resource),
                   operations=operations, purposes=purposes, valid_until=valid_until, revoked=False,
                   revision=1, parent_grant_id=None)
        with self.transaction() as db:
            self._rows(db)
            self._write(db, row)
        return row

    def delegate(self, parent_id, *, user_id, subject, operations, purposes, valid_until=None):
        with self.transaction() as db:
            rows = self._rows(db)
            parent = rows.get(parent_id)
            if not parent or parent['user_id'] != user_id or not self._valid(parent, rows):
                raise PermissionError('parent resource grant unavailable')
            row = {**parent, 'grant_id': str(uuid.uuid4()), 'subject': subject, 'parent_grant_id': parent_id,
                   'operations': sorted(set(operations)), 'purposes': sorted(set(purposes)),
                   'valid_until': valid_until or parent['valid_until'], 'revision': 1}
            if not subject or not row['operations'] or not row['purposes'] or not self._valid(row, rows):
                raise PermissionError('resource delegation exceeds parent')
            self._write(db, row)
            return row

    def check(self, *, user_id, subject, resource, operation, purpose='interactive'):
        with self.transaction() as db:
            rows = self._rows(db)
            for row in rows.values():
                if (row['user_id'] == user_id and row['subject'] == subject and row['resource'] == resource and
                        operation in row['operations'] and purpose in row['purposes'] and self._valid(row, rows)):
                    return row
        raise PermissionError('resource grant denied; authorize the resource and retry')

    def list_owned(self, user_id):
        with self.transaction() as db:
            rows = self._rows(db)
            return [{**row, 'effective': self._valid(row, rows)} for row in rows.values() if row['user_id'] == user_id]

    def revoke(self, grant_id, *, user_id, expected_revision):
        with self.transaction() as db:
            row = self._rows(db).get(grant_id)
            if not row or row['user_id'] != user_id:
                raise PermissionError('resource grant not owned')
            if row['revision'] != expected_revision:
                raise ValueError('resource grant revision changed; refresh and retry')
            self._write(db, {**row, 'revoked': True, 'revision': row['revision'] + 1})

    @staticmethod
    def _restrictions(db):
        db.execute('''CREATE TABLE IF NOT EXISTS resource_restrictions (
            user TEXT NOT NULL, subject TEXT NOT NULL, connection TEXT NOT NULL,
            revision INTEGER NOT NULL, body TEXT NOT NULL, original TEXT NOT NULL,
            PRIMARY KEY(user,subject,connection))''')

    def connection_restrictions(self, *, user_id, subjects, connections):
        with self.transaction() as db:
            self._restrictions(db)
            return [json.loads(row[0]) for row in db.execute('SELECT body,subject,connection FROM resource_restrictions WHERE user=?',
                (user_id,)) if row[1] in subjects and row[2] in connections]

    def connection_revision(self, *, user_id, connection):
        names = {connection}
        if connection in {'mcp:email', 'mcp:elearning'}:
            names.add(connection.removeprefix('mcp:'))
        with self.transaction() as db:
            self._restrictions(db)
            return sum(row[1] for row in db.execute('SELECT connection,revision FROM resource_restrictions WHERE user=? AND subject=?',
                (user_id, 'user:' + user_id)) if row[0] in names)

    def update_owned_connection(self, *, user_id, connection, policy, expected_revision):
        from agent.permission.security_policy import validate_patch
        policy = validate_patch({'connections': {connection: policy}})['connections'][connection]
        names = {connection}
        if connection in {'mcp:email', 'mcp:elearning'}:
            names.add(connection.removeprefix('mcp:'))
        with self.transaction() as db:
            self._restrictions(db)
            revision = sum(row[1] for row in db.execute('SELECT connection,revision FROM resource_restrictions WHERE user=? AND subject=?',
                (user_id, 'user:' + user_id)) if row[0] in names)
            if type(expected_revision) is not int or expected_revision != revision:
                raise ValueError('Connection authorization revision changed; refresh and retry')
            for name in names:
                db.execute('DELETE FROM resource_restrictions WHERE user=? AND subject=? AND connection=?', (user_id, 'user:' + user_id, name))
            body = json.dumps(policy, sort_keys=True)
            db.execute('INSERT INTO resource_restrictions VALUES (?,?,?,?,?,?)',
                (user_id, 'user:' + user_id, connection, revision + 1, body, body))

    def set_connection_restriction(self, *, user_id, subject, connection, policy):
        from agent.permission.security_policy import validate_patch
        policy = validate_patch({'connections': {connection: policy}})['connections'][connection]
        body = json.dumps(policy, sort_keys=True)
        with self.transaction() as db:
            self._restrictions(db)
            db.execute('''INSERT INTO resource_restrictions VALUES (?,?,?,1,?,?)
                ON CONFLICT(user,subject,connection) DO UPDATE SET body=excluded.body,revision=resource_restrictions.revision+1''',
                       (user_id, subject, connection, body, body))

    def check_legacy_container(self, *, user_id, subject, connection, operation, purpose):
        """Only a migration-created all-owned container can authorize descendants.

        Ordinary connection grants allow connection operations, not account bodies.
        The caller must first bind the server's real user and resolve its public metadata.
        """
        with self.transaction() as db:
            rows = self._rows(db)
            for row in rows.values():
                if (row['user_id'] == user_id and row['subject'] == subject and row['resource'] ==
                        {'kind': 'connection', 'id': connection} and row.get('legacy_scope') == 'all_owned_resources'
                        and operation in row['operations'] and purpose in row['purposes'] and self._valid(row, rows)):
                    return row
        raise PermissionError('resource grant denied; authorize the resource and retry')

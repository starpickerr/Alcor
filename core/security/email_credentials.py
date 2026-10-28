"""Declared mailbox passwords in the original email.db account rows."""
from contextlib import closing, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from core.security import credential_values, env_store
from core.security.control import require_owner
from core.security.keychain import KeychainUnavailable


class EmailCredentialStore:
    def __init__(self, path, *, user_id):
        self.path = Path(path).resolve()
        self.user_id = user_id

    @contextmanager
    def _connect(self, *, write=False):
        # Never create/reset an absent or invalid business database.
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            with db:
                if write:
                    db.execute('PRAGMA secure_delete=ON')
                    db.execute('BEGIN IMMEDIATE')
                yield db

    @staticmethod
    def _rows(db):
        return [dict(row) for row in db.execute('SELECT * FROM email_accounts ORDER BY position, created_at')]

    @staticmethod
    def _row(db, account_id):
        row = db.execute('SELECT * FROM email_accounts WHERE id=?', (account_id,)).fetchone()
        if row is None:
            raise KeyError('Mailbox no longer exists; reload its settings')
        return dict(row)

    def _name(self, row):
        return 'EMAIL:' + json.dumps([self.user_id, row['id'], row['host'].casefold().rstrip('.'),
                                     row['port'], row['user'], 'password'], ensure_ascii=False)

    @staticmethod
    def _revision(row):
        return sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()

    def _view(self, row):
        value = row['password']
        if not isinstance(value, str) or (value.startswith(credential_values.REFERENCE) and not
                re.fullmatch(re.escape(credential_values.REFERENCE) + r'[0-9a-f]{32}', value)):
            state = 'repair_required'
        else:
            name = self._name(row)
            available = credential_values.statuses(self.path, {name: value}, env_store.keychain_for)[name]
            state = ('unavailable' if available is None else 'configured' if available else
                     'repair_required' if value.startswith(credential_values.REFERENCE) else 'not_configured')
        return {'id': row['id'], 'account': row['user'], 'state': state, 'revision': self._revision(row)}

    def inventory(self):
        require_owner()
        with self._connect() as db:
            return [self._view(row) for row in self._rows(db)]

    def migrate(self):
        require_owner()
        with self._connect(write=True) as db:
            rows = self._rows(db)
            # A malformed field remains repairable without blocking other accounts.
            selected = [row for row in rows if isinstance(row['password'], str)]
            before = {self._name(row): row['password'] for row in selected}
            def commit(saved):
                for row in selected:
                    value = saved[self._name(row)]
                    if value != row['password']:
                        db.execute('UPDATE email_accounts SET password=? WHERE id=?', (value, row['id']))
                db.commit()
            credential_values.publish(self.path, before, before, env_store.keychain_for, commit)
        return self.inventory()

    def save_password(self, account_id, value, *, expected_revision):
        require_owner()
        if not isinstance(value, str) or any(char in value for char in '\r\n\0'):
            raise ValueError('Invalid mailbox password')
        if value.startswith(credential_values.REFERENCE):
            raise ValueError('Enter a password through its original settings')
        with self._connect(write=True) as db:
            row = self._row(db, account_id)
            if self._revision(row) != expected_revision:
                raise ValueError('Mailbox changed; reload before saving')
            name = self._name(row)
            before = {name: row['password']} if isinstance(row['password'], str) else {}
            def commit(saved):
                db.execute('UPDATE email_accounts SET password=? WHERE id=?', (saved[name], account_id))
                db.commit()
            saved = credential_values.publish(self.path, {name: value}, before, env_store.keychain_for, commit)
            row['password'] = saved[name]
        return self._view(row)

    def resolve(self, account_id):
        """Trusted connector use only; reread the exact account on every login."""
        with self._connect() as db:
            row = self._row(db, account_id)
        return self._resolve(row)

    def _resolve(self, row):
        value = row['password']
        if not isinstance(value, str) or not value.startswith(credential_values.REFERENCE):
            raise KeychainUnavailable('Mailbox credential needs repair through its original settings')
        name = self._name(row)
        values, _ = credential_values.resolve(self.path, {name: value}, env_store.keychain_for)
        return values[name]

    def connect(self, account_id, *, check=None, cancel_event=None, timeout=30):
        """Trusted mailbox connector, bound to this account's current endpoint."""
        from core.security.imap_transport import connect_imap
        with self._connect() as db:
            row = self._row(db, account_id)
        revision = self._revision(row)
        password = self._resolve(row)
        def active():
            if check is not None:
                check()
            with self._connect() as db:
                current = self._row(db, account_id)
            if self._revision(current) != revision:
                raise PermissionError('Mailbox changed; the old connection cannot resume')
            name = self._name(current)
            status = credential_values.statuses(self.path, {name: current['password']}, env_store.keychain_for)[name]
            if status is not True:
                raise KeychainUnavailable('Mailbox credential is no longer available')
        return connect_imap(row['host'], row['port'], username=row['user'], password=password,
                            check=active, cancel_event=cancel_event, timeout=timeout)

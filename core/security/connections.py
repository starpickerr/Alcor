"""Transport intent and live cancellation; resource authorization stays separate."""
from hashlib import sha256
import threading
from urllib.parse import urlsplit
import weakref

from core.security.control import require_owner
from core.security.policy_store import RunPolicyStore

_LOCK = threading.RLock()
_LIVE = weakref.WeakSet()
_WATCHER = None


def _watch_connections():
    global _WATCHER
    while True:
        with _LOCK:
            leases = list(_LIVE)
            if not leases:
                _WATCHER = None
                return
        for lease in leases:
            try:
                lease.check()
            except Exception:
                # An unreadable policy store cannot leave old IO authorized.
                lease.close()
        threading.Event().wait(.05)


def current_user():
    from common.runtime_identity import current_identity
    return current_identity().user_id or 'local-default'


def service_id(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'https', 'http', 'imaps'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Connection must name a supported service without embedded credentials')
    host = parsed.hostname.lower()
    host = '[' + host + ']' if ':' in host else host
    port = parsed.port or {'https': 443, 'http': 80, 'imaps': 993}[parsed.scheme]
    origin = f'{parsed.scheme}://{host}:{port}'
    return 'service:' + sha256(origin.encode()).hexdigest(), origin


def connection_catalog():
    """Only actual configured services and fixed public mechanisms become rows."""
    from config import conf
    cfg = conf()
    indexed = {}
    def add(url, owner, name, configuration):
        try:
            identifier, endpoint = service_id(url)
        except (TypeError, ValueError):
            return
        row = indexed.setdefault(identifier, dict(id=identifier, name=name, endpoint=endpoint, owners=[], configure_url=configuration))
        row['owners'] = sorted(set([*row['owners'], owner]))
    from models.catalog import PROVIDER_MODELS
    from config import MODEL_CONFIG_TO_ENV
    # Reuse the settings catalog and credential mappings; no shadow endpoint configuration.
    for provider, metadata in PROVIDER_MODELS.items():
        key = metadata['api_key_field']
        if not cfg.get(key):
            continue
        field = metadata.get('api_base_key')
        if not field:
            prefix = MODEL_CONFIG_TO_ENV.get(key, '').removesuffix('_API_KEY')
            field = next((name for name, environment in MODEL_CONFIG_TO_ENV.items()
                          if environment == prefix + '_API_BASE'), None)
        endpoint = cfg.get(field) if field else None
        endpoint = endpoint or metadata.get('api_base_default')
        if endpoint:
            add(endpoint, 'model:' + provider, provider, '/models')
    for provider in cfg.get('custom_providers') or []:
        if isinstance(provider, dict) and provider.get('id') and provider.get('api_base'):
            add(provider['api_base'], 'model:custom:' + provider['id'], provider.get('name') or provider['id'], '/models')
    from agent.tools.tool_manager import ToolManager
    from urllib.parse import quote
    for server in ToolManager()._load_mcp_configs():
        if server.get('type', 'stdio') != 'stdio' and server.get('url'):
            add(server['url'], 'mcp:' + server['name'], server['name'], '/skills/mcp/' + quote(server['name'], safe='') + '?section=configuration')
    # The original business account store declares these endpoints; listing
    # connections must not start a Worker or read/decrypt password columns.
    import sqlite3
    from core.services.email_facade import _paths
    path = _paths()[0]
    if path.is_file():
        try:
            from contextlib import closing
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
                for account, host, port in db.execute('SELECT id,host,port FROM email_accounts'):
                    if isinstance(host, str) and host:
                        address = '[' + host + ']' if ':' in host else host
                        add(f'imaps://{address}:{port}', 'email:' + account, 'IMAP ' + host, '/email')
        except (OSError, sqlite3.Error):
            # A broken mailbox must not hide unrelated service controls.
            pass
    import os
    if cfg.get('github_token') or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN'):
        add('https://api.github.com', 'github', 'GitHub', '/security?tab=resources&credential=GITHUB_TOKEN')
    return [*indexed.values(),
        dict(id='public-browser', name='Public web', endpoint='', owners=['browser']),
        dict(id='public-packages', name='Package registries', endpoint='', owners=['runtime'])]


class ConnectionStore(RunPolicyStore):
    @staticmethod
    def _table(db):
        db.execute('''CREATE TABLE IF NOT EXISTS connection_intents (
            user TEXT NOT NULL, service TEXT NOT NULL, enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
            PRIMARY KEY(user,service))''')

    def state(self, user, service):
        with self.transaction() as db:
            self._table(db)
            row = db.execute('SELECT enabled,revision FROM connection_intents WHERE user=? AND service=?', (user, service)).fetchone()
            return dict(id=service, enabled=bool(row[0]), revision=row[1]) if row else dict(id=service, enabled=True, revision=0)

    def rows(self, user):
        with self.transaction() as db:
            self._table(db)
            return [dict(id=key, enabled=bool(enabled), revision=revision)
                    for key, enabled, revision in db.execute('SELECT service,enabled,revision FROM connection_intents WHERE user=?', (user,))]

    def save(self, user, service, enabled, expected_revision):
        with self.transaction() as db:
            self._table(db)
            row = db.execute('SELECT revision FROM connection_intents WHERE user=? AND service=?', (user, service)).fetchone()
            revision = row[0] if row else 0
            if revision != expected_revision:
                raise ValueError('Connection revision changed; refresh and retry')
            db.execute('''INSERT INTO connection_intents VALUES (?,?,?,?) ON CONFLICT(user,service)
                DO UPDATE SET enabled=excluded.enabled,revision=excluded.revision''', (user, service, int(enabled), revision + 1))
            return dict(id=service, enabled=enabled, revision=revision + 1)


class ConnectionLease:
    def __init__(self, store, user, service, revision):
        self.store, self.user, self.service, self.revision = store, user, service, revision
        self.revoked = False
        self.callbacks = []
        self.guards = []
        _LIVE.add(self)

    def _valid(self):
        state = self.store.state(self.user, self.service)
        return not self.revoked and state['enabled'] and state['revision'] == self.revision

    def check(self):
        try:
            for guard in tuple(self.guards):
                guard()
        except Exception:
            self.close()
            raise
        with _LOCK:
            valid = self._valid()
        if not valid:
            self.close()
            raise PermissionError('Connection was disabled or changed; the revoked request cannot resume')

    def attach(self, cancel):
        with _LOCK:
            if self._valid():
                self.callbacks.append(cancel)
                return
        try:
            cancel()
        finally:
            self.close()
        raise PermissionError('Connection was disabled before IO registration')

    def detach(self, cancel):
        with _LOCK:
            if cancel in self.callbacks:
                self.callbacks.remove(cancel)

    def close(self):
        with _LOCK:
            self.revoked = True
            callbacks, self.callbacks = self.callbacks, []
            _LIVE.discard(self)
        for cancel in reversed(callbacks):
            try:
                cancel()
            except (OSError, RuntimeError):
                pass

    def __enter__(self):
        self.check()
        return self

    def __exit__(self, *args):
        self.close()


class ConnectionControl:
    def __init__(self, *, store=None, catalog=None):
        self.store = store or ConnectionStore()
        self.catalog = catalog or connection_catalog

    def inventory(self):
        require_owner()
        user = current_user()
        rows = self.catalog()
        for row in rows:
            row.update(self.store.state(user, row['id']))
            with _LOCK:
                row['active_requests'] = sum(1 for lease in _LIVE if lease.user == user and lease.service == row['id'] and not lease.revoked)
        return {'connections': rows}

    def begin(self, service, *, cancel=None):
        global _WATCHER
        user = current_user()
        with _LOCK:
            state = self.store.state(user, service)
            if not state['enabled']:
                raise PermissionError('External connection is disabled')
            lease = ConnectionLease(self.store, user, service, state['revision'])
            if cancel:
                lease.callbacks.append(cancel)
            if _WATCHER is None:
                _WATCHER = threading.Thread(target=_watch_connections, name='connection-revocation', daemon=True)
                _WATCHER.start()
            return lease

    def save(self, service, *, enabled, expected_revision):
        require_owner()
        if type(enabled) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Connection state and current revision are required')
        if service not in {row['id'] for row in self.catalog()}:
            raise ValueError('Unknown connection; configure its owner first')
        user = current_user()
        with _LOCK:
            result = self.store.save(user, service, enabled, expected_revision)
            leases = [lease for lease in _LIVE if lease.user == user and lease.service == service]
            for lease in leases:
                lease.revoked = True  # Atomically invalidate before running IO callbacks.
        for lease in leases:
            lease.close()
        from agent.permission.audit import record
        record(event='connection_state', operation='enable' if enabled else 'disable', resource=service,
               decision='policy_change', reason='Owner changed connection intent; previous requests were revoked.')
        return result

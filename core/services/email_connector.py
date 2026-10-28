"""Explicit trusted mailbox connector; the managed Server has no credentials."""
import sys
import threading
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType

from common.runtime_identity import current_identity
from core.mcp.managed import ManagedMcpConfig, OPERATIONS, _installed_digest
from core.runtime.paths import source_digest
from core.security.control import require_owner
from core.security.credential_values import REFERENCE
from core.security.email_credentials import EmailCredentialStore
from core.security.imap_transport import connect_imap
from core.security.mcp_runtime import _launch_inputs

_MODULES = {}
_LOCK = threading.Lock()
# Only these stdlib-only business modules are trusted. server.py and arbitrary
# extension code are never imported into Core. SQL is from the same snapshot.
_TRUSTED_MODULES = ('hosts', 'imap', 'schema', 'service')


def _business(config):
    if (not isinstance(config, ManagedMcpConfig) or config.get('name') != 'email' or
            config.data_root is None or not config.package_digest or
            config.package_digest != _installed_digest('email')):
        raise PermissionError('Mailbox connector requires the Core-pinned provider')
    _, root = _launch_inputs(config)
    snapshot = {}
    digest = source_digest(root, snapshot=snapshot)
    if digest != config.package_digest:
        raise PermissionError('Mailbox connector source does not match the Core distribution')
    with _LOCK:
        if digest not in _MODULES:
            package_name = '_stella_trusted_email_' + digest
            package = ModuleType(package_name)
            package.__path__ = []
            sys.modules[package_name] = package
            loaded = {}
            try:
                for name in _TRUSTED_MODULES:
                    path = 'email_mcp/' + name + '.py'
                    module = ModuleType(package_name + '.' + name)
                    module.__package__ = package_name
                    module.__file__ = str(root / path)
                    sys.modules[module.__name__] = module
                    loaded[name] = module
                    exec(compile(snapshot[path], module.__file__, 'exec'), module.__dict__)
                    if name == 'schema':
                        sql = snapshot['email_mcp/schema.sql'].decode('utf-8')
                        module.load_schema_sql = lambda: sql
                _MODULES[digest] = loaded['service'].EmailService
            except BaseException:
                for name in [package_name, *[package_name + '.' + name for name in loaded]]:
                    sys.modules.pop(name, None)
                raise
        return _MODULES[digest]


class EmailConnector:
    def __init__(self, config, *, check, cancel_event=None):
        self.config, self.check, self.cancel_event = config, check, cancel_event

    def invoke(self, method, arguments, *, meta=None):
        self.check()
        if method not in OPERATIONS['email']:
            raise PermissionError('Unsupported mailbox operation')
        base = _business(self.config)
        user = current_identity().user_id or 'local-default'
        args = self.config.get('args', [])
        if '--user-id' not in args or args[args.index('--user-id') + 1] != user:
            raise PermissionError('Mailbox user does not match its managed connection')
        try:
            require_owner()
            owner = True
        except PermissionError:
            owner = False
        if method in {'save_account', 'delete_account', 'test_connection'} or (
                method == 'list_folders' and arguments.get('payload') is not None):
            require_owner()
        payload = arguments.get('payload') or {}
        if isinstance(payload, dict) and str(payload.get('password') or '').startswith(REFERENCE):
            raise ValueError('Enter the password through the original mailbox settings')
        scope = (meta or {}).get('resourceScope')
        if scope is not None and (not isinstance(scope, dict) or scope.get('userId') != user or
                                  not isinstance(scope.get('resources'), list)):
            raise PermissionError('Invalid mailbox resource scope')
        store = EmailCredentialStore(self.config.data_root / 'email.db', user_id=user,
                                      expected_revision=payload.get('credential_revision') if isinstance(payload, dict) else None)
        connector = self
        account_id = arguments.get('account_id')

        class Service(base):
            def _load_accounts(self, db=None):
                rows = super()._load_accounts(db)
                if user != 'local-default' and len(rows) == 1 and rows[0]['id'] == 'environment':
                    with self._connect() if db is None else nullcontext(db) as connection:
                        if not connection.execute("SELECT 1 FROM email_accounts WHERE id='environment'").fetchone():
                            return []
                return rows

            def _source(self, settings, **extra):
                source = super()._source(settings, **extra)
                def opener():
                    check_operation()
                    if settings['password'].startswith(REFERENCE):
                        with store._connect() as db:
                            row = store._row(db, account_id)
                        if (any(row[key] != settings[key] for key in ('host', 'port', 'user', 'password'))):
                            raise PermissionError('Mailbox changed; enter credentials for the new endpoint')
                        revision = store._revision(row)
                        def active():
                            check_operation()
                            with store._connect() as db:
                                if store._revision(store._row(db, account_id)) != revision:
                                    raise PermissionError('Mailbox changed during the operation')
                        return store.connect(account_id, check=active, cancel_event=connector.cancel_event)
                    require_owner()
                    return connect_imap(settings['host'], settings['port'], username=settings['user'],
                                        password=settings['password'], check=check_operation,
                                        cancel_event=connector.cancel_event)
                source._opener = opener
                return source

        legacy = next((path for path in self.config.legacy_roots if path.name == 'mailbox.json'), None)
        service = Service(store.path, legacy_mailbox_path=legacy if owner else None,
                          user_id=user, authorized_resources=scope['resources'] if scope else None,
                          credentials=store)
        def check_operation():
            connector.check()
            from core.mcp.authorization import check
            from core.plugins.caller import current_invocation_purpose
            check('email', method, arguments, user,
                  lambda name, values: getattr(service, name)(**values), current_invocation_purpose())
        check_operation()
        migration_failed = set()
        if owner:
            try:
                rows = service._load_accounts()
                if len(rows) == 1 and rows[0]['id'] == 'environment' and rows[0]['password'] and not (
                        method == 'save_account' and payload.get('password')):
                    with store._connect() as db:
                        virtual = db.execute("SELECT 1 FROM email_accounts WHERE id='environment'").fetchone() is None
                    if virtual:
                        # Resolve only this legacy account's declared fields from
                        # the original .env, never cached decrypted credentials.
                        from common.utils import expand_path
                        from core.security import credential_values, env_store
                        path = Path(expand_path('~/.stella/.env'))
                        names = {'IMAP_' + field.upper(): field for field in ('host', 'user', 'password', 'port', 'folder')}
                        declared = {key: value for key, value in env_store._entries(path).items() if key in names}
                        resolved, _ = credential_values.resolve(path, declared, env_store.keychain_for)
                        service.save_account({'id': 'environment', **{names[key]: value for key, value in resolved.items()}})
                store.migrate()
            except OSError:
                if method not in {'list_accounts', 'get_account', 'save_account', 'delete_account'}:
                    raise
                migration_failed = {row['id'] for row in service._load_accounts()
                                    if isinstance(row['password'], str) and not row['password'].startswith(REFERENCE)}
        if method in {'test_connection', 'list_folders'} and account_id is None:
            selected = service._selected(payload, service._load_accounts())
            account_id = selected['id'] if selected else None
        result = getattr(service, method)(**arguments)
        if owner and method in {'list_accounts', 'get_account', 'save_account'}:
            states = {row['id']: row for row in store.inventory()}
            for row in result.get('mailboxes', []) if method == 'list_accounts' else [result]:
                state = states.get(row['id'])
                if state:
                    row.update(credential_status='unavailable' if row['id'] in migration_failed and not
                               state['state'] == 'repair_required' else state['state'],
                               credential_revision=state['revision'])
                else:
                    row.update(credential_status='unavailable' if row['id'] in migration_failed else 'not_configured',
                               credential_revision=None)
        elif not owner and method in {'list_accounts', 'get_account'}:
            for row in result.get('mailboxes', []) if method == 'list_accounts' else [result]:
                row.pop('password_set', None)
                row.pop('password_masked', None)
        check_operation()
        return result

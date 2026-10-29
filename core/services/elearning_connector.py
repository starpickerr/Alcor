"""Trusted adapter over the existing eLearning protocol, settings and login flow."""
from contextvars import copy_context
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
import time
import threading
import urllib.error

from common.runtime_identity import current_identity
from core.mcp.managed import OPERATIONS
from core.security.control import require_owner
from core.security.credential_values import REFERENCE
from core.security.elearning_credentials import ElearningCredentialStore
from core.security.egress import EgressPermit, operation_digest
from core.security.connections import ConnectionControl, service_id
from core.security.http_transport import open_request
from core.security.keychain import KeychainUnavailable
from core.services.trusted_account_modules import load_business


class _Response:
    def __init__(self, response, url):
        self.response, self.url = response, url
        self.headers = response.headers

    def info(self):
        return self.headers

    def geturl(self):
        return self.url

    def read(self, *args):
        return self.response.read(*args)

    def close(self):
        self.response.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class ElearningConnector:
    def __init__(self, config, *, check, cancel_event=None):
        self.config, self.check, self.cancel_event = config, check, cancel_event

    def invoke(self, method, arguments, *, meta=None):
        self.check()
        if method not in OPERATIONS['elearning']:
            raise PermissionError('Unsupported eLearning operation')
        modules = load_business(self.config)
        user = current_identity().user_id or 'local-default'
        args = self.config.get('args', [])
        if '--user-id' not in args or args[args.index('--user-id') + 1] != user:
            raise PermissionError('eLearning user does not match its managed connection')
        try:
            require_owner()
            owner = True
        except PermissionError:
            owner = False
        if method in {'save_settings', 'delete_settings', 'save_file_sync_settings', 'test_connection'}:
            require_owner()
        payload = arguments.get('payload') or {}
        if isinstance(payload, dict) and any(str(payload.get(key) or '').startswith(REFERENCE) for key in ('password', 'token')):
            raise ValueError('Enter credentials through their original settings')
        credentials = ElearningCredentialStore(self.config.data_root, user_id=user)
        connections = ExitStack()
        temporary_sessions = {}
        connector = self
        base_store = modules['store'].ElearningStore

        class Store(base_store):
            def save_settings(self, body):
                with modules['locking'].state_lock(self.root):
                    if 'credential_field' in body:
                        if set(body) != {'credential_field', 'credential_value', 'credential_revision'}:
                            raise ValueError('Credential edits cannot also change account settings')
                        field, value = body['credential_field'], body['credential_value']
                        if field == 'session':
                            if value != '':
                                raise ValueError('Saved login state can only be cleared here')
                            credentials.clear_session(expected_revision=body['credential_revision'])
                        else:
                            credentials.save_field(field, value, expected_revision=body['credential_revision'])
                        return self.public_settings()
                    revision = credentials._revision()
                    cfg = self.resolve_settings(body)
                    credentials.publish_settings(cfg, expected_revision=body.get('credential_revision') or revision)
                    return self.public_settings()

            def save_file_sync_settings(self, root):
                with modules['locking'].state_lock(self.root):
                    revision = credentials._revision()
                    cfg = self.load_settings()
                    cfg['file_sync_root'] = self._directory(root)
                    credentials.publish_settings(cfg, expected_revision=revision)
                    self._record_directory_change(cfg['file_sync_root'])
                    return self.public_settings()

            def load_session(self, cfg):
                account = modules['store'].account_id(cfg)
                if account in temporary_sessions:
                    return temporary_sessions[account]
                if credentials._identity(cfg) != credentials._identity(credentials._read(credentials.path)):
                    raise modules['errors'].LoginRequired('请先配置或测试此账号的登录状态')
                if not credentials.session_path(cfg).exists():
                    raise FileNotFoundError('eLearning has no saved session')
                return credentials.load_session()

            def save_session(self, cfg, data):
                connector.check()
                account = modules['store'].account_id(cfg)
                current = credentials._read(credentials.path)
                if self.login_revision != credentials._session_revision(current):
                    raise ValueError('eLearning settings changed during login')
                temporary_sessions[account] = data
                if credentials._identity(cfg) == credentials._identity(current):
                    credentials.save_session(data, expected_revision=self.login_revision)

            def delete_settings(self):
                require_owner()
                credentials.clear_session(expected_revision=credentials.inventory()['session_revision'])
                credentials.publish_settings(modules['store'].DEFAULTS, expected_revision=credentials._revision())

        store = Store(credentials.root)
        if owner and '--legacy-dir' in args and not store.settings_path.exists():
            legacy = Path(args[args.index('--legacy-dir') + 1]).absolute()
            sources = [legacy / name for name in ('elearning.json', 'elearning-sessions',
                                                   'elearning-courses', 'elearning-file-sync')]
            if not set(sources).issubset(self.config.legacy_roots):
                raise PermissionError('eLearning legacy data must match its Core-owned source')
            for source in sources:
                if (any(path.is_symlink() for path in (source, *source.parents)
                        if str(path) not in {'/var', '/tmp'}) or
                        source.is_dir() and any(path.is_symlink() for path in source.iterdir())):
                    raise PermissionError('eLearning legacy data cannot follow symbolic links')
            store.import_legacy(legacy)
        scope = (meta or {}).get('resourceScope')
        if scope is not None and (scope.get('userId') != user or not isinstance(scope.get('resources'), list)):
            raise PermissionError('Invalid eLearning resource scope')
        service = modules['service'].ElearningService(credentials.root, store=store, user_id=user,
                    authorized_resources=scope['resources'] if scope else None, cancel_event=self.cancel_event)

        def active():
            connector.check()
            if method == 'sync_files':
                output = self.config.output_scope
                if output is None:
                    raise PermissionError('eLearning sync requires its existing output scope')
                output.check()
                if Path(store.load_settings().get('file_sync_root') or '').resolve() != output.root:
                    raise PermissionError('eLearning output directory changed')
            from core.mcp.authorization import check
            from core.plugins.caller import current_invocation_purpose
            check('elearning', method, arguments, user,
                  lambda name, values: getattr(service, name)(**values), current_invocation_purpose())

        def check_output(path):
            from core.security.protected_paths import check_path
            from core.security.service import SecurityService
            active()
            target = Path(path).absolute()
            if not target.is_relative_to(self.config.output_scope.root):
                raise PermissionError('eLearning output is outside the selected directory')
            check_path(target)
            SecurityService.authorize_filesystem(target, operation='write')

        service.file_sync_guard = check_output

        def client_factory(cfg):
            active()
            primary, origin = service_id(cfg['base_url'])
            cancelled = threading.Event()
            lease = connections.enter_context(ConnectionControl().begin(primary, cancel=cancelled.set))
            context = copy_context()
            class Cancellation:
                def is_set(self):
                    context.copy().run(active)
                    lease.check()
                    return cancelled.is_set() or (connector.cancel_event is not None and connector.cancel_event.is_set())
                def wait(self, seconds):
                    deadline = time.monotonic() + seconds
                    while not self.is_set():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            return False
                        cancelled.wait(min(.05, remaining))
                    return True
            cancellation = Cancellation()
            service.cancel_event = cancellation
            def resolve_field(field):
                value = cfg.get(field, '')
                if not isinstance(value, str):
                    raise KeychainUnavailable('Repair this eLearning credential in its original settings')
                if value.startswith(REFERENCE):
                    return credentials.resolve(field)
                if value and not (owner and method == 'test_connection' and payload.get(field) == value):
                    raise KeychainUnavailable('Migrate this eLearning credential through its original settings')
                return value
            token = cfg.get('token', '')
            material = {**cfg, 'token': resolve_field('token')}
            binding = {key: cfg.get(key) for key in ('base_url', 'username', 'password', 'token')}

            def verify():
                def current():
                    active()
                    lease.check()
                    if self.cancel_event is not None and self.cancel_event.is_set():
                        raise PermissionError('eLearning operation was cancelled')
                    current = store.load_settings()
                    for key in (('token',) if token else ()):
                        value = binding.get(key)
                        if isinstance(value, str) and value.startswith(REFERENCE):
                            if (credentials._identity(current) != credentials._identity(cfg) or current.get(key) != value or
                                    credentials._state(credentials.path, credentials._name(cfg, key), value) != 'configured'):
                                raise PermissionError('eLearning credential changed or is unavailable')
                context.copy().run(current)

            def opener_factory(jar, *, from_browser=False):
                session_path = credentials.session_path(cfg)
                temporary = (modules['store'].account_id(cfg) in temporary_sessions and
                             credentials._identity(cfg) != credentials._identity(store.load_settings()))
                session_reference = (credentials._read(session_path).get('credential')
                                     if not token and not from_browser else None)
                def authenticated():
                    verify()
                    if not token and not from_browser:
                        def session_current():
                            current = credentials._read(credentials.path)
                            if temporary:
                                if credentials._session_revision(current) != store.login_revision:
                                    raise PermissionError('eLearning settings changed during draft login')
                                return
                            if (credentials._identity(current) != credentials._identity(cfg) or
                                    credentials._read(session_path).get('credential') != session_reference or
                                    credentials._state(session_path, credentials._name(cfg, 'session'),
                                                       session_reference) != 'configured'):
                                raise PermissionError('eLearning login changed or is unavailable')
                        context.copy().run(session_current)
                class Opener:
                    def open(self, request, timeout=30):
                        authenticated()
                        url = request.full_url
                        receiver, target = service_id(url)
                        api = request.get_header('Accept') == 'application/json'
                        if request.get_method() != 'GET' or request.data is not None or not url.startswith('https://') or api and receiver != primary:
                            raise PermissionError('Unsupported eLearning protocol request')
                        jar.add_cookie_header(request)
                        headers = {key: value for key, value in request.header_items()
                                   if receiver == primary or key.lower() not in {'authorization', 'cookie'}}
                        permit = EgressPermit(authenticated, time.monotonic() + timeout, sha256(url.encode()).hexdigest(),
                                              sha256(b'').hexdigest(), operation_digest('GET', headers, None))
                        response = open_request('GET', url, headers=headers, timeout=timeout, permit=permit,
                            configured_origin=origin, allow_configured_local=target == origin,
                            cancel_event=cancellation,
                            related_services=(primary,), maximum=512 * 1024 * 1024)
                        wrapped = _Response(response, url)
                        if not 200 <= response.status < 300:
                            status = response.status
                            response.close()
                            raise urllib.error.HTTPError(url, status, 'eLearning protocol request failed', {}, None)
                        jar.extract_cookies(wrapped, request)
                        return wrapped
                return Opener()

            def setup_context(browser_context):
                allowed = {origin, service_id('https://id.fudan.edu.cn')[1]}
                def route(request_route):
                    verify()
                    try:
                        permitted = service_id(request_route.request.url)[1] in allowed
                    except ValueError:
                        permitted = False
                    request_route.continue_() if permitted else request_route.abort()
                browser_context.route('**/*', route)

            def login_config():
                verify()
                store.login_revision = credentials._session_revision(credentials._read(credentials.path))
                return {**material, 'password': resolve_field('password')}

            return modules['client'].Canvas(material, store, cancel_event=cancellation,
                       opener_factory=opener_factory, context_setup=setup_context, login_config=login_config)

        service.client_factory = client_factory
        active()
        if owner and not (method == 'save_settings' and 'credential_field' in payload):
            for migrate in (credentials.migrate_settings, credentials.migrate_session):
                try:
                    migrate()
                except (OSError, ValueError):
                    if method not in {'get_settings', 'save_settings', 'delete_settings', 'save_file_sync_settings'}:
                        raise
        with connections:
            result = getattr(service, method)(**arguments)
            active()
        if isinstance(result, dict) and method in {'get_settings', 'save_settings', 'save_file_sync_settings', 'delete_settings'}:
            if owner:
                view = credentials.inventory()
                result.update(credential_states={field['key']: field['state'] for field in view['fields']},
                              credential_revision=view['revision'], session_revision=view['session_revision'])
            else:
                result.pop('password_set', None)
                result.pop('password_masked', None)
        return result

"""Trusted Canvas read transport, bound to one account's current authentication."""
from contextvars import copy_context
from hashlib import sha256
import http.cookiejar
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import Request

from core.security.connections import ConnectionControl, service_id
from core.security.credential_values import REFERENCE
from core.security.egress import EgressPermit, operation_digest
from core.security.http_transport import open_request
from core.security.keychain import KeychainUnavailable
from core.security.protected_paths import current_revision, check_context_revision
from core.security.service import SecurityService


class ElearningHTTPError(PermissionError):
    def __init__(self, status):
        self.status = status
        super().__init__(f'eLearning endpoint returned HTTP {status}; check the original account settings')


class ElearningTransport:
    def __init__(self, store, *, check, cancel_event=None):
        self.store, self.check, self.cancel_event = store, check, cancel_event
        with store._lock():
            config = store._read(store.path)
            self.identity = store._identity(config)
            self.base = self.identity[0]
            self.primary, self.origin = service_id(self.base)
            if urlsplit(self.base).scheme != 'https':
                raise PermissionError('eLearning authentication requires HTTPS')
            self.mode = 'token' if config.get('token') else 'session'
            self.reference = self._credential(config)
        self.revision = current_revision()
        self.policy = SecurityService.policy_binding()
        self.context = copy_context()

    def _credential(self, config):
        if self.store._identity(config) != self.identity:
            raise PermissionError('eLearning account changed; old requests cannot resume')
        if self.mode == 'token':
            value, path = config.get('token'), self.store.path
        else:
            path = self.store.session_path(config)
            value = self.store._read(path).get('credential')
        if not isinstance(value, str) or not value.startswith(REFERENCE):
            raise KeychainUnavailable('eLearning authentication needs repair or login in its original settings')
        return path, self.store._name(config, self.mode), value

    def _verify(self):
        from core.plugins.caller import remaining_invocation_seconds
        def active():
            remaining_invocation_seconds()
            SecurityService.check_execution()
            check_context_revision(self.revision)
            if SecurityService.policy_binding() != self.policy:
                raise PermissionError('eLearning execution authorization changed')
            self.check()
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise PermissionError('eLearning operation was cancelled')
            with self.store._lock():
                current = self._credential(self.store._read(self.store.path))
                if current != self.reference:
                    raise PermissionError('eLearning authentication changed; start a fresh request')
                if self.store._state(*current) != 'configured':
                    raise KeychainUnavailable('eLearning authentication is no longer available')
        self.context.copy().run(active)

    def _headers(self, url, *, api, authenticated):
        headers = {'Accept': 'application/json' if api else '*/*', 'User-Agent': 'Stella-eLearning/1.0'}
        if not authenticated:
            return headers
        if self.mode == 'token':
            token = self.store.resolve('token')
            if not token or token.startswith(REFERENCE):
                raise KeychainUnavailable('eLearning token is unavailable')
            headers['Authorization'] = 'Bearer ' + token
            return headers
        jar = http.cookiejar.CookieJar()
        host = urlsplit(self.base).hostname
        for item in self.store.load_session()['cookies']:
            domain = item['domain'].lower()
            if domain.lstrip('.') != host and not (domain.startswith('.') and host.endswith(domain)):
                continue
            expiry = float(item.get('expires', -1))
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=item['name'], value=item['value'], port=None, port_specified=False,
                domain=domain, domain_specified=domain.startswith('.'), domain_initial_dot=domain.startswith('.'),
                path=item.get('path', '/'), path_specified=True, secure=item.get('secure', True),
                expires=int(expiry) if expiry >= 0 else None, discard=expiry < 0,
                comment=None, comment_url=None, rest={}, rfc2109=False))
        jar.clear_expired_cookies()
        request = Request(url)
        jar.add_cookie_header(request)
        if not request.has_header('Cookie'):
            raise KeychainUnavailable('eLearning session expired or has no cookie for this service')
        headers['Cookie'] = request.get_header('Cookie')
        return headers

    def open(self, url, *, api=True, timeout=30):
        """Only reads; authenticated API calls cannot change receiving service."""
        from core.plugins.caller import remaining_invocation_seconds
        url = urljoin(self.base + '/', url)
        receiver, origin = service_id(url)
        if urlsplit(url).scheme != 'https' or api and receiver != self.primary:
            raise PermissionError('eLearning API receiver must match its configured HTTPS service')
        # Check intent before touching the account's secret values.
        with ConnectionControl().begin(receiver):
            pass
        self._verify()
        headers = self._headers(url, api=api, authenticated=receiver == self.primary)
        remaining = remaining_invocation_seconds()
        if remaining is not None:
            timeout = min(timeout, remaining)
        permit = EgressPermit(self._verify, time.monotonic() + timeout, sha256(url.encode()).hexdigest(),
                              sha256(b'').hexdigest(), operation_digest('GET', headers, None))
        # This explicit auth connector emits only protocol metadata and bound
        # authentication. It is not an ordinary-content egress bypass.
        response = open_request('GET', url, headers=headers, timeout=timeout, permit=permit,
                                configured_origin=self.origin, related_services=(self.primary,),
                                cancel_event=self.cancel_event, allow_configured_local=origin == self.origin,
                                maximum=128 * 1024 * 1024 if api else 512 * 1024 * 1024)
        if not 200 <= response.status < 300:
            status = response.status
            response.close()
            raise ElearningHTTPError(status)
        kind = response.headers.get('Content-Type', '').lower()
        if (api and 'json' not in kind or not api and any(value in kind for value in ('text/html', 'application/json'))
                and not response.headers.get('Content-Disposition', '').lower().startswith('attachment')):
            response.close()
            raise ElearningHTTPError(401)
        from agent.permission.audit import record
        record(tool='elearning', operation='read', resource=origin, risk='L2', decision='allow',
               reason='Bound eLearning protocol read admitted through controlled transport.')
        return response

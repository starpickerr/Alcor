"""
MCP OAuth 2.1 client (authorization code + PKCE) with zero external deps.

Implements the subset of the MCP authorization spec needed to connect to
remote MCP servers that guard their endpoint behind OAuth (e.g. Xmind):

  1. Metadata discovery via RFC 9728 (protected-resource) + RFC 8414
     (authorization-server) .well-known documents.
  2. Dynamic Client Registration (RFC 7591) to obtain a client_id.
  3. PKCE (RFC 7636, S256) authorization-code flow.
  4. Token exchange + refresh, persisted to ~/.stella/mcp_oauth.json.

The actual browser round-trip is completed out-of-band: McpClient generates
an authorization URL, the user opens it, and the web console callback
(/mcp/oauth/callback) feeds the returned code back into finish_authorization().
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
from copy import deepcopy
from contextvars import copy_context
from pathlib import Path
from typing import Optional

from common.log import logger


# ------------------------------------------------------------------
# Token store: ~/.stella/mcp_oauth.json  {server_name: {...credentials...}}
# ------------------------------------------------------------------

_STORE_LOCK = threading.Lock()
_SECRET_FIELDS = ('client_secret', 'access_token', 'refresh_token')


class _CredentialRecord(dict):
    def __init__(self, values, original):
        super().__init__(values)
        self.original = deepcopy(original)


def _credential_locations(store):
    if not isinstance(store, dict):
        raise ValueError('OAuth store must be an object; existing data retained')
    result = {}
    for server, record in store.items():
        if not isinstance(server, str) or not server or not isinstance(record, dict):
            raise ValueError('Invalid OAuth account record; existing data retained')
        resource = record.get('resource_url', '')
        if not isinstance(resource, str):
            raise ValueError('Invalid OAuth resource identity')
        if not isinstance(record.get('metadata', {}), dict):
            raise ValueError('Invalid OAuth endpoint metadata; existing data retained')
        for field in _SECRET_FIELDS:
            value = record.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError('Invalid OAuth credential field; existing data retained')
                name = json.dumps((server, resource, field), separators=(',', ':'))
                result[name] = record, field
    return result


def _values(locations):
    return {name: row[field] for name, (row, field) in locations.items()}


def _store_path() -> str:
    base = os.path.expanduser("~/.stella")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return os.path.join(base, "mcp_oauth.json")


def _load_store() -> dict:
    path = _store_path()
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    _credential_locations(data)
    return data


def _save_store(store: dict) -> dict:
    # Callers hold the store lock across their read, change and publication.
    from core.security import credential_values, env_store
    from core.security.storage import private_atomic_write
    path = _store_path()
    candidate = deepcopy(store)
    locations = _credential_locations(candidate)
    previous = _load_store()
    before = _values(_credential_locations(previous))
    for server, row in candidate.items():
        old = previous.get(server, {})
        if (old.get('metadata', {}).get('token_endpoint') != row.get('metadata', {}).get('token_endpoint') and
                any(old.get(field) and row.get(field) for field in ('client_secret', 'refresh_token'))):
            raise ValueError('OAuth authentication endpoint changed; remove the old authorization before registering again')
    def commit(saved):
        for name, value in saved.items():
            row, field = locations[name]
            row[field] = value
        if candidate != previous:
            private_atomic_write(path, json.dumps(candidate, ensure_ascii=False, indent=2))
    credential_values.publish(path, _values(locations), before, env_store.keychain_for, commit)
    return candidate


def _credential_status(name, record):
    from core.security import credential_values, env_store
    locations = _credential_locations({name: record})
    fields = _values(locations)
    states = credential_values.statuses(_store_path(), fields, env_store.keychain_for)
    unavailable = any(state is None for state in states.values())
    repair = any(not states[key] and value.startswith(credential_values.REFERENCE) for key, value in fields.items())
    configured = any(states[key] for key, (_, field) in locations.items() if field in {'access_token', 'refresh_token'})
    reset = any(field == 'client_secret' and row[field] and states[key] is not True
                for key, (row, field) in locations.items())
    return dict(server=name, configured=None if unavailable else bool(configured and not repair),
                state='unavailable' if unavailable else 'repair-required' if repair else 'configured' if configured else 'not-configured',
                requires_reset=reset, reason='Credential storage unavailable; unlock it and refresh, or remove this authorization.' if unavailable else
                'Client credential is missing; remove this authorization before authorizing again.' if reset else
                'Saved OAuth credential is missing; authorize again or remove it.' if repair else '')


def credential_statuses():
    """Owner-reported status for the fixed token store; never return records."""
    from core.security.control import require_owner
    require_owner()
    with _STORE_LOCK:
        return [_credential_status(name, record) for name, record in _load_store().items() if not record.get('_deleted')]


def authorization_status(server_name, resource_url=None):
    from core.security.control import require_owner
    require_owner()
    with _STORE_LOCK:
        record = _load_store().get(server_name, {})
        saved = bool(record) and not record.get('_deleted')
        result = _credential_status(server_name, record if saved else {})
        if saved and resource_url is not None and record.get('resource_url') != resource_url:
            result.update(state='repair-required', requires_reset=True,
                          reason='The service address changed; remove the old authorization before authorizing again.')
        return dict(result, saved=bool(saved))


def load_server_record(server_name: str, *, resource_url=None) -> dict:
    with _STORE_LOCK:
        from core.security import credential_values, env_store
        original = _load_store().get(server_name, {})
        if original.get('_deleted') or resource_url is not None and original.get('resource_url') != resource_url:
            return _CredentialRecord({}, original)
        result = deepcopy(original)
        fields = _credential_locations({server_name: result})
        values, _ = credential_values.resolve(_store_path(), _values(fields), env_store.keychain_for, strict=False)
        for name, value in values.items():
            row, field = fields[name]
            row[field] = value
        return _CredentialRecord(result, original)


def save_server_record(server_name: str, record: dict, *, expected=None) -> dict:
    from core.plugins.locks import package_lock
    with _STORE_LOCK, package_lock(Path(_store_path()).parent, 'oauth-credentials', exclusive=True):
        store = _load_store()
        if expected is not None and store.get(server_name, {}) != expected:
            raise ValueError('OAuth account changed; reload before saving')
        store[server_name] = record
        return _save_store(store)[server_name]


def clear_server_record(server_name: str) -> None:
    from core.security.control import require_owner
    require_owner()
    from core.plugins.locks import package_lock
    with _STORE_LOCK, package_lock(Path(_store_path()).parent, 'oauth-credentials', exclusive=True):
        store = _load_store()
        # A tombstone prevents a previously empty pending registration from
        # recreating credentials after an Owner removal (the ABA case).
        store[server_name] = {'_deleted': True, '_revision': secrets.token_hex(16)}
        _save_store(store)
    cancel_pending(server_name=server_name)


# ------------------------------------------------------------------
# Pending authorizations, keyed by the OAuth `state` param.
# Populated when an authorization URL is generated; consumed by the
# web callback when the browser redirects back with ?code&state.
# ------------------------------------------------------------------

_PENDING_LOCK = threading.Lock()
_PENDING: dict = {}  # state -> {"handler": OAuthHandler, "created": ts}
_PENDING_TTL = 600  # seconds


def _register_pending(state: str, handler: "OAuthHandler") -> None:
    with _PENDING_LOCK:
        _prune_pending_locked()
        _PENDING[state] = {"handler": handler, "created": time.time()}


def _prune_pending_locked() -> None:
    now = time.time()
    stale = [s for s, v in _PENDING.items() if now - v["created"] > _PENDING_TTL]
    for s in stale:
        _PENDING.pop(s, None)


def pop_pending(state: str) -> Optional["OAuthHandler"]:
    with _PENDING_LOCK:
        _prune_pending_locked()
        entry = _PENDING.pop(state, None)
        if entry:
            entry['handler']._authorization_in_progress = True
    return entry["handler"] if entry else None


def has_pending() -> bool:
    with _PENDING_LOCK:
        _prune_pending_locked()
        return bool(_PENDING)


def cancel_pending(*, server_name=None, handler=None):
    with _PENDING_LOCK:
        for state, entry in list(_PENDING.items()):
            target = entry['handler']
            if target is handler or server_name is not None and target.server_name == server_name:
                target._authorization_cancelled = True
                target._authorization_in_progress = False
                _PENDING.pop(state, None)
        if handler is not None:
            handler._authorization_cancelled = True
            handler._authorization_in_progress = False


def pending_authorization(handler):
    from core.security.control import require_owner
    require_owner()
    with _PENDING_LOCK:
        _prune_pending_locked()
        present = any(entry['handler'] is handler for entry in _PENDING.values())
    if not present and not handler._authorization_in_progress:
        return None
    handler._check_authorization_state()
    return dict(authorization_url=handler._authorization_url if present else None,
                expires_at=handler._authorization_expires, authorizing=handler._authorization_in_progress)


# ------------------------------------------------------------------
# Controlled OAuth protocol transport
# ------------------------------------------------------------------

_UA = "Stella-MCP-OAuth/1.0"


def _oauth_request(url, *, resource_url, body=None, content_type=None, timeout=20, check_active=None):
    from core.security.connections import service_id
    from core.security.egress import EgressPermit, operation_digest
    from core.security.http_transport import open_request
    from core.security.protected_paths import current_revision, check_context_revision
    from core.security.service import SecurityService
    from core.plugins.caller import remaining_invocation_seconds
    from agent.permission.audit import record
    resource, resource_origin = service_id(resource_url)
    _, receiver = service_id(url)
    local_binding = receiver == resource_origin
    if body is not None and not url.startswith('https://'):
        import ipaddress
        try:
            private = ipaddress.ip_address(urllib.parse.urlsplit(url).hostname).is_private
        except ValueError:
            private = False
        if not local_binding or not private:
            raise PermissionError('OAuth credentials require HTTPS or the exact declared numeric local service')
    method = 'GET' if body is None else 'POST'
    headers = {'Accept': 'application/json', 'User-Agent': _UA}
    if content_type:
        headers['Content-Type'] = content_type
    revision, binding = current_revision(), SecurityService.policy_binding()
    remaining = remaining_invocation_seconds()
    if remaining is not None:
        timeout = min(timeout, remaining)
    context = copy_context()
    def verify():
        def current():
            remaining_invocation_seconds()
            SecurityService.check_execution()
            check_context_revision(revision)
            if SecurityService.policy_binding() != binding:
                raise PermissionError('OAuth execution authorization changed')
            if check_active:
                check_active()
        context.copy().run(current)
    # This narrow trusted connector sends only OAuth protocol fields to its
    # declared authentication endpoint. Ordinary content retains egress checks.
    permit = EgressPermit(verify, time.monotonic() + timeout,
        hashlib.sha256(url.encode()).hexdigest(), hashlib.sha256(body or b'').hexdigest(),
        operation_digest(method, headers, None))
    with open_request(method, url, body=body, headers=headers, timeout=timeout, maximum=2 * 1024 * 1024,
                      permit=permit, configured_origin=resource_origin, related_services=(resource,),
                      allow_configured_local=local_binding) as response:
        if not 200 <= response.status < 300:
            raise PermissionError('OAuth endpoint rejected the protocol request')
        result = json.loads(response.read().decode('utf-8') or '{}')
        if not isinstance(result, dict):
            raise ValueError('OAuth endpoint returned invalid metadata')
    record(tool='mcp_oauth', operation='authenticate' if body is not None else 'discovery',
           resource=receiver, risk='L4' if body is not None else 'L2', decision='allow',
           reason='Bound OAuth protocol request completed.')
    return result


def _http_get_json(url: str, timeout: int = 15, *, resource_url, check_active=None) -> Optional[dict]:
    try:
        return _oauth_request(url, resource_url=resource_url, timeout=timeout, check_active=check_active)
    except (OSError, ValueError, PermissionError):
        logger.debug('[MCP-OAuth] Metadata request unavailable or refused')
        return None


def _http_post_form(url: str, fields: dict, timeout: int = 20, *, resource_url, check_active=None) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    return _oauth_request(url, resource_url=resource_url, body=body, timeout=timeout,
                          content_type='application/x-www-form-urlencoded', check_active=check_active)


def _http_post_json(url: str, payload: dict, timeout: int = 20, *, resource_url, check_active=None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    return _oauth_request(url, resource_url=resource_url, body=body, timeout=timeout,
                          content_type='application/json', check_active=check_active)


# ------------------------------------------------------------------
# Discovery (RFC 9728 + RFC 8414)
# ------------------------------------------------------------------

def _origin(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def discover_metadata(resource_url: str, www_authenticate: str = "", *, check_active=None) -> Optional[dict]:
    """
    Resolve the authorization server metadata for a protected MCP resource.

    Returns a dict with at least authorization_endpoint + token_endpoint,
    plus registration_endpoint when the server supports DCR. Returns None
    when discovery fails.
    """
    as_metadata_url = _parse_resource_metadata_url(www_authenticate)

    # 1) Protected-resource metadata (RFC 9728) to locate the auth server.
    auth_server = None
    prm = None
    if as_metadata_url:
        prm = _http_get_json(as_metadata_url, resource_url=resource_url, check_active=check_active)
    if prm is None:
        origin = _origin(resource_url)
        prm = _http_get_json(f"{origin}/.well-known/oauth-protected-resource", resource_url=resource_url, check_active=check_active)
    if prm and isinstance(prm.get("authorization_servers"), list) and prm["authorization_servers"]:
        auth_server = prm["authorization_servers"][0]

    # 2) Authorization-server metadata (RFC 8414). Fall back to the resource
    #    origin when the resource did not advertise a separate auth server.
    base = auth_server or _origin(resource_url)
    asm = _fetch_as_metadata(base, resource_url=resource_url, check_active=check_active)
    if not asm:
        return None

    if not asm.get("authorization_endpoint") or not asm.get("token_endpoint"):
        logger.warning("[MCP-OAuth] Authorization server metadata missing required endpoints")
        return None

    # Derive the scope to request. Prefer the resource's required_scopes
    # (RFC 9728), then its scopes_supported, then the auth server's
    # scopes_supported. Stored so callers don't have to configure it.
    discovered_scope = ""
    if prm:
        scopes = prm.get("required_scopes") or prm.get("scopes_supported")
        if isinstance(scopes, list) and scopes:
            discovered_scope = " ".join(str(s) for s in scopes)
    if not discovered_scope and isinstance(asm.get("scopes_supported"), list) and asm["scopes_supported"]:
        discovered_scope = " ".join(str(s) for s in asm["scopes_supported"])
    if discovered_scope:
        asm["_discovered_scope"] = discovered_scope
    return asm


def _parse_resource_metadata_url(www_authenticate: str) -> Optional[str]:
    """Extract resource_metadata="..." from a WWW-Authenticate: Bearer header."""
    if not www_authenticate:
        return None
    # naive but sufficient parse for `resource_metadata="URL"`
    marker = "resource_metadata="
    idx = www_authenticate.find(marker)
    if idx < 0:
        return None
    rest = www_authenticate[idx + len(marker):].strip()
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end > 0 else None
    # unquoted, up to comma/space
    for sep in (",", " "):
        if sep in rest:
            rest = rest.split(sep, 1)[0]
    return rest or None


def _fetch_as_metadata(base: str, *, resource_url, check_active=None) -> Optional[dict]:
    """Try both RFC 8414 and OIDC well-known locations."""
    base = base.rstrip("/")
    candidates = [
        f"{base}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration",
    ]
    for url in candidates:
        data = _http_get_json(url, resource_url=resource_url, check_active=check_active)
        if data and data.get("authorization_endpoint"):
            return data
    return None


# ------------------------------------------------------------------
# PKCE
# ------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _make_pkce() -> tuple:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ------------------------------------------------------------------
# OAuthHandler: per-server OAuth state machine
# ------------------------------------------------------------------

class OAuthHandler:
    """Drives the OAuth flow and token lifecycle for a single MCP server."""

    def __init__(self, server_name: str, resource_url: str, redirect_uri: str,
                 scope: str = "", client_name: str = "Stella", on_authorized=None, check_active=None,
                 check_authorization=None, publish=None):
        self.server_name = server_name
        self.resource_url = resource_url
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.client_name = client_name
        self.on_authorized = on_authorized
        self._check_active = check_active
        self._check_authorization = check_authorization or check_active
        self._publish = publish
        self._authorization_cancelled = False
        self._authorization_in_progress = False
        self._authorization_expires = None
        self._authorization_url = None

        self._reload()
        self._verifier: Optional[str] = None

    def _reload(self):
        rec = load_server_record(self.server_name, resource_url=self.resource_url)
        self._record = rec
        self.metadata: dict = rec.get("metadata", {})
        self.client_id: Optional[str] = rec.get("client_id")
        self.client_secret: Optional[str] = rec.get("client_secret")
        self.access_token: Optional[str] = rec.get("access_token")
        self.refresh_token: Optional[str] = rec.get("refresh_token")
        self.expires_at: float = float(rec.get("expires_at", 0) or 0)

    # --- persistence -------------------------------------------------

    def notify_authorized(self) -> None:
        """Reload only the originating runtime generation, if it is still enabled."""
        if self.on_authorized is not None:
            try:
                self.on_authorized()
            except Exception:
                logger.warning('[MCP-OAuth] Failed to reload authorized server')

    def _persist(self, **changes) -> None:
        candidate = {
            "resource_url": self.resource_url,
            "metadata": self.metadata,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }
        candidate.update(changes)
        saved = deepcopy(candidate)
        for field in _SECRET_FIELDS:
            if (self._record.original.get('resource_url') == self.resource_url and
                    candidate[field] == self._record.get(field)):
                saved[field] = self._record.original.get(field)
        def publish():
            self._check_authorization_state()
            original = save_server_record(self.server_name, saved, expected=self._record.original)
            for field in candidate:
                setattr(self, field, candidate[field])
            self._record = _CredentialRecord(candidate, original)
        (self._publish or (lambda operation: operation()))(publish)

    def _check_authorization_state(self):
        if self._authorization_cancelled or (self._authorization_expires is not None and time.time() >= self._authorization_expires):
            raise PermissionError('OAuth authorization was cancelled or expired')
        if self._check_authorization:
            self._check_authorization()

    # --- token access ------------------------------------------------

    def get_valid_access_token(self, leeway: int = 60) -> Optional[str]:
        """Return a usable access token, refreshing proactively when near expiry."""
        self._reload()
        if not self.access_token:
            return None
        if self.expires_at and time.time() >= self.expires_at - leeway:
            if not self.refresh():
                return None
        return self.access_token

    def refresh(self) -> bool:
        """Refresh the access token using the stored refresh token."""
        self._reload()
        if not self.refresh_token or not self.metadata.get("token_endpoint"):
            return False
        fields = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": self.client_id or "",
        }
        if self.client_secret:
            fields["client_secret"] = self.client_secret
        try:
            resp = _http_post_form(self.metadata["token_endpoint"], fields, resource_url=self.resource_url,
                                   check_active=self._authentication_guard('token_endpoint', ('refresh_token', 'client_secret')))
        except Exception as e:
            logger.warning(f"[MCP-OAuth:{self.server_name}] refresh failed: {e}")
            return False
        return self._absorb_token_response(resp)

    # --- authorization-code flow ------------------------------------

    def ensure_registered(self, www_authenticate: str = "") -> bool:
        """Discover metadata + register a client if not already done."""
        if (self._record.original and not self._record.original.get('_deleted') and
                self._record.original.get('resource_url') != self.resource_url):
            return False
        if not self.metadata.get("authorization_endpoint"):
            meta = discover_metadata(self.resource_url, www_authenticate, check_active=self._check_active)
            if not meta:
                return False
            self.metadata = meta
        # Adopt the scope discovered from metadata when the user didn't set one.
        if not self.scope and self.metadata.get("_discovered_scope"):
            self.scope = self.metadata["_discovered_scope"]
            logger.info(f"[MCP-OAuth:{self.server_name}] Using discovered scope: {self.scope}")
        if not self.client_id:
            if any(self._record.original.get(field) for field in _SECRET_FIELDS):
                return False  # Existing unidentified credentials require Owner removal, not reassignment.
            return self._register_client()
        self._persist()
        return True

    def _register_client(self) -> bool:
        reg_endpoint = self.metadata.get("registration_endpoint")
        if not reg_endpoint:
            logger.warning(
                f"[MCP-OAuth:{self.server_name}] No registration_endpoint; "
                f"DCR unavailable. Provide client_id manually."
            )
            return False
        payload = {
            "client_name": self.client_name,
            "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if self.scope:
            payload["scope"] = self.scope
        try:
            resp = _http_post_json(reg_endpoint, payload, resource_url=self.resource_url,
                                   check_active=self._authentication_guard('registration_endpoint'))
        except Exception as e:
            logger.warning(f"[MCP-OAuth:{self.server_name}] DCR failed: {e}")
            return False
        client_id = resp.get("client_id")
        if not client_id:
            logger.warning(f"[MCP-OAuth:{self.server_name}] DCR returned no client_id")
            return False
        from core.security.credential_values import REFERENCE
        secret = resp.get('client_secret')
        if not isinstance(client_id, str) or (secret is not None and
                (not isinstance(secret, str) or secret.startswith(REFERENCE))):
            return False
        try:
            self._persist(client_id=client_id, client_secret=secret)
        except (OSError, PermissionError, ValueError):
            logger.warning(f"[MCP-OAuth:{self.server_name}] Client storage failed; previous credentials retained")
            return False
        logger.info(f"[MCP-OAuth:{self.server_name}] Registered client_id={client_id}")
        return True

    def build_authorization_url(self) -> Optional[str]:
        """Create an authorization URL and register this handler as pending."""
        if not self.metadata.get("authorization_endpoint") or not self.client_id:
            return None
        from core.security.connections import service_id
        service_id(self.metadata['authorization_endpoint'])
        self._verifier, challenge = _make_pkce()
        state = secrets.token_urlsafe(24)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if self.scope:
            params["scope"] = self.scope
        # Advertise the resource we intend to access (RFC 8707).
        params["resource"] = self.resource_url
        _register_pending(state, self)
        self._authorization_expires = time.time() + _PENDING_TTL
        self._authorization_url = f"{self.metadata['authorization_endpoint']}?{urllib.parse.urlencode(params)}"
        return self._authorization_url

    def finish_authorization(self, code: str) -> bool:
        """Exchange an authorization code for tokens."""
        if not self.metadata.get("token_endpoint") or not self._verifier:
            return False
        fields = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id or "",
            "code_verifier": self._verifier,
            "resource": self.resource_url,
        }
        if self.client_secret:
            fields["client_secret"] = self.client_secret
        try:
            resp = _http_post_form(self.metadata["token_endpoint"], fields, resource_url=self.resource_url,
                                   check_active=self._authentication_guard('token_endpoint', ('client_secret',), authorization=True))
        except Exception:
            logger.warning(f"[MCP-OAuth:{self.server_name}] token exchange failed")
            return False
        ok = self._absorb_token_response(resp)
        self._verifier = None
        if ok:
            self._authorization_expires = None
            self._authorization_url = None
        return ok

    def _absorb_token_response(self, resp: dict) -> bool:
        from core.security.credential_values import REFERENCE
        access = resp.get("access_token")
        refresh = resp.get('refresh_token')
        if (not isinstance(access, str) or not access or access.startswith(REFERENCE) or
                refresh is not None and (not isinstance(refresh, str) or refresh.startswith(REFERENCE))):
            logger.warning(f"[MCP-OAuth:{self.server_name}] token response missing access_token")
            return False
        expires_in = resp.get("expires_in")
        try:
            self._persist(access_token=access, refresh_token=resp.get('refresh_token') or self.refresh_token,
                          expires_at=time.time() + int(expires_in) if expires_in else 0)
        except (OSError, PermissionError, ValueError):
            logger.warning(f"[MCP-OAuth:{self.server_name}] Token storage failed; previous credentials retained")
            return False
        logger.info(f"[MCP-OAuth:{self.server_name}] Access token stored")
        return True

    def _authentication_guard(self, role, fields=(), *, authorization=False):
        original, endpoint = deepcopy(self._record.original), self.metadata[role]
        def check():
            active = self._check_authorization if authorization else self._check_active
            if authorization:
                self._check_authorization_state()
            if active:
                active()
            with _STORE_LOCK:
                if _load_store().get(self.server_name, {}) != original or self.metadata.get(role) != endpoint:
                    raise PermissionError('OAuth account or authentication endpoint changed')
                if fields and original.get('metadata', {}).get(role) != endpoint:
                    raise PermissionError('OAuth credentials are not bound to this authentication endpoint')
            from core.security import credential_values, env_store
            locations = _credential_locations({self.server_name: original})
            values = {name: row[field] for name, (row, field) in locations.items() if field in fields and row[field]}
            if not all(credential_values.statuses(_store_path(), values, env_store.keychain_for).values()):
                raise PermissionError('OAuth authentication credential is unavailable; repair it through MCP settings')
        return check

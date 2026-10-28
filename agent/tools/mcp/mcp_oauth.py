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
import urllib.request
import urllib.error
from copy import deepcopy
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
    before = _values(_credential_locations(_load_store()))
    def commit(saved):
        for name, value in saved.items():
            row, field = locations[name]
            row[field] = value
        private_atomic_write(path, json.dumps(candidate, ensure_ascii=False, indent=2))
    credential_values.publish(path, _values(locations), before, env_store.keychain_for, commit)
    return candidate


def credential_statuses():
    """Owner-reported status for the fixed token store; never return records."""
    from core.security.control import require_owner
    require_owner()
    with _STORE_LOCK:
        from core.security import credential_values, env_store
        result = []
        for name, record in _load_store().items():
            fields = _values(_credential_locations({name: record}))
            states = credential_values.statuses(_store_path(), fields, env_store.keychain_for)
            configured = None if any(state is None for state in states.values()) else any(
                states[key] for key, (_, field) in _credential_locations({name: record}).items()
                if field in {'access_token', 'refresh_token'})
            repair = any(not states[key] and value.startswith(credential_values.REFERENCE) for key, value in fields.items())
            result.append(dict(server=name, configured=configured,
                reason='Credential storage unavailable; repair or remove through MCP settings.' if configured is None else
                       'Saved OAuth credential is missing or invalid; authorize again or remove it.' if repair else ''))
        return result


def load_server_record(server_name: str) -> dict:
    with _STORE_LOCK:
        from core.security import credential_values, env_store
        original = _load_store().get(server_name, {})
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
    from core.plugins.locks import package_lock
    with _STORE_LOCK, package_lock(Path(_store_path()).parent, 'oauth-credentials', exclusive=True):
        store = _load_store()
        if server_name in store:
            store.pop(server_name, None)
            _save_store(store)


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
    return entry["handler"] if entry else None


def has_pending() -> bool:
    with _PENDING_LOCK:
        _prune_pending_locked()
        return bool(_PENDING)


# ------------------------------------------------------------------
# HTTP helpers (stdlib only)
# ------------------------------------------------------------------

_UA = "Stella-MCP-OAuth/1.0"


def _http_get_json(url: str, timeout: int = 15) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        logger.debug(f"[MCP-OAuth] GET {url} -> HTTP {e.code}")
        return None
    except Exception as e:
        logger.debug(f"[MCP-OAuth] GET {url} failed: {e}")
        return None


def _http_post_form(url: str, fields: dict, timeout: int = 20) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": _UA,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _http_post_json(url: str, payload: dict, timeout: int = 20) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _UA,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


# ------------------------------------------------------------------
# Discovery (RFC 9728 + RFC 8414)
# ------------------------------------------------------------------

def _origin(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def discover_metadata(resource_url: str, www_authenticate: str = "") -> Optional[dict]:
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
        prm = _http_get_json(as_metadata_url)
    if prm is None:
        origin = _origin(resource_url)
        prm = _http_get_json(f"{origin}/.well-known/oauth-protected-resource")
    if prm and isinstance(prm.get("authorization_servers"), list) and prm["authorization_servers"]:
        auth_server = prm["authorization_servers"][0]

    # 2) Authorization-server metadata (RFC 8414). Fall back to the resource
    #    origin when the resource did not advertise a separate auth server.
    base = auth_server or _origin(resource_url)
    asm = _fetch_as_metadata(base)
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


def _fetch_as_metadata(base: str) -> Optional[dict]:
    """Try both RFC 8414 and OIDC well-known locations."""
    base = base.rstrip("/")
    candidates = [
        f"{base}/.well-known/oauth-authorization-server",
        f"{base}/.well-known/openid-configuration",
    ]
    for url in candidates:
        data = _http_get_json(url)
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
                 scope: str = "", client_name: str = "Stella", on_authorized=None):
        self.server_name = server_name
        self.resource_url = resource_url
        self.redirect_uri = redirect_uri
        self.scope = scope
        self.client_name = client_name
        self.on_authorized = on_authorized

        self._reload()
        self._verifier: Optional[str] = None

    def _reload(self):
        rec = load_server_record(self.server_name)
        if rec and rec.get('resource_url') != self.resource_url:
            rec = _CredentialRecord({}, rec.original)
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
        original = save_server_record(self.server_name, saved, expected=self._record.original)
        for field in candidate:
            setattr(self, field, candidate[field])
        self._record = _CredentialRecord(candidate, original)

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
            resp = _http_post_form(self.metadata["token_endpoint"], fields)
        except Exception as e:
            logger.warning(f"[MCP-OAuth:{self.server_name}] refresh failed: {e}")
            return False
        return self._absorb_token_response(resp)

    # --- authorization-code flow ------------------------------------

    def ensure_registered(self, www_authenticate: str = "") -> bool:
        """Discover metadata + register a client if not already done."""
        if not self.metadata.get("authorization_endpoint"):
            meta = discover_metadata(self.resource_url, www_authenticate)
            if not meta:
                return False
            self.metadata = meta
        # Adopt the scope discovered from metadata when the user didn't set one.
        if not self.scope and self.metadata.get("_discovered_scope"):
            self.scope = self.metadata["_discovered_scope"]
            logger.info(f"[MCP-OAuth:{self.server_name}] Using discovered scope: {self.scope}")
        if not self.client_id:
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
            resp = _http_post_json(reg_endpoint, payload)
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
        return f"{self.metadata['authorization_endpoint']}?{urllib.parse.urlencode(params)}"

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
            resp = _http_post_form(self.metadata["token_endpoint"], fields)
        except Exception:
            logger.warning(f"[MCP-OAuth:{self.server_name}] token exchange failed")
            return False
        ok = self._absorb_token_response(resp)
        self._verifier = None
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

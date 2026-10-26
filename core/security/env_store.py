"""Owner-only adapter for the existing .env credential storage."""

import os
import re
from hashlib import sha256
import hmac
from pathlib import Path
import secrets
import sys
from typing import Dict
from common.log import logger
from common.utils import expand_path
from core.security.control import require_owner

_REFERENCE = 'stella-keychain:v1:'


def keychain_for(path):
    from core.security.keychain import MacOSKeychain
    return MacOSKeychain(str(path))


def _entries(path):
    from dotenv import dotenv_values
    return {name: value or '' for name, value in dotenv_values(path).items()} if Path(path).is_file() else {}


def _account(name, reference):
    from core.security.keychain import KeychainUnavailable
    version = reference.removeprefix(_REFERENCE)
    if not re.fullmatch(r'[a-f0-9]{32}', version):
        raise KeychainUnavailable('Credential reference is invalid; restore it through its owning settings')
    return sha256(name.encode()).hexdigest() + ':' + version


def read_environment(path):
    """Trusted startup/connector adapter; never exposed as an Agent method."""
    values = _entries(path)
    backend = None
    for name, value in values.items():
        if value.startswith(_REFERENCE):
            backend = backend or keychain_for(path)
            value = backend.get(_account(name, value))
            if value is None:
                from core.security.keychain import KeychainUnavailable
                raise KeychainUnavailable('Saved credential is unavailable; restore it through its owning settings')
            values[name] = value
    return values


def load_environment(path, *, override=True):
    try:
        values = read_environment(path)
    except (OSError, PermissionError):
        # A missing/locked reference cannot leave a stale secret or a literal
        # storage reference active in the process environment.
        for name, value in _entries(path).items():
            if value.startswith(_REFERENCE):
                os.environ.pop(name, None)
        raise
    for name, value in values.items():
        if override or name not in os.environ:
            os.environ[name] = value


def environment_statuses(path):
    """Only inspect exact registered items; do not request their secret values."""
    result = {}
    for name, value in _entries(path).items():
        if not value.startswith(_REFERENCE):
            result[name] = bool(value)
            continue
        try:
            result[name] = keychain_for(path).contains(_account(name, value))
        except (OSError, PermissionError):
            result[name] = None
    return result


class EnvironmentFile:
    def __init__(self):
        require_owner()
        self.env_dir = expand_path("~/.stella")
        self.env_path = os.path.join(self.env_dir, ".env")

    def _ensure_env_file(self):
        """Ensure the .env file exists"""
        require_owner()
        # Create ~/.stella directory if it doesn't exist
        os.makedirs(self.env_dir, exist_ok=True)

        if not os.path.exists(self.env_path):
            Path(self.env_path).touch()
            os.chmod(self.env_path, 0o600)
            logger.info(f"[EnvConfig] Created .env file at {self.env_path}")

    def _read_env_file(self) -> Dict[str, str]:
        """Read all key-value pairs from .env file"""
        require_owner()
        return read_environment(self.env_path)

    def update_secret(self, name, value):
        """Replace/remove one owner-selected field without decrypting its old value."""
        self._write_env_file({name: value}, replace=False)

    def _write_env_file(self, env_vars: Dict[str, str], *, replace=True):
        """Write all key-value pairs to .env file"""
        require_owner()
        from core.security.storage import private_atomic_write

        if not isinstance(env_vars, dict) or any(not isinstance(key, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key)
                or not (value is None and not replace or isinstance(value, str) and '\n' not in value and '\r' not in value)
                for key, value in env_vars.items()):
            raise ValueError('Invalid environment credential values')
        from core.plugins.locks import package_lock
        with package_lock(Path(self.env_dir), 'environment-credentials', exclusive=True):
            before = _entries(self.env_path)
            backend = keychain_for(self.env_path) if sys.platform == 'darwin' or any(
                value.startswith(_REFERENCE) for value in before.values()) else None
            saved = {}
            created = []
            publication_started = False
            try:
                selected = env_vars if replace else {**before, **env_vars}
                for name, value in selected.items():
                    if value is None:
                        continue
                    if not replace and name not in env_vars and value.startswith(_REFERENCE):
                        saved[name] = value
                        continue
                    if backend is None or not value:
                        saved[name] = value
                        continue
                    reference = _REFERENCE + secrets.token_hex(16)
                    account = _account(name, reference)
                    backend.put(account, value)
                    created.append(account)
                    verified = backend.get(account)
                    if verified is None or not hmac.compare_digest(verified.encode(), value.encode()):
                        raise PermissionError('Credential verification failed; existing configuration retained')
                    saved[name] = reference
                content = '# Environment variables for Stella\n\n' + ''.join(
                    f'{name}={value}\n' for name, value in sorted(saved.items()))
                publication_started = True
                private_atomic_write(self.env_path, content)
            except BaseException:
                # Rename may already have succeeded before an fsync error.
                # Retain both versions if publication is uncertain.
                for account in ([] if publication_started else created):
                    try:
                        backend.delete(account)
                    except (OSError, PermissionError):
                        logger.warning('[Credentials] Unpublished encrypted entry cleanup pending')
                raise
            if backend:
                for name, reference in before.items():
                    if reference.startswith(_REFERENCE) and reference != saved.get(name):
                        try:
                            backend.delete(_account(name, reference))
                        except (OSError, PermissionError):
                            logger.warning('[Credentials] Retired encrypted entry cleanup pending')

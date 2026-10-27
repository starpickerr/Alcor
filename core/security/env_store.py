"""Owner-only adapter for the existing .env credential storage."""

import os
import re
from pathlib import Path
from typing import Dict
from common.log import logger
from common.utils import expand_path
from core.security.control import require_owner
from core.security.credential_values import REFERENCE as _REFERENCE, account as _account  # noqa: F401 - existing adapter import
from core.security import credential_values


def keychain_for(path):
    from core.security.keychain import MacOSKeychain
    return MacOSKeychain(str(path))


def _entries(path):
    from dotenv import dotenv_values
    return {name: value or '' for name, value in dotenv_values(path).items()} if Path(path).is_file() else {}


def read_environment(path):
    """Trusted startup/connector adapter; never exposed as an Agent method."""
    return credential_values.resolve(path, _entries(path), keychain_for)[0]


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
    return credential_values.statuses(path, _entries(path), keychain_for)


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
            selected = env_vars if replace else {**before, **env_vars}
            selected = {name: value for name, value in selected.items() if value is not None}
            def commit(saved):
                content = '# Environment variables for Stella\n\n' + ''.join(
                    f'{name}={value}\n' for name, value in sorted(saved.items()))
                private_atomic_write(self.env_path, content)
            credential_values.publish(self.env_path, selected, before, keychain_for, commit)

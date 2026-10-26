"""Owner-only adapter for the existing .env credential storage."""

import os
import re
from pathlib import Path
from typing import Dict
from common.log import logger
from common.utils import expand_path
from core.security.control import require_owner


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
        env_vars = {}
        if os.path.exists(self.env_path):
            with open(self.env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue
                    # Parse KEY=VALUE
                    match = re.match(r"^([^=]+)=(.*)$", line)
                    if match:
                        key, value = match.groups()
                        env_vars[key.strip()] = value.strip()
        return env_vars

    def _write_env_file(self, env_vars: Dict[str, str]):
        """Write all key-value pairs to .env file"""
        require_owner()
        from core.security.storage import private_atomic_write

        content = "# Environment variables for Stella\n\n" + "".join(
            f"{key}={value}\n" for key, value in sorted(env_vars.items())
        )
        private_atomic_write(self.env_path, content)

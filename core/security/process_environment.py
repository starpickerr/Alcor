"""Minimal inherited state for Agent shell and delegated processes.

Platform variable lists and PATHEXT fallback adapted from OpenAI Codex,
codex-rs/protocol/src/shell_environment.rs at ac558e2a3264e1025fd8025fdd71d03dfbe8c86e.
Copyright 2025 OpenAI. SPDX-License-Identifier: Apache-2.0
See licenses/codex/{LICENSE,NOTICE} and THIRD_PARTY_NOTICES.md.
"""

import os
import sys


_UNIX_CORE = frozenset({
    'PATH', 'SHELL', 'TMPDIR', 'TEMP', 'TMP', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'LOGNAME', 'USER',
})
_WINDOWS_CORE = frozenset({
    'PATH', 'PATHEXT', 'SHELL', 'COMSPEC', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE',
    'USERNAME', 'USERDOMAIN', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
    'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432', 'PROGRAMDATA',
    'LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP', 'TMPDIR', 'POWERSHELL', 'PWSH',
})


def core_process_environment() -> dict[str, str]:
    """Select platform startup variables; no arbitrary host state or overrides."""
    allowed = _WINDOWS_CORE if sys.platform == 'win32' else _UNIX_CORE
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    if sys.platform == 'win32' and not any(key.upper() == 'PATHEXT' for key in env):
        env['PATHEXT'] = '.COM;.EXE;.BAT;.CMD'
    return env

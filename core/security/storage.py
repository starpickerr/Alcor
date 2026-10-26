"""Atomic owner writes whose temporary plaintext is inside a protected folder."""

import os
from pathlib import Path
import secrets


def private_atomic_write(path, content):
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    folder = path.parent / ".security-private-writes"
    if folder.is_symlink() or path.is_symlink():
        raise PermissionError("Credential storage cannot be redirected by symlinks")
    folder.mkdir(exist_ok=True, mode=0o700)
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = secrets.token_hex(24)
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)
        os.close(parent)

"""Core-owned code and shared interpreters are readable, never Agent-writable."""
import os
from pathlib import Path
import sys


def runtime_readonly_roots():
    from common.state_dir import shared_root
    return sorted({os.path.realpath(path) for path in (
        sys.prefix, sys.base_prefix, Path(__file__).resolve().parents[2],
        shared_root() / 'plugin-packages',
    )})


def is_runtime_path(path):
    target = os.path.realpath(path)
    return any(target == root or target.startswith(root + os.sep) for root in runtime_readonly_roots())


def source_digest(root, *, snapshot=None):
    """Hash source inputs, pruning local dependencies and generated products."""
    from hashlib import sha256
    root = Path(root).resolve()
    digest = sha256()
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(name for name in folders if name not in {
            '.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.ruff_cache', '.mypy_cache',
            'node_modules', 'build', 'dist',
        } and not name.endswith('.egg-info'))
        if any((Path(directory) / folder).is_symlink() for folder in folders):
            raise PermissionError('Runtime source inputs cannot use symlinks')
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix in {'.pyc', '.pyo'} or name == '.DS_Store':
                continue
            if path.is_symlink():
                raise PermissionError('Runtime source inputs cannot use symlinks')
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b'\0')
            content = path.read_bytes()
            digest.update(content)
            if snapshot is not None:
                snapshot[path.relative_to(root).as_posix()] = content
            digest.update(b'\0')
    return digest.hexdigest()


def bundle_digest(root):
    """Hash a complete frozen distribution, including bounded internal aliases."""
    from hashlib import sha256
    root = Path(root).resolve(strict=True)
    digest = sha256()
    for directory, folders, files in os.walk(root, followlinks=False):
        folders.sort()
        for name in sorted([*folders, *files]):
            path = Path(directory) / name
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b'\0')
            if path.is_symlink():
                try:
                    target = path.resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise PermissionError('Frozen bundle contains an invalid link') from exc
                if not target.is_relative_to(root):
                    raise PermissionError('Frozen bundle link points outside its distribution')
                digest.update(b'link\0' + str(path.readlink()).encode())
            elif path.is_dir():
                digest.update(b'directory')
            elif path.is_file():
                digest.update(b'file\0')
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
            else:
                raise PermissionError('Frozen bundle contains a non-regular input')
            digest.update(b'\0')
    return digest.hexdigest()

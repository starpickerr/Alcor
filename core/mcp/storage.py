"""Owner confirmation of retained MCP data; wire values never select paths."""
from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

from core.plugins.locks import package_lock
from core.plugins.management import default_store


def _identity(path):
    from core.security.mcp_runtime import _check_private_storage_path
    _check_private_storage_path(path)
    value = path.stat()
    if not stat.S_ISDIR(value.st_mode):
        raise PermissionError('MCP retained storage must be a directory')
    return {'path': str(path), 'device': value.st_dev, 'inode': value.st_ino}


def _key(config, user, digest):
    return ('mcp:' + config['name'], user, 'private-storage', digest)


def sources(config, user, digest):
    from core.security.mcp_runtime import storage_layout, _has_private_storage_data, _check_private_storage_path
    legacy, directory, _, bound = storage_layout(config, user, digest)
    if bound:
        return []
    paths = [legacy] if legacy.exists() and _has_private_storage_data(legacy) else []
    if directory.exists():
        for path in sorted(directory.iterdir()):
            _check_private_storage_path(path)
            if path.name != digest and _has_private_storage_data(path):
                paths.append(path)
    return paths


def approved(config, user, digest, storage, paths):
    values = default_store().settings(*_key(config, user, digest))['values']
    return (storage.exists() and values.get('target') == _identity(storage)
            and all(_identity(path) in values.get('sources', []) for path in paths))


_FLAGS = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)


def _publish(source, target):
    """Publish a directory atomically without replacing even an empty target."""
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == 'darwin':
        result = libc.renamex_np(os.fsencode(source), os.fsencode(target), 4)  # RENAME_EXCL
    elif sys.platform.startswith('linux'):
        result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE
    else:
        raise PermissionError('Atomic MCP data adoption is unavailable on this platform')
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _walk(path, target=None):
    """Inspect/copy through directory handles, refusing links and special files."""
    if os.open not in os.supports_dir_fd or not hasattr(os, 'O_NOFOLLOW'):
        raise PermissionError('Safe MCP data inspection is unavailable on this platform')
    result = []

    def visit(descriptor, relative, destination):
        for name in sorted(os.listdir(descriptor)):
            before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            folder = stat.S_ISDIR(before.st_mode)
            if not folder and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
                raise PermissionError('MCP retained data cannot contain links or special files')
            child = os.open(name, _FLAGS | (os.O_DIRECTORY if folder else os.O_NONBLOCK), dir_fd=descriptor)
            with os.fdopen(child, 'rb') if not folder else ExitStack() as stream:
                try:
                    current = os.fstat(child)
                    if (before.st_dev, before.st_ino, before.st_mode, before.st_nlink) != (
                            current.st_dev, current.st_ino, current.st_mode, current.st_nlink):
                        raise PermissionError('MCP retained data changed during inspection')
                    result.append([str(relative / name), current.st_dev, current.st_ino,
                                   current.st_mode, current.st_size, current.st_mtime_ns, current.st_ctime_ns])
                    if folder:
                        if destination:
                            (destination / name).mkdir(mode=0o700)
                        visit(child, relative / name, destination / name if destination else None)
                    elif destination:
                        with (destination / name).open('xb') as output:
                            shutil.copyfileobj(stream, output)
                finally:
                    if folder:
                        os.close(child)

    descriptor = os.open(path, _FLAGS | os.O_DIRECTORY)
    try:
        value = os.fstat(descriptor)
        result.append(['.', value.st_dev, value.st_ino, value.st_mode,
                       value.st_size, value.st_mtime_ns, value.st_ctime_ns])
        visit(descriptor, Path(), target)
    finally:
        os.close(descriptor)
    return result


def preview(lifecycle, name):
    lifecycle._owner()
    config = lifecycle._config(name)
    if config.get('type', 'stdio') != 'stdio':
        raise ValueError('Remote MCP has no local private data to adopt')
    facts = lifecycle._facts(config)
    digest = facts['environment_digest']
    from core.security.mcp_runtime import storage_layout
    _, _, target, _ = storage_layout(config, lifecycle.user, digest)
    paths = sources(config, lifecycle.user, digest)
    if not paths or approved(config, lifecycle.user, digest, target, paths):
        return None
    state = lifecycle.store.settings(*_key(config, lifecycle.user, digest))
    rows = [{**_identity(path), 'entries': _walk(path)} for path in paths]
    token = sha256(json.dumps([lifecycle.user, facts, rows, state['revision']], sort_keys=True).encode()).hexdigest()
    return {'token': token, 'sources': [{'id': sha256(json.dumps(_identity(path), sort_keys=True).encode()).hexdigest(),
                                       'path': str(path)} for path in paths]}


def adopt(lifecycle, name, token, source_id):
    lifecycle._owner()
    if not isinstance(token, str) or not isinstance(source_id, str):
        raise ValueError('Current storage confirmation and source are required')
    from core.mcp.lifecycle import admission_key
    from core.security.mcp_runtime import storage_layout
    with package_lock(lifecycle.store.root, admission_key(name, lifecycle.user) + ':management', exclusive=True, timeout=30):
        if lifecycle.pending(name):
            raise PermissionError('A pending MCP operation needs recovery first')
        current = preview(lifecycle, name)
        if current is None or current['token'] != token:
            raise ValueError('MCP data or configuration changed; refresh and confirm again')
        selected = next((row for row in current['sources'] if row['id'] == source_id), None)
        if selected is None:
            raise ValueError('Select a retained source from the current preview')
        config = lifecycle._config(name)
        digest = lifecycle._facts(config)['environment_digest']
        paths = sources(config, lifecycle.user, digest)
        _, _, target, _ = storage_layout(config, lifecycle.user, digest)
        lifecycle._stop_connections(name, None, family=config)
        with ExitStack() as locks:
            for key in sorted({digest, *(path.name for path in paths)}):
                locks.enter_context(package_lock(lifecycle.store.root, admission_key(name, lifecycle.user, key), exclusive=True, timeout=15))
            lifecycle._stop_connections(name, None, family=config)
            if preview(lifecycle, name) != current:
                raise ValueError('MCP data or configuration changed; refresh and confirm again')
            if target.exists():
                raise FileExistsError('Current MCP private storage already exists; it has been retained')
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix='.adopt-', dir=target.parent.parent) as scratch:
                staged = Path(scratch) / 'data'
                staged.mkdir(mode=0o700)
                _walk(Path(selected['path']), staged)
                if preview(lifecycle, name) != current:
                    raise ValueError('MCP data or configuration changed during copying; retry')
                # Persist the staged inode first so a crash after publication is recoverable.
                state = lifecycle.store.settings(*_key(config, lifecycle.user, digest))
                lifecycle.store.save_settings(*_key(config, lifecycle.user, digest),
                    {'target': {**_identity(staged), 'path': str(target)},
                     'sources': [_identity(path) for path in paths]}, expected_revision=state['revision'])
                _publish(staged, target)

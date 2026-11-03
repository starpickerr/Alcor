"""Descriptor-anchored file tool I/O with protection checks at the actual open."""
from contextlib import contextmanager
import os
import io
from pathlib import Path
import stat

from core.security.protected_paths import policy_guard

_DIRECTORY = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)


def identity(file):
    value = os.fstat(file.fileno())
    return value.st_dev, value.st_ino


class _CheckedFile(io.FileIO):
    def __init__(self, descriptor, mode, guard):
        super().__init__(descriptor, mode)
        self._guard = guard

    def _perform(self, operation, *args):
        with self._guard():
            return operation(*args)

    def read(self, size=-1):
        return self._perform(super().read, size)

    def readall(self):
        return self._perform(super().readall)

    def readinto(self, buffer):
        return self._perform(super().readinto, buffer)

    def write(self, buffer):
        return self._perform(super().write, buffer)

    def truncate(self, size=None):
        return self._perform(super().truncate, size)


@contextmanager
def _io_guard(revision, check):
    with policy_guard(revision):
        check()
        yield


@contextmanager
def checked_open(path, mode='r', *, encoding=None, expected=None, create_parents=False):
    """Pin every directory and the final object; never truncate before checks."""
    if mode not in {'r', 'rb', 'w'}:
        raise ValueError('Unsupported managed file mode')
    from agent.tools.utils.credentials import is_credential_path, credential_denial_message
    from core.runtime.paths import is_runtime_path
    raw = Path(path).expanduser().absolute()
    with policy_guard() as revision:
        target = raw.resolve()
        if is_credential_path(str(raw)):
            raise PermissionError(credential_denial_message(str(raw)))
        if mode == 'w' and is_runtime_path(str(target)):
            raise PermissionError('Shared runtime code requires its installation flow')
        parent = os.open(target.anchor, _DIRECTORY)
        descriptor = None
        try:
            for component in target.parent.parts[1:]:
                try:
                    child = os.open(component, _DIRECTORY, dir_fd=parent)
                except FileNotFoundError:
                    if mode != 'w' or not create_parents:
                        raise
                    os.mkdir(component, dir_fd=parent)
                    child = os.open(component, _DIRECTORY, dir_fd=parent)
                os.close(parent)
                parent = child
            try:
                before = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                before = None
            if before is not None and not stat.S_ISREG(before.st_mode):
                raise PermissionError('Only safely resolved regular files can be opened')
            flags = os.O_RDONLY if mode != 'w' else os.O_WRONLY | os.O_CREAT
            if before is None and mode == 'w':
                flags |= os.O_EXCL
            descriptor = os.open(target.name, flags | getattr(os, 'O_NOFOLLOW', 0), 0o666, dir_fd=parent)
            opened = os.fstat(descriptor)
            actual = (opened.st_dev, opened.st_ino)
            if not stat.S_ISREG(opened.st_mode) or (before and actual != (before.st_dev, before.st_ino)):
                raise PermissionError('File was replaced before access')
            if expected is not None and actual != expected:
                raise PermissionError('File changed after it was read; read it again before editing')
            def check():
                if raw.resolve() != target:
                    raise PermissionError('File path changed during access')
                current = raw.stat()
                if (current.st_dev, current.st_ino) != actual:
                    raise PermissionError('File was replaced during access')
                if is_credential_path(str(raw)):
                    raise PermissionError(credential_denial_message(str(raw)))
            check()
            if mode == 'w':
                os.ftruncate(descriptor, 0)
            pinned = _CheckedFile(descriptor, mode, lambda: _io_guard(revision, check))
            descriptor = None
            file = pinned if mode == 'rb' else io.TextIOWrapper(pinned, encoding=encoding, write_through=True)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(parent)
    try:
        yield file
        with policy_guard(revision):
            check()
    finally:
        try:
            file.close()
        finally:
            pinned.close()

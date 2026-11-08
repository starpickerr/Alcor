"""Explicit fresh initialization for test-owned temporary profiles only."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
import weakref

_issued = weakref.WeakKeyDictionary()


class _TestHome:
    __slots__ = ('__weakref__',)

    def __fspath__(self):
        return str(_issued[self][0])


@contextmanager
def fresh_test_home(prefix):
    if not isinstance(prefix, str) or not prefix or Path(prefix).name != prefix or prefix in ('.', '..'):
        raise ValueError('A simple test directory prefix is required')
    with tempfile.TemporaryDirectory(prefix=prefix, dir='/private/tmp') as directory:
        home = _TestHome()
        path = Path(directory)
        info = path.lstat()
        _issued[home] = (path, info.st_dev, info.st_ino)
        try:
            yield home
        finally:
            del _issued[home]


def _directory_fd(root_fd, parts):
    current = os.dup(root_fd)
    try:
        for part in parts:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def _exists(fd, name):
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def initialize_fresh(home, dsh_home, profile):
    """Create a marker once, before fixtures or any Alcor/legacy data exist."""
    if type(home) is not _TestHome or home not in _issued:
        raise ValueError('A live test-owned temporary home is required')
    root, device, inode = _issued[home]
    info = root.lstat()
    if root.parent != Path('/private/tmp') or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or (info.st_dev, info.st_ino) != (device, inode):
        raise ValueError('Unsafe test home')
    dsh_home, profile = Path(dsh_home), Path(profile)
    if not dsh_home.is_absolute() or not profile.is_absolute() or '..' in dsh_home.parts or '..' in profile.parts:
        raise ValueError('Absolute fixture paths required')
    try:
        home_parts = dsh_home.relative_to(root).parts
        profile_parts = profile.relative_to(dsh_home).parts
    except ValueError as error:
        raise ValueError('Profile escapes test home') from error
    if len(profile_parts) != 2 or profile_parts[0] != 'profiles' or profile_parts[1] in ('', '.', '..'):
        raise ValueError('Expected one profile inside test DSH_HOME')
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    dsh_fd = profile_fd = target_fd = None
    try:
        dsh_fd = _directory_fd(root_fd, home_parts)
        profile_fd = _directory_fd(dsh_fd, profile_parts)
        if any(_exists(profile_fd, name) for name in ('stella', 'alcor')) or _exists(dsh_fd, '.credentials.yaml'):
            raise ValueError('Profile, marker, or credentials already exist')
        if _exists(dsh_fd, 'storages'):
            storage_fd = _directory_fd(dsh_fd, ('storages',))
            try:
                if _exists(storage_fd, 'schedule.json') or any(_exists(storage_fd, name + suffix) for name in ('stella_organizer', 'stella_secretary', 'alcor_organizer', 'alcor_secretary') for suffix in ('', '.json')):
                    raise ValueError('Existing profile domains')
            finally:
                os.close(storage_fd)
        if _exists(profile_fd, 'package.json'):
            package_fd = os.open('package.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=profile_fd)
            with os.fdopen(package_fd, encoding='utf-8') as stream:
                package = json.load(stream)
            bundles = package.get('dsh', {}).get('profile', {}).get('bundles', [])
            dependencies = package.get('dependencies', {})
            if any(str(name).startswith('@stella/') for name in [*bundles, *dependencies]):
                raise ValueError('Legacy profile cannot be initialized as fresh')
        os.mkdir('alcor', 0o700, dir_fd=profile_fd)
        target_fd = _directory_fd(profile_fd, ('alcor',))
        marker = { 'schema': 1, 'status': 'complete', 'source': 'fresh',
            'directories': dict.fromkeys(('business', 'appleSync', 'email', 'elearning'), 'absent'),
            'domains': dict.fromkeys(('organizer', 'secretary'), 'absent'),
            'credentials': dict.fromkeys(('elearning', 'email'), 'absent'), 'schedule': { 'mapped': 0 } }
        marker_fd = os.open('.migration.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=target_fd)
        with os.fdopen(marker_fd, 'w', encoding='utf-8') as stream:
            json.dump(marker, stream)
            stream.write('\n')
        return marker
    finally:
        for fd in (target_fd, profile_fd, dsh_fd, root_fd):
            if fd is not None:
                os.close(fd)

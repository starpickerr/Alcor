"""Host-selected private storage for plugins without a legacy domain path."""
from pathlib import Path
from contextlib import contextmanager
import re


def private_data_handle(plugin_id: str, user_id: str, *, ensure=False) -> dict:
    if not isinstance(plugin_id, str) or not re.fullmatch(r'[a-z][a-z0-9_-]*(?:\.[a-z0-9_-]+)+', plugin_id):
        raise ValueError('invalid plugin storage namespace')
    from common.state_dir import user_root, shared_root
    from common.runtime_identity import identity_scope
    with identity_scope(user_id=user_id):
        root = user_root()
    path = Path(root) / 'plugins' / plugin_id
    anchor = Path(shared_root())
    try:
        path.relative_to(anchor)
    except ValueError:
        # Explicit standalone/test roots retain a local anchor. OS ancestors
        # such as macOS /var are outside the configured storage boundary.
        anchor = Path(root).parent
    candidates = [path]
    while candidates[-1] != anchor and candidates[-1] != candidates[-1].parent:
        candidates.append(candidates[-1].parent)
    for candidate in candidates:
        if candidate.is_symlink():
            raise PermissionError('plugin storage cannot use symlinks')
    if ensure:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
    return {'namespace': plugin_id, 'dataDir': str(path.absolute())}


_STORAGE_KEYS = ('dataDir', 'dbPath', 'legacyMemoryDir', 'legacyMainFile', 'legacyCalendarDbPath')


def bound_data_handle(plugin_id, handle):
    """Normalize paths supplied by trusted Core adapters before worker launch."""
    if handle.get('namespace', plugin_id) != plugin_id:
        raise PermissionError('plugin storage namespace mismatch')
    result = {**handle, 'namespace': plugin_id}
    for key in _STORAGE_KEYS:
        if key not in handle:
            continue
        path = Path(handle[key]).absolute()
        # /var and /tmp are OS aliases on macOS, not user-controlled redirects.
        candidate = path
        while candidate != candidate.parent and str(candidate) not in {'/var', '/tmp'}:
            if candidate.is_symlink():
                raise PermissionError('plugin storage cannot use symlinks')
            candidate = candidate.parent
        result[key] = str(path.resolve())
    if not (result.get('dataDir') or result.get('dbPath')):
        raise ValueError('plugin storage requires dataDir or dbPath')
    return result


def storage_paths(handle, *, legacy=False):
    """One owner domain is writable; legacy import sources are read-only."""
    primary = Path(handle['dbPath']).parent if handle.get('dbPath') else Path(handle['dataDir'])
    primary.mkdir(parents=True, exist_ok=True, mode=0o700)
    reads = [Path(handle[key]) for key in _STORAGE_KEYS[2:] if (legacy or key == 'legacyCalendarDbPath') and handle.get(key) and Path(handle[key]).exists()]
    return primary, reads


@contextmanager
def migration_import_handle(handle):
    """Expose only a fixed Markdown snapshot, never a legacy Core directory."""
    if handle.get('namespace') != 'stella.memory':
        yield handle
        return
    import shutil
    import tempfile
    normalized = bound_data_handle('stella.memory', handle)
    with tempfile.TemporaryDirectory(prefix='stella-memory-import-') as folder:
        target = Path(folder)
        memories = target / 'memory'
        memories.mkdir()
        legacy = Path(normalized['legacyMemoryDir'])
        if legacy.is_dir():
            for source in legacy.rglob('*.md'):
                relative = source.relative_to(legacy)
                if any(part.startswith('.') for part in relative.parts):
                    continue
                if source.is_symlink() or not source.resolve().is_relative_to(legacy.resolve()):
                    raise PermissionError('legacy memory symlink refused')
                if source.is_file():
                    destination = memories / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
        main = Path(normalized['legacyMainFile'])
        if main.is_file():
            shutil.copy2(main, target / 'MEMORY.md')
        yield {**normalized, 'legacyMemoryDir': str(memories), 'legacyMainFile': str(target / 'MEMORY.md')}

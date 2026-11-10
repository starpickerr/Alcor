"""Owner-protected paths for Agent tools and Stella-managed processes only."""
from contextlib import closing, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import threading
import weakref

from core.security.control import require_owner
from core.security.policy_store import RunPolicyStore

_LOCK = threading.RLock()
_PROCESSES = weakref.WeakValueDictionary()
_POLICIES = {}
_WAKE = threading.Condition(_LOCK)
_WATCHER = None


def _state(path=None):
    path = Path(path) if path is not None else Path(RunPolicyStore().path)
    if not path.exists():
        return dict(revision=0, phase='ready', rules=[])
    try:
        with closing(sqlite3.connect(path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='protected_path_state'").fetchone():
                return dict(revision=0, phase='ready', rules=[])
            row = db.execute('SELECT revision,body FROM protected_path_state WHERE id=1').fetchone()
        return dict(json.loads(row[1]), revision=row[0]) if row else dict(revision=0, phase='ready', rules=[])
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        raise PermissionError('Protected folder policy is unavailable; repair it through Security settings') from error


def _write(state, expected_revision):
    with RunPolicyStore().transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS protected_path_state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, body TEXT NOT NULL)')
        row = db.execute('SELECT revision FROM protected_path_state WHERE id=1').fetchone()
        if (row[0] if row else 0) != expected_revision:
            raise ValueError('Protection policy revision changed; refresh and retry')
        db.execute('INSERT INTO protected_path_state VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,body=excluded.body',
                   (expected_revision + 1, json.dumps({key: value for key, value in state.items() if key != 'revision'})))
    return expected_revision + 1


def _identity(path):
    target = Path(path)
    stat = target.stat()
    if not target.is_dir() or target.is_symlink():
        raise PermissionError('Protected folder changed or was replaced; repair its rule')
    return [stat.st_dev, stat.st_ino]


def _ready(path=None):
    state = _state(path)
    if state['phase'] != 'ready':
        raise PermissionError('Protected folder policy is changing; retry or repair it through Security settings')
    for row in state['rules']:
        try:
            if _identity(row['path']) != row['identity']:
                raise OSError('directory identity changed')
        except (OSError, PermissionError):
            raise PermissionError('Protected folder changed or was replaced; repair its rule through Security settings') from None
    return state


def current_revision():
    return _ready()['revision']


def check_context_revision(revision):
    if revision != current_revision():
        raise PermissionError('保护规则已变化或历史上下文来源未验证。请清空上下文或新建会话；历史记录保留。')


def sandbox_paths():
    state = _ready()
    return state['revision'], [row['path'] for row in state['rules']]


def _inside(path, root):
    return path == root or root in path.parents


def check_path(path):
    state = _ready()
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        raise PermissionError('Path cannot be resolved safely') from None
    if any(_inside(target, Path(row['path'])) for row in state['rules']):
        raise PermissionError('Agent access to an Owner-protected folder is denied')


def denial_reason(path):
    try:
        check_path(path)
    except PermissionError as error:
        return str(error)
    return None


def revoke_managed():
    """Cancel registered operations; never change tool switches or user data."""
    from common.process_tree import stop_process_tree
    from agent.protocol.cancel import get_cancel_registry
    get_cancel_registry().cancel_all()
    with _LOCK:
        processes = [process for pid, process in list(_PROCESSES.items())
                     if _POLICIES[pid][0] == Path(RunPolicyStore().path)]
    for process in processes:
        if process.poll() is None:
            stop_process_tree(process, owned_session=True)
        if process.poll() is None:
            raise RuntimeError('A managed process could not stop; protection transition requires retry')


def _watch():
    from common.process_tree import stop_process_tree
    while True:
        with _WAKE:
            for pid in list(_POLICIES):
                process = _PROCESSES.get(pid)
                if process is None or process.poll() is not None:
                    _POLICIES.pop(pid, None)
                    _PROCESSES.pop(pid, None)
            if not _POLICIES:
                _WAKE.wait()
                continue
            active = [(process, *_POLICIES[pid]) for pid, process in list(_PROCESSES.items())]
        for process, path, revision in active:
            try:
                if _ready(path)['revision'] != revision:
                    raise PermissionError('Protection policy changed')
            except PermissionError:
                stop_process_tree(process, owned_session=True)
        with _WAKE:
            _WAKE.wait(.1)


class _Launch:
    def __init__(self, revision):
        self.revision = revision

    def bind(self, process):
        global _WATCHER
        _PROCESSES[process.pid] = process
        _POLICIES[process.pid] = (Path(RunPolicyStore().path), self.revision)
        if _WATCHER is None:
            _WATCHER = threading.Thread(target=_watch, daemon=True, name='protected-path-processes')
            _WATCHER.start()
        _WAKE.notify_all()


@contextmanager
def policy_guard(revision=None):
    with _LOCK:
        current = current_revision()
        if revision is not None and current != revision:
            raise PermissionError('Protection policy changed; prepare the affected operation again')
        yield current


@contextmanager
def launch_guard(revision):
    """Keep policy admission, actual spawn and process registration together."""
    with policy_guard(revision):
        yield _Launch(revision)


def _required_paths():
    from config import get_data_root
    from core.runtime.paths import runtime_readonly_roots
    from agent.tools.utils.credentials import protected_credential_paths, protected_credential_folders
    roots = {*runtime_readonly_roots(), *protected_credential_paths(), *protected_credential_folders(),
             get_data_root(), str(RunPolicyStore().path)}
    from agent.tools.tool_manager import ToolManager
    from core.security.mcp_runtime import _launch_inputs
    for config in ToolManager()._load_mcp_configs():
        if config.get('type', 'stdio') != 'stdio':
            continue
        try:
            files, package = _launch_inputs(config)
            roots.update(map(str, [package, *files]))
        except (OSError, ValueError):
            continue  # An invalid/unavailable launch has no usable source root.
    return [Path(path).expanduser().resolve() for path in roots]


class ProtectedPathControl:
    def inventory(self):
        require_owner()
        state = _state()
        rows = []
        for row in state['rules']:
            try:
                valid = _identity(row['path']) == row['identity']
            except (OSError, PermissionError):
                valid = False
            rows.append(dict(id=row['id'], path=row['path'], status='active' if valid else 'needs-repair'))
        return dict(revision=state['revision'], phase=state['phase'], rules=rows,
                    pending_paths=[row['path'] for row in state.get('pending_rules', [])], scope='agent-and-managed-execution')

    def replace(self, paths, *, expected_revision):
        require_owner()
        if not isinstance(paths, list) or len(paths) > 100 or type(expected_revision) is not int:
            raise ValueError('Protected paths and their current revision are required')
        required = _required_paths()
        rules = []
        for value in paths:
            if not isinstance(value, str) or not value or '\0' in value or not Path(value).expanduser().is_absolute():
                raise ValueError('Protected folder requires an absolute path')
            try:
                path = Path(value).expanduser().resolve(strict=True)
                identity = _identity(path)
            except (OSError, RuntimeError, PermissionError):
                raise ValueError('Protected folder must be an existing, safely resolved directory') from None
            if any(_inside(path, root) or _inside(root, path) for root in required):
                raise ValueError('Protected folder conflicts with required runtime, credential, policy or business storage')
            rules.append(dict(id=sha256(str(path).encode()).hexdigest(), path=str(path), identity=identity))
        rules.sort(key=lambda row: row['path'])
        if len({row['path'] for row in rules}) != len(rules):
            raise ValueError('Duplicate protected folders')
        with _LOCK:
            state = _state()
            if state['revision'] != expected_revision:
                raise ValueError('Protection policy revision changed; refresh and retry')
            if state['phase'] == 'ready' and state['rules'] == rules:
                return self.inventory()
            pending = dict(phase='changing', rules=state['rules'], pending_rules=rules)
            revision = _write(pending, expected_revision)
        # A failed drain leaves a durable, actionable changing state. Retrying
        # from the owner page uses this revision and preserves every source file.
        revoke_managed()
        with _LOCK:
            _write(dict(phase='ready', rules=rules), revision)
        return self.inventory()

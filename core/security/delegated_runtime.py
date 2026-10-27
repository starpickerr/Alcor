"""Core builds child process plans from the live parent ceiling, never wire ACLs."""
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from hashlib import sha256
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

_LAUNCH_SCOPE = ContextVar('core_delegated_launch_scope', default=None)


def prepare_delegated(workspace, binaries):
    from common.runtime_identity import current_identity
    from agent.runtime.tool_invocation import _current_authorization
    from agent.permission.security_policy import rule
    from core.security.extension_sandbox import prepare_extension
    identity = current_identity()
    mode, context, _ = _current_authorization(None, identity.agent_id, identity.session_id, None, False)
    policy = context['policy']
    from core.security.service import SecurityService
    if SecurityService.native_user_operation():
        policy = {**policy, 'preset': 'full-access', 'filesystem_mode': 'full-access', 'rules': {},
                  'migration_status': 'complete'}
        policy.pop('error', None)
        mode = 'full-access'
    if mode == 'full-access':
        from agent.permission.process_sandbox import prepare
        home = Path(workspace) / '.home'
        home.mkdir(exist_ok=True, mode=0o700)
        env = {key: value for key, value in os.environ.items() if key in {
            'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SYSTEMROOT', 'WINDIR'}}
        env.update(HOME=str(home), USERPROFILE=str(home))
        plan = prepare('exec "$@"', str(workspace), [str(workspace)], policy, env)
        return {'prefix': [*plan.argv, 'stella-delegation'], 'env': env, 'mode': 'isolated', 'protection_revision': plan.protection_revision}
    reads = [context['cwd'], *context['scope'].write_roots, *(policy.get('extra_roots') or [])]
    executable_reads = [shutil.which(value) or value for value in binaries if value]
    env = {key: value for key, value in os.environ.items() if key in {
        'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SYSTEMROOT', 'WINDIR'}}
    plan = prepare_extension(['/usr/bin/true'], Path(workspace), Path(workspace), env,
        writable=mode == 'workspace-write' and rule(policy, 'workspace_write') == 'allow',
        read_roots=reads, executable_reads=executable_reads)
    return {'prefix': plan.argv[:-1], 'env': plan.env, 'mode': 'isolated', 'protection_revision': plan.protection_revision}


@contextmanager
def delegated_scope(session, workspace, binaries, *, task=None):
    """Only a Core adapter binds commands, task lease and inherited policy."""
    from core.security.service import SecurityService
    from core.security.runtime_trust import plugin_trusted
    if not plugin_trusted(session.plugin_root, session.plugin_id, session.package_digest, session.user_id):
        raise PermissionError('Core execution requires a verified first-party provider adapter')
    paths = {}
    for value in binaries:
        path = Path(shutil.which(value) or value).resolve()
        from agent.tools.utils.credentials import is_credential_path
        if is_credential_path(str(path)):
            raise PermissionError('A protected resource cannot be used as a provider binary')
        if path.is_file() and os.access(path, os.X_OK):
            paths[str(path)] = sha256(path.read_bytes()).hexdigest()
    plan = prepare_delegated(workspace, list(paths))
    token = _LAUNCH_SCOPE.set(dict(session=session, workspace=Path(workspace).resolve(), plan=plan,
        binaries=paths, task=task, policy_binding=SecurityService.policy_binding()))
    try:
        yield
    finally:
        _LAUNCH_SCOPE.reset(token)


class DelegatedProcesses:
    """Ephemeral process records; execution history remains in ExecutionService."""
    def __init__(self, session):
        from core.execution.process_supervisor import ProcessSupervisor
        self.session = session
        self.supervisor = ProcessSupervisor()
        self.lock = threading.RLock()
        self.runs = {}
        self.closed = False

    def _spawn(self, scope, argv, output, *, wall, execution_id=None):
        if (not isinstance(argv, list) or not argv or any(not isinstance(value, str) or '\0' in value for value in argv)
                or not 0 < wall <= 3600):
            raise PermissionError('Invalid delegated command')
        binary = Path(shutil.which(argv[0]) or argv[0]).resolve()
        expected = scope['binaries'].get(str(binary))
        if expected is None:
            raise PermissionError('Delegated binary is not bound to the approved provider')
        if expected != sha256(binary.read_bytes()).hexdigest():
            raise PermissionError('Delegated binary changed after provider selection')
        session = self.session
        worker = session._proc
        from core.security.service import SecurityService
        if SecurityService.policy_binding() != scope['policy_binding']:
            raise PermissionError('Delegating policy changed before launch')
        with self.lock:
            if self.closed or worker is None or worker.poll() is not None:
                raise PermissionError('Core worker generation is no longer active')
            from core.security.protected_paths import launch_guard
            with launch_guard(scope['plan']['protection_revision']) as launch:
                process = subprocess.Popen([*scope['plan']['prefix'], str(binary), *argv[1:]],
                    cwd=scope['workspace'], env=scope['plan']['env'], stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
                launch.bind(process)
            reference = 'runtime-' + uuid.uuid4().hex
            self.supervisor.register(reference, process)
            record = dict(process=process, status='running', outputPath=getattr(output, 'name', None),
                          execution_id=execution_id, deadline=time.monotonic() + wall)
            self.runs[reference] = record

        def monitor():
            while True:
                with self.lock:
                    if record['status'] != 'running':
                        return
                    exited = process.poll()
                    reason = None
                    try:
                        if SecurityService.policy_binding() != scope['policy_binding']:
                            reason = 'policy_changed'
                    except Exception:
                        reason = 'execution_scope_expired'
                    if worker.poll() is not None or session._proc is not worker:
                        reason = 'worker_stopped'
                    if time.monotonic() >= record['deadline']:
                        reason = 'wall_time_exceeded'
                    if exited is not None or reason:
                        # Reap all groups in the owned OS session, including
                        # ordinary descendants left by an exited leader.
                        self.supervisor.revoke(reference)
                        record['status'] = ('outcome_unknown' if reason else 'succeeded' if exited == 0 else 'failed')
                        if reason:
                            record['error'] = {'code': reason}
                        return
                time.sleep(.02)
        threading.Thread(target=copy_context().run, args=(monitor,), name='delegated-runtime', daemon=True).start()
        return reference, record

    def probe(self, scope, argv):
        if not isinstance(argv, list) or len(argv) != 2 or argv[1] not in {'--version', 'version'}:
            raise PermissionError('Provider probes only accept the declared version operation')
        with tempfile.TemporaryFile(mode='w+b') as output:
            reference, record = self._spawn(scope, argv, output, wall=8)
            while record['status'] == 'running':
                time.sleep(.02)
            output.seek(0)
            value = output.read(4096).decode('utf-8', errors='replace').strip()[:500]
            with self.lock:
                self.runs.pop(reference, None)
            return {'ok': record['status'] == 'succeeded', 'returncode': record['process'].returncode, 'output': value}

    def start(self, scope, argv, filename):
        task = scope['task']
        if not task or not isinstance(filename, str) or Path(filename).name != filename or filename in {'', '.', '..'}:
            raise PermissionError('Core execution lease and a safe output name are required')
        directory = os.open(scope['workspace'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            # No truncation or link following: an existing file is not ours.
            goal = os.open('.stella-agent-hub-goal.md', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(goal, 'w', encoding='utf-8') as stream:
                stream.write(task['goal'].strip() + '\n')
            descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(descriptor, 'wb') as output:
                reference, record = self._spawn(scope, argv, output,
                    wall=float(task['budget']['maxWallTime']), execution_id=task['executionId'])
                record['outputPath'] = str(scope['workspace'] / filename)
            return {'externalRunRef': reference, 'status': 'started'}
        finally:
            os.close(directory)

    def get(self, reference, execution_id):
        with self.lock:
            record = self.runs.get(reference)
            if record is None or record['execution_id'] != execution_id:
                raise PermissionError('Runtime reference does not belong to this execution')
            return record

    def observe(self, reference, execution_id):
        with self.lock:
            record = self.get(reference, execution_id)
            if record['status'] == 'running':
                return [{'type': 'progress', 'message': 'CLI task is running'}]
            return [{'type': 'terminal', 'status': record['status'], 'outputPath': record['outputPath'],
                     'summary': 'CLI task finished', 'error': record.get('error')}]

    def cancel(self, reference, execution_id):
        with self.lock:
            record = self.get(reference, execution_id)
            if record['status'] != 'running':
                return {'status': 'already_finished'}
            self.supervisor.revoke(reference)
            record['status'] = 'cancelled'
            return {'status': 'acknowledged'}

    def close(self):
        with self.lock:
            self.closed = True
            self.supervisor.revoke_all()
            for record in self.runs.values():
                if record['status'] == 'running':
                    record.update(status='outcome_unknown', error={'code': 'worker_stopped'})


def invoke_runtime(session, parent, method, payload):
    """Private callback, available only inside the existing admitted Hub RPC."""
    owner_method = {'start': 'provider_start', 'probe': {'probe_provider', 'select_provider'},
                    'observe': 'provider_observe', 'cancel': 'provider_cancel'}.get(method)
    if (session.plugin_id != 'stella.agent-hub' or parent.get('contract') != 'stella.agent-hub@1'
            or parent.get('method') not in (owner_method if isinstance(owner_method, set) else {owner_method})):
        raise PermissionError('Runtime callback is outside its approved owner operation')
    scope = _LAUNCH_SCOPE.get()
    if method in {'start', 'probe'} and (not scope or scope['session'] is not session):
        raise PermissionError('Core launch scope is required')
    session._check_runtime()
    if not hasattr(session, '_delegated_processes'):
        session._delegated_processes = DelegatedProcesses(session)
    manager = session._delegated_processes
    fields = {'start': {'argv', 'filename'}, 'probe': {'argv'}, 'observe': {'reference'}, 'cancel': {'reference'}}[method]
    if not isinstance(payload, dict) or set(payload) != fields:
        raise PermissionError('Runtime callback cannot supply environment, paths or authority')
    if method == 'probe':
        value = manager.probe(scope, payload['argv'])
    elif method == 'start':
        value = manager.start(scope, payload['argv'], payload['filename'])
    else:
        value = getattr(manager, method)(payload['reference'], parent['input'].get('execution_id'))
    return {'status': 'succeeded', 'sideEffects': 'none' if method in {'probe', 'observe'} else 'committed', 'value': value}

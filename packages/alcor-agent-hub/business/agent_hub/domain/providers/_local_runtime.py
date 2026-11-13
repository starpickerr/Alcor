"""Local CLI probing, minimal environment and managed goal files."""

from __future__ import annotations

import uuid
import atexit
import os
import signal
import subprocess
import threading
import codecs
import time
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_SANDBOX = ContextVar('host_execution_sandbox', default=None)
_RUNS = {}


@contextmanager
def execution_sandbox(plan):
    token = _SANDBOX.set(plan)
    try:
        yield
    finally:
        _SANDBOX.reset(token)


def stop_process_tree(process, *, force=False):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        # Popen retains the process handle, keeping its PID from being recycled.
        executable = Path(os.environ['SystemRoot']) / 'System32' / 'taskkill.exe'
        result = subprocess.run([str(executable), '/PID', str(process.pid), '/T', '/F'],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode and process.poll() is None:
            raise RuntimeError('Could not stop the owned CLI process tree')
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            pass


def runtime_call(method, payload):
    plan = _SANDBOX.get()
    if method == 'probe':
        try:
            result = subprocess.run(payload['argv'], capture_output=True, text=True, timeout=8)
            return {'ok': result.returncode == 0, 'stdout': result.stdout[:8000], 'stderr': result.stderr[:8000]}
        except (OSError, subprocess.TimeoutExpired) as error:
            return {'ok': False, 'error': str(error)}
    if method == 'start':
        if not isinstance(plan, dict) or not plan.get('workspacePath'):
            raise ValueError('workspacePath is required')
        workspace = Path(plan['workspacePath']).resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError('workspacePath must be a directory')
        output = Path(plan['outputDirectory']) / (uuid.uuid4().hex + '.txt')
        output.parent.mkdir(parents=True, exist_ok=True)
        handle = output.open('wb')
        try:
            process = subprocess.Popen(payload['argv'], cwd=workspace, stdout=handle, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
        except Exception:
            handle.close()
            raise
        reference = uuid.uuid4().hex
        run = {'process': process, 'output': output, 'status': 'running', 'cancelled': False}
        _RUNS[reference] = run
        def settle():
            code = process.wait()
            handle.close()
            run['status'] = 'cancelled' if run['cancelled'] else 'succeeded' if code == 0 else 'failed'
        threading.Thread(target=settle, daemon=True).start()
        return {'status': 'started', 'externalRunRef': reference}
    run = _RUNS.get(payload['reference'])
    if run is None:
        return [{'type': 'terminal', 'status': 'outcome_unknown'}] if method == 'observe' else {'status': 'unknown'}
    if method == 'cancel':
        if run['process'].poll() is None:
            run['cancelled'] = True
            stop_process_tree(run['process'])
            def force_stop():
                try:
                    run['process'].wait(timeout=3)
                except subprocess.TimeoutExpired:
                    stop_process_tree(run['process'], force=True)
            threading.Thread(target=force_stop, daemon=True).start()
        return {'status': 'acknowledged'}
    if method == 'observe':
        cursor = payload.get('after_cursor', 0)
        if type(cursor) is not int or cursor < 0:
            raise ValueError('invalid output cursor')
        with run['output'].open('rb') as handle:
            handle.seek(cursor)
            data = handle.read(65536)
            if data and run['status'] == 'running' and b'\n' in data:
                data = data[:data.rfind(b'\n') + 1]
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        text = decoder.decode(data, final=run['status'] != 'running' and cursor + len(data) >= run['output'].stat().st_size)
        consumed = len(data) - len(decoder.getstate()[0])
        events = [{'type': 'output', 'text': text, 'cursor': cursor + consumed}] if consumed else []
        if run['status'] != 'running' and cursor + consumed >= run['output'].stat().st_size:
            events.append({'type': 'terminal', 'status': run['status'], 'outputPath': str(run['output'])})
        return events
    raise ValueError('Unknown runtime operation')


@atexit.register
def close_runs():
    processes = [run['process'] for run in _RUNS.values() if run['process'].poll() is None]
    for process in processes:
        stop_process_tree(process)
    deadline = time.monotonic() + 3
    for process in processes:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            stop_process_tree(process, force=True)


def run_version_probe(argv: list[str], *, timeout: float = 8.0) -> dict[str, Any]:
    return runtime_call('probe', {'argv': argv})


def new_run_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"

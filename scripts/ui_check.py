"""Run browser checks against a private renderer and clean its process group."""
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_runtime import npm_path, python_path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.process_tree import stop_process_tree  # noqa: E402

SCRIPT_TIMEOUT = 90
UI_TIMEOUT = 1800


def run_clients(clients, env, timeout=SCRIPT_TIMEOUT):
    failed = False
    for client in clients:
        name = client[-1]
        print(f'RUN ui: {name}', flush=True)
        started = time.monotonic()
        process = subprocess.Popen(client, cwd=ROOT, env=env, start_new_session=True)
        expired = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code, expired = 1, True
        finally:
            stop_process_tree(process, owned_session=True)
        failed = failed or code != 0
        detail = f'; timeout after {timeout}s' if expired else f'; exit {code}'
        status = 'FAIL' if code else 'PASS'
        print(f'{status} ui: {name} ({time.monotonic() - started:.2f}s{detail})', flush=True)
    return int(failed)


def run_server(server, clients, port=None):
    with socket.socket() as reservation:
        try:
            reservation.bind(('127.0.0.1', port or 0))
        except OSError as exc:
            raise RuntimeError('renderer port occupied') from exc
        port = reservation.getsockname()[1]
    env = dict(os.environ, STELLA_TEST_URL=f'http://127.0.0.1:{port}')
    command = [value.replace('{port}', str(port)) for value in server]
    with tempfile.TemporaryFile(mode='w+') as output:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=output, stderr=output, start_new_session=True)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    output.seek(0)
                    raise RuntimeError('renderer exited: ' + output.read()[-2000:])
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('renderer startup timeout')
            if process.poll() is not None:
                raise RuntimeError('renderer exited before browser check')
            return run_clients(clients, env)
        finally:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                process.wait(timeout=5)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()


if __name__ == '__main__':
    scripts = ['tests/ui_smoke.py', 'tests/ui_briefing.py', 'tests/ui_todo_deadlines.py', 'tests/ui_tasks.py', 'tests/ui_apple_calendar.py',
               'tests/ui_apple_settings.py', 'tests/ui_embedded_agents.py', 'tests/ui_work_shares.py',
               'tests/ui_run_policy.py', 'tests/ui_knowledge_links.py', 'tests/ui_settings_entry.py',
               'tests/ui_information_architecture.py', 'tests/ui_capabilities.py', 'tests/ui_capability_details.py', 'tests/ui_capability_features.py', 'tests/ui_model_selection.py',
               'tests/ui_data_management.py', 'tests/ui_elearning_settings.py']
    checks = [[str(python_path(ROOT)), '-u', '-c',
               'import runpy, sys; runpy.run_path(sys.argv[1])', path] for path in scripts]
    sys.exit(run_server([npm_path(), '--prefix', 'desktop', 'run', 'dev:renderer', '--', '--host', '127.0.0.1', '--port', '{port}'], checks))

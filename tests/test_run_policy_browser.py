"""Real React interactions with transport fixtures; backend policy is tested separately."""
from pathlib import Path
import socket
import subprocess
import time
import urllib.request


REPO = Path(__file__).resolve().parents[1]


def test_three_modes_save_immediately_without_security_management():
    import os
    import sys

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    process = subprocess.Popen(['node', 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', str(port), '--strictPort'],
        cwd=REPO / 'desktop', stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(origin, timeout=.2).close()
                break
            except OSError:
                time.sleep(.05)
        else:
            raise AssertionError('Vite did not start')
        subprocess.run([sys.executable, str(REPO / 'tests/ui_run_policy.py')],
            cwd=REPO, env={**os.environ, 'STELLA_TEST_URL': origin}, check=True, timeout=90)
    finally:
        process.terminate()
        process.communicate(timeout=10)

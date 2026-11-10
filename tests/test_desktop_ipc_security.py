"""Actual Electron processes must enforce IPC and renderer network boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.test_plugin_frame_electron import _electron_bin

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / 'desktop'


@pytest.mark.parametrize('renderer_mode', ['http', 'file'])
def test_real_renderer_keeps_host_apis_and_network_within_declared_boundaries(tmp_path, renderer_mode):
    electron = _electron_bin()
    node = shutil.which('node')
    tsc = DESKTOP / 'node_modules/typescript/bin/tsc'
    if not electron or not node or not tsc.exists():
        pytest.skip('Electron and local TypeScript are required')
    if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
        pytest.skip('Linux display unavailable; macOS is the delivery environment')
    output = tmp_path / 'compiled'
    result = subprocess.run([node, str(tsc), str(DESKTOP / 'src/main/renderer-security.ts'),
                             '--outDir', str(output), '--module', 'commonjs', '--target', 'ES2020',
                             '--strict', '--skipLibCheck', '--esModuleInterop', '--typeRoots',
                             str(DESKTOP / 'node_modules/@types')],
                            cwd=DESKTOP, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    env = dict(os.environ, STELLA_RENDERER_SECURITY_JS=str(output / 'renderer-security.js'),
               STELLA_SECURITY_WORK=str(tmp_path), STELLA_RENDERER_MODE=renderer_mode,
               NODE_PATH=str(DESKTOP / 'node_modules'))
    env.pop('ELECTRON_RUN_AS_NODE', None)
    result = subprocess.run([str(electron), str(ROOT / 'tests/electron/renderer_security_harness.cjs')],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
    lines = [line.removeprefix('SECURITY_RESULT ') for line in result.stdout.splitlines()
             if line.startswith('SECURITY_RESULT ')]
    assert result.returncode == 0 and lines, result.stdout[-2500:] + result.stderr[-2500:]
    report = json.loads(lines[-1])
    assert 'error' not in report, report
    assert report['normalIpc'] == 'owner-ui-ok'
    assert report['foreignWindowDenied'] is True
    assert report['changedPageDenied'] is True
    assert report['normalBackend'] == 'test-backend-ok'
    assert report['childStatus'] == 403
    assert report['otherPortDenied'] is True
    assert report['otherPortRequests'] == 0
    assert report['navigationBlocked'] is True
    assert report['unsafePopupDenied'] is True
    assert report['nodeAbsent'] is True
    assert report['urlBoundary'] == [True, False, False, False]
    assert report['localPaths'] == ['reveal', 'open', 'reveal', 'open']
    assert report['iconRequestsRejected'] is True
    assert report['localIconAccepted'] is True

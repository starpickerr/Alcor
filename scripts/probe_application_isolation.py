#!/usr/bin/env python3
"""Compile and run the native preflight without signing, escalation or policy edits."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def probe():
    report = {'scope': 'preflight', 'platform': sys.platform, 'protected': False}
    if sys.platform != 'darwin':
        return {**report, 'endpoint_security': {'status': 'unavailable', 'reason': 'macOS only'}}
    xcrun = shutil.which('xcrun')
    if not xcrun:
        return {**report, 'endpoint_security': {'status': 'unavailable', 'reason': 'Xcode tools missing'}}
    source = Path(__file__).resolve().parents[1] / 'native/security/endpoint_security_probe.c'
    with tempfile.TemporaryDirectory(prefix='stella-es-preflight-') as directory:
        executable = str(Path(directory) / 'probe')
        env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'DEVELOPER_DIR') if key in os.environ}
        env.update(HOME=directory, TMPDIR=directory)
        result = subprocess.run([xcrun, 'clang', '-fblocks', str(source), '-lEndpointSecurity', '-o', executable],
                                capture_output=True, timeout=30, env=env)
        if result.returncode:
            return {**report, 'endpoint_security': {'status': 'unavailable', 'reason': 'Native preflight build failed'}}
        result = subprocess.run([executable], capture_output=True, text=True, timeout=10, env=env)
        if result.returncode:
            return {**report, 'endpoint_security': {'status': 'unavailable', 'reason': 'Native preflight could not run'}}
        return {**report, 'endpoint_security': json.loads(result.stdout)}


if __name__ == '__main__':
    try:
        print(json.dumps(probe()))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        print(json.dumps({'scope': 'preflight', 'protected': False, 'platform': sys.platform,
                          'endpoint_security': {'status': 'unavailable', 'reason': 'Native preflight unavailable'}}))

"""Seed verified public dependency artifacts into disposable test state only."""
from pathlib import Path
import hashlib
import json
import os
import shutil

import pytest


def seed_runtime_artifacts():
    from common.state_dir import shared_root
    source = Path(os.environ.get('STELLA_TEST_RUNTIME_ARTIFACTS',
        str(Path(__file__).resolve().parents[2] / '.superpowers/sdd/2026-10-25-security-managed-runtime/public-artifacts')))
    if not (source / 'tools/uv.json').is_file():
        pytest.skip('Verified public runtime artifacts not prepared; no network or dependency fallback is used')
    target = shared_root() / 'managed-runtimes'
    for path in (source / 'downloads/sha256').glob('*/*.whl'):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.parent.name
        dest = target / 'downloads/sha256' / path.parent.name / path.name
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    marker = json.loads((source / 'tools/uv.json').read_text())
    binary = source / 'tools' / marker['digest'] / 'uv'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == marker['digest']
    tool = target / 'tools' / marker['digest'] / 'uv'
    if not tool.exists():
        tool.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(binary, tool)
    (target / 'tools/uv.json').write_text(json.dumps(marker))
    return target


@pytest.fixture
def offline_runtime_artifacts():
    return seed_runtime_artifacts()


if __name__ == '__main__':
    import argparse
    from common.state_dir import shared_root
    from core.runtime.artifacts import python_wheels
    from core.runtime.tools import ManagedTools
    parser = argparse.ArgumentParser(description='Prepare hash-verified public artifacts in disposable state')
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    ManagedTools().uv()
    root = Path(__file__).resolve().parents[2]
    sources = json.loads((root / 'resources/legacy-sources.lock.json').read_text())
    for name in ('email', 'elearning'):
        python_wheels(root.parent / sources[name]['path'] / 'requirements.lock')
    shutil.copytree(shared_root() / 'managed-runtimes', args.destination)
    print(f'PASS: verified public runtime artifacts prepared at {args.destination}')

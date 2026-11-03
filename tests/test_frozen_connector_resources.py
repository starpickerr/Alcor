"""Frozen connectors execute only Core-pinned source snapshots."""
import json
import sys

import pytest

from core.mcp.managed import ManagedMcpConfig
from core.runtime.paths import bundle_digest, source_digest
from core.services import trusted_account_modules as loader


@pytest.mark.parametrize('domain', ['email', 'elearning'])
def test_frozen_connector_loads_the_verified_build_snapshot(tmp_path, monkeypatch, domain):
    from scripts.build_mcp_resources import prepare
    dist = tmp_path / 'dist'
    for name in ('email', 'elearning'):
        package = dist / (name + '-mcp')
        package.mkdir(parents=True)
        (package / (name + '-mcp')).write_bytes(b'FROZEN_TEST_EXECUTABLE')
    resources = tmp_path / 'resources'
    prepare(dist, resources)
    catalog = json.loads((resources / 'mcp-providers.json').read_text())
    root = dist / (domain + '-mcp')
    row = catalog['providers'][domain]
    assert row['bundleSha256'] == bundle_digest(root)
    snapshot = resources / 'trusted-connectors' / domain
    assert row['connectorSha256'] == source_digest(snapshot)
    assert {path.name for path in snapshot.rglob('*.py')} == {
        name + '.py' for name in loader._TRUSTED_MODULES[domain]}
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(loader, '_MODULES', {})
    config = ManagedMcpConfig({'name': domain, 'command': str(root / (domain + '-mcp')), 'args': []},
        data_root=tmp_path / 'data', package_digest=row['bundleSha256'])
    modules = loader.load_business(config)
    assert 'service' in modules
    sentinel = tmp_path / 'unverified-code-executed'
    with (snapshot / (domain + '_mcp/service.py')).open('a') as stream:
        stream.write('\nfrom pathlib import Path\nPath(' + repr(str(sentinel)) + ').touch()\n')
    with pytest.raises(PermissionError):
        loader.load_business(config)
    assert not sentinel.exists()


def test_build_rejects_a_changed_provider_before_bundling(tmp_path, monkeypatch):
    from scripts import build_mcp_resources
    original = build_mcp_resources.source_digest
    def changed(root, **kwargs):
        original(root, **kwargs)
        return '0' * 64
    monkeypatch.setattr(build_mcp_resources, 'source_digest', changed)
    with pytest.raises(PermissionError):
        build_mcp_resources.prepare(tmp_path / 'dist', tmp_path / 'resources')
    assert not (tmp_path / 'resources/mcp-providers.json').exists()

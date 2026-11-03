"""Selecting a local entry must not grant unrelated Core or adjacent files."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agent.tools.mcp.mcp_client import McpClient
from core.security.mcp_runtime import prepare_mcp


def test_native_entry_does_not_grant_read_access_to_its_parent_directory(tmp_path):
    package = tmp_path / 'downloaded-entry'
    package.mkdir()
    compiler = shutil.which('cc')
    if not compiler:
        pytest.skip('Native C compiler unavailable')
    source = package / 'entry.c'
    source.write_text('#include <stdio.h>\nint main(int argc,char**argv){char data[128];if(argc<2)return 2;FILE*f=fopen(argv[1],"rb");if(!f)return 3;size_t n=fread(data,1,sizeof(data),f);fclose(f);fwrite(data,1,n,stdout);return 0;}\n')
    executable = package / 'mcp-entry'
    built = subprocess.run([compiler, str(source), '-o', str(executable)], capture_output=True, text=True, timeout=30)
    assert built.returncode == 0, built.stderr
    secret = package / 'unrelated-private.txt'
    secret.write_text('UNRELATED_PRIVATE_CONTEXT')
    control = subprocess.run([str(executable), str(secret)], capture_output=True, text=True, timeout=5)
    assert control.returncode == 0 and control.stdout == 'UNRELATED_PRIVATE_CONTEXT'
    config = {'name': 'native-source-scope', 'command': str(executable), 'args': [str(secret)]}
    plan, _ = prepare_mcp(config, {})
    result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env,
                            capture_output=True, text=True, timeout=5)
    # Exit 3 is emitted by the running probe only when fopen is denied.
    # A loader/sandbox launch failure must not count as successful isolation.
    assert result.returncode == 3, result.stderr
    assert 'UNRELATED_PRIVATE_CONTEXT' not in result.stdout


def test_source_package_containing_registered_core_credentials_cannot_start(tmp_path, monkeypatch):
    package = tmp_path / 'source-with-core-state'
    package.mkdir()
    (package / 'config.json').write_text(json.dumps({'model_key': 'SYNTHETIC_CORE_CREDENTIAL'}))
    script = package / 'server.py'
    script.write_text(Path('tests/fixtures/mcp_echo_server.py').read_text())
    monkeypatch.setattr('config.get_data_root', lambda: str(package))
    client = McpClient({'name': 'source-with-core-state', 'command': sys.executable, 'args': [str(script)]})
    try:
        assert client.initialize() is False
        assert client._proc is None
        assert 'SYNTHETIC_CORE_CREDENTIAL' in (package / 'config.json').read_text()
    finally:
        client.shutdown()


@pytest.mark.parametrize('runtime', ['python', 'node'])
def test_dependency_preparation_rejects_private_source_before_build_or_copy(tmp_path, monkeypatch, runtime):
    from core.runtime.environments import EnvironmentStore
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'config.json').write_text('{"key":"SYNTHETIC_CORE_CREDENTIAL"}')
    monkeypatch.setattr('config.get_data_root', lambda: str(source))
    store = EnvironmentStore(tmp_path / 'environments')
    prepare = store.prepare_python if runtime == 'python' else store.prepare_node
    with pytest.raises(PermissionError, match='source|credential|private'):
        prepare(kind='mcp', identifier='private-source', user='alice', package=source, digest='source')
    assert not list(store.root.rglob('config.json'))


def test_local_plugin_staging_does_not_copy_registered_core_credentials(tmp_path, monkeypatch):
    from core.plugins.package_ops import PackageStore
    source = tmp_path / 'source'
    shutil.copytree('samples/plugins/alpha', source)
    (source / 'config.json').write_text('{"key":"SYNTHETIC_CORE_CREDENTIAL"}')
    monkeypatch.setattr('config.get_data_root', lambda: str(source))
    store = PackageStore(tmp_path / 'packages')
    with pytest.raises(PermissionError, match='source|credential|private'):
        store.stage(source)
    assert not list(store.staging.rglob('config.json'))
    assert (source / 'config.json').read_text() == '{"key":"SYNTHETIC_CORE_CREDENTIAL"}'

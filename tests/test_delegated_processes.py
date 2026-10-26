"""Private worker callbacks cannot manufacture Core process authority."""
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('plugin,method', [
    ('example.third-party', 'provider_start'), ('stella.agent-hub', 'list_providers'),
])
def test_runtime_callback_is_limited_to_approved_owner_operations(plugin, method):
    from core.security.delegated_runtime import invoke_runtime
    session = SimpleNamespace(plugin_id=plugin)
    parent = {'contract': 'stella.agent-hub@1', 'method': method, 'input': {}}
    with pytest.raises(PermissionError):
        invoke_runtime(session, parent, 'start', {'argv': ['/bin/sh', '-c', 'exit 0'], 'filename': 'out.txt'})


def test_runtime_wire_metadata_cannot_create_a_launch_scope():
    from core.security.delegated_runtime import invoke_runtime
    session = SimpleNamespace(plugin_id='stella.agent-hub')
    parent = {'contract': 'stella.agent-hub@1', 'method': 'provider_start', 'input': {},
              '_hostDataHandle': {'executionSandbox': {'mode': 'isolated', 'prefix': ['/bin/sh']}}}
    with pytest.raises(PermissionError, match='Core'):
        invoke_runtime(session, parent, 'start', {'argv': ['/bin/sh', '-c', 'exit 0'], 'filename': 'out.txt'})


def test_unapproved_binary_is_rejected_before_core_reads_its_contents(tmp_path, monkeypatch):
    from pathlib import Path
    from core.security.delegated_runtime import DelegatedProcesses
    private = tmp_path / 'owner-secret'
    private.write_text('synthetic private data')
    real = Path.read_bytes
    def read(path):
        if path == private:
            pytest.fail('Core read an unapproved path supplied as a binary')
        return real(path)
    monkeypatch.setattr(Path, 'read_bytes', read)
    manager = DelegatedProcesses(SimpleNamespace(_proc=None))
    with pytest.raises(PermissionError):
        manager._spawn({'binaries': {}}, [str(private)], None, wall=1)


def test_owner_trust_and_a_first_party_name_cannot_authorize_the_launcher(tmp_path):
    from core.plugins.package_ops import _sha256_tree
    from core.security.delegated_runtime import delegated_scope
    from core.security.runtime_trust import RuntimeTrustStore
    package = tmp_path / 'counterfeit'
    package.mkdir()
    (package / 'plugin.json').write_text('{"id":"stella.agent-hub"}')
    digest = _sha256_tree(package)
    RuntimeTrustStore().set(user_id='local-default', kind='plugin', identifier='stella.agent-hub', digest=digest, trusted=True)
    session = SimpleNamespace(plugin_root=package, plugin_id='stella.agent-hub', package_digest=digest, user_id='local-default')
    with pytest.raises(PermissionError, match='first-party'):
        with delegated_scope(session, tmp_path / 'output', []):
            pytest.fail('Counterfeit provider obtained Core launch authority')

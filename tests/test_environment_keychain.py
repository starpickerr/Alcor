"""Credential migration must publish verified references, never plaintext or stale keys."""
import os
from pathlib import Path
import sys

import pytest

from core.security.env_store import EnvironmentFile

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='Real macOS Keychain test')


def owner_file(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    result = EnvironmentFile()
    result._ensure_env_file()
    return result


def test_owner_save_replaces_plaintext_with_a_readable_keychain_reference(tmp_path, monkeypatch):
    store = owner_file(tmp_path, monkeypatch)
    path = Path(store.env_path)
    path.write_text('OWNER_TOKEN=ORIGINAL_SYNTHETIC_SECRET\n')
    store._write_env_file({'OWNER_TOKEN': 'ORIGINAL_SYNTHETIC_SECRET', 'EXTRA_TOKEN': 'SECOND_SYNTHETIC_SECRET'})
    assert 'ORIGINAL_SYNTHETIC_SECRET' not in path.read_text()
    assert 'SECOND_SYNTHETIC_SECRET' not in path.read_text()
    assert store._read_env_file() == {'OWNER_TOKEN': 'ORIGINAL_SYNTHETIC_SECRET', 'EXTRA_TOKEN': 'SECOND_SYNTHETIC_SECRET'}
    assert path.stat().st_mode & 0o777 == 0o600


def test_file_keychain_operations_disable_native_interaction_and_restore_it(tmp_path, isolated_keychain, monkeypatch):
    import ctypes as c
    backend = isolated_keychain(tmp_path / 'credentials')
    security = backend.security
    security.SecKeychainGetUserInteractionAllowed.argtypes = [c.POINTER(c.c_bool)]
    def interaction_allowed():
        state = c.c_bool()
        assert security.SecKeychainGetUserInteractionAllowed(c.byref(state)) == 0
        return state.value
    before = interaction_allowed()
    calls = []
    for name in ('SecItemAdd', 'SecItemCopyMatching', 'SecItemDelete'):
        original = getattr(security, name)
        def checked(*args, original=original, name=name):
            assert interaction_allowed() is False, 'File-based Keychain must never open an authentication dialog'
            calls.append(name)
            return original(*args)
        monkeypatch.setattr(security, name, checked)
    backend.put('test-only-item', 'SYNTHETIC_NONINTERACTIVE')
    assert backend.get('test-only-item') == 'SYNTHETIC_NONINTERACTIVE'
    assert backend.contains('test-only-item')
    backend.delete('test-only-item')
    assert calls == ['SecItemAdd', 'SecItemCopyMatching', 'SecItemCopyMatching', 'SecItemDelete']
    assert interaction_allowed() is before


def test_unavailable_keychain_keeps_old_file_and_environment(tmp_path, monkeypatch):
    store = owner_file(tmp_path, monkeypatch)
    path = Path(store.env_path)
    path.write_text('OWNER_TOKEN=ORIGINAL_SYNTHETIC_SECRET\n')
    monkeypatch.setenv('OWNER_TOKEN', 'ORIGINAL_SYNTHETIC_SECRET')
    def unavailable(path):
        raise PermissionError('Keychain unavailable')
    monkeypatch.setattr('core.security.env_store.keychain_for', unavailable, raising=False)
    with pytest.raises(PermissionError):
        store._write_env_file({'OWNER_TOKEN': 'REPLACEMENT_SYNTHETIC_SECRET'})
    assert path.read_text() == 'OWNER_TOKEN=ORIGINAL_SYNTHETIC_SECRET\n'
    assert os.environ['OWNER_TOKEN'] == 'ORIGINAL_SYNTHETIC_SECRET'


def test_initializer_resolves_saved_keychain_values(tmp_path, monkeypatch):
    from bridge.agent_initializer import AgentInitializer
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_INITIALIZER_SECRET'})
    assert 'SYNTHETIC_INITIALIZER_SECRET' not in Path(store.env_path).read_text()
    monkeypatch.setenv('OWNER_TOKEN', 'REPLACE_ON_LOAD')
    AgentInitializer.__new__(AgentInitializer)._load_env_file()
    assert os.environ['OWNER_TOKEN'] == 'SYNTHETIC_INITIALIZER_SECRET'


def test_failed_publication_preserves_previous_encrypted_value(tmp_path, monkeypatch):
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'})
    before = Path(store.env_path).read_bytes()
    assert b'SYNTHETIC_ORIGINAL' not in before
    def failed(*args):
        raise OSError('simulated atomic publish failure')
    monkeypatch.setattr('core.security.storage.private_atomic_write', failed)
    with pytest.raises(OSError):
        store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_REPLACEMENT'})
    assert Path(store.env_path).read_bytes() == before
    assert store._read_env_file() == {'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'}


def test_post_publication_io_failure_does_not_destroy_published_credentials(tmp_path, monkeypatch):
    from core.security.storage import private_atomic_write
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'})
    def uncertain(*args):
        private_atomic_write(*args)
        raise OSError('simulated directory fsync failure after rename')
    monkeypatch.setattr('core.security.storage.private_atomic_write', uncertain)
    with pytest.raises(OSError):
        store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_REPLACEMENT'})
    assert store._read_env_file()['OWNER_TOKEN'] in {'SYNTHETIC_ORIGINAL', 'SYNTHETIC_REPLACEMENT'}


def test_missing_reference_cannot_be_loaded_as_a_literal_or_leave_a_stale_secret(tmp_path, monkeypatch, isolated_keychain):
    from bridge.agent_initializer import AgentInitializer
    from core.security.env_store import _account, _entries
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'})
    reference = _entries(store.env_path)['OWNER_TOKEN']
    isolated_keychain(store.env_path).delete(_account('OWNER_TOKEN', reference))
    monkeypatch.setenv('OWNER_TOKEN', 'STALE_SYNTHETIC_SECRET')
    AgentInitializer.__new__(AgentInitializer)._load_env_file()
    assert 'OWNER_TOKEN' not in os.environ


def test_credential_references_cannot_be_copied_to_another_profile(tmp_path, monkeypatch):
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'})
    other = owner_file(tmp_path / 'other-profile', monkeypatch)
    Path(other.env_path).write_bytes(Path(store.env_path).read_bytes())
    with pytest.raises(PermissionError):
        other._read_env_file()


def test_credential_directory_uses_attributes_without_reading_secret_values(tmp_path, monkeypatch, isolated_keychain):
    from agent.permission.center import secrets
    from core.security.env_store import _account, _entries
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'})
    def forbidden(*args):
        raise AssertionError('Credential status must not retrieve a secret')
    monkeypatch.setattr('core.security.keychain.MacOSKeychain.get', forbidden)
    row = next(row for row in secrets() if row['name'] == 'OWNER_TOKEN')
    assert row['configured'] is True and 'SYNTHETIC_ORIGINAL' not in str(row)
    reference = _entries(store.env_path)['OWNER_TOKEN']
    isolated_keychain(store.env_path).delete(_account('OWNER_TOKEN', reference))
    assert next(row for row in secrets() if row['name'] == 'OWNER_TOKEN')['configured'] is False


def test_failed_owner_save_does_not_change_the_running_environment(tmp_path, monkeypatch):
    from agent.permission.center import set_secret
    store = owner_file(tmp_path, monkeypatch)
    Path(store.env_path).write_text('OWNER_TOKEN=ORIGINAL_SYNTHETIC_SECRET\n')
    monkeypatch.setenv('OWNER_TOKEN', 'ORIGINAL_SYNTHETIC_SECRET')
    def unavailable(path):
        raise PermissionError('Keychain unavailable')
    monkeypatch.setattr('core.security.env_store.keychain_for', unavailable)
    with pytest.raises(PermissionError):
        set_secret('OWNER_TOKEN', 'REPLACEMENT_SYNTHETIC_SECRET')
    assert os.environ['OWNER_TOKEN'] == 'ORIGINAL_SYNTHETIC_SECRET'


def test_hot_refresh_resolves_the_existing_workspace_credential_file(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from bridge.agent_bridge import AgentBridge
    from common.state_dir import env_file
    store = owner_file(tmp_path, monkeypatch)
    workspace = tmp_path / 'workspace'
    store.env_dir = str(workspace)
    store.env_path = str(env_file(base=workspace))
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_HOT_RELOAD'})
    agent = SimpleNamespace(workspace_dir=str(workspace))
    bridge = AgentBridge.__new__(AgentBridge)
    monkeypatch.setattr(bridge, 'iter_agent_instances', lambda: [('agent', 'session', agent)])
    monkeypatch.setattr(bridge, '_refresh_conditional_tools', lambda agent: None)
    monkeypatch.setenv('OWNER_TOKEN', 'REPLACE_ON_LOAD')
    bridge.refresh_all_skills()
    assert os.environ['OWNER_TOKEN'] == 'SYNTHETIC_HOT_RELOAD'


def test_empty_credential_remains_unconfigured(tmp_path, monkeypatch):
    from agent.permission.center import secrets
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': ''})
    assert store._read_env_file()['OWNER_TOKEN'] == ''
    assert next(row for row in secrets() if row['name'] == 'OWNER_TOKEN')['configured'] is False


def test_locked_native_keychain_does_not_prompt_or_replace_the_original_file(tmp_path, monkeypatch, isolated_keychain):
    import ctypes as c
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'})
    before = Path(store.env_path).read_bytes()
    backend = isolated_keychain(store.env_path)
    backend.security.SecKeychainLock.argtypes = [c.c_void_p]
    assert backend.security.SecKeychainLock(backend.keychain) == 0
    with pytest.raises(PermissionError):
        store._read_env_file()
    with pytest.raises(PermissionError):
        store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_REPLACEMENT'})
    assert Path(store.env_path).read_bytes() == before


def test_untrusted_worker_cannot_read_keychain_even_with_ciphertext_file_access(tmp_path, monkeypatch, isolated_keychain):
    import subprocess
    from core.security.env_store import _account, _entries
    from core.security.extension_sandbox import prepare_extension
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_ORIGINAL'})
    reference = _entries(store.env_path)['OWNER_TOKEN']
    package = tmp_path / 'worker-code'
    package.mkdir()
    keychain_path = tmp_path / 'test.keychain-db'
    assert keychain_path.exists()
    code = '''import ctypes as c,sys
from core.security.keychain import MacOSKeychain
backend=MacOSKeychain(sys.argv[1])
backend.security.SecKeychainOpen.argtypes=[c.c_char_p,c.POINTER(c.c_void_p)]
ref=c.c_void_p()
try:
 status=backend.security.SecKeychainOpen(sys.argv[2].encode(),c.byref(ref))
 if status: raise PermissionError('isolated keychain open blocked')
 backend.keychain=ref
 value=backend.get(sys.argv[3])
 print('blocked' if value is None else value)
except PermissionError:
 print('blocked')
'''
    plan = prepare_extension([sys.executable, '-c', code, store.env_path, str(keychain_path),
                              _account('OWNER_TOKEN', reference)], package, tmp_path / 'worker-data', {},
                              executable_reads=[keychain_path])
    result = subprocess.run(plan.argv, cwd=plan.cwd, env=plan.env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'blocked'
    assert store._read_env_file()['OWNER_TOKEN'] == 'SYNTHETIC_ORIGINAL'


def test_owner_can_repair_a_missing_reference_without_reading_other_credentials(tmp_path, monkeypatch, isolated_keychain):
    from agent.permission.center import set_secret
    from core.security.env_store import _entries, _account
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_OLD', 'OTHER_TOKEN': 'SYNTHETIC_OTHER'})
    before = _entries(store.env_path)
    backend = isolated_keychain(store.env_path)
    backend.delete(_account('OWNER_TOKEN', before['OWNER_TOKEN']))
    # Register the old process state for teardown before exercising the real API.
    monkeypatch.setenv('OWNER_TOKEN', 'SYNTHETIC_STALE')
    set_secret('OWNER_TOKEN', 'SYNTHETIC_NEW')
    assert store._read_env_file() == {'OWNER_TOKEN': 'SYNTHETIC_NEW', 'OTHER_TOKEN': 'SYNTHETIC_OTHER'}
    assert _entries(store.env_path)['OTHER_TOKEN'] == before['OTHER_TOKEN']
    assert os.environ['OWNER_TOKEN'] == 'SYNTHETIC_NEW'


def test_owner_can_remove_a_missing_reference_and_preserve_other_unavailable_items(tmp_path, monkeypatch, isolated_keychain):
    from agent.permission.center import set_secret
    from core.security.env_store import _entries, _account
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_OLD', 'OTHER_TOKEN': 'SYNTHETIC_OTHER'})
    before = _entries(store.env_path)
    backend = isolated_keychain(store.env_path)
    for name, reference in before.items():
        backend.delete(_account(name, reference))
    monkeypatch.setenv('OWNER_TOKEN', 'SYNTHETIC_STALE')
    set_secret('OWNER_TOKEN', None)
    assert _entries(store.env_path) == {'OTHER_TOKEN': before['OTHER_TOKEN']}
    assert 'OWNER_TOKEN' not in os.environ


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write', 'full-access'])
def test_shell_cannot_decrypt_keychain_in_any_execution_mode(tmp_path, monkeypatch, mode):
    import shlex
    import shutil
    import subprocess
    from agent.permission import process_sandbox
    from agent.permission.security_policy import PRESETS
    from core.security.env_store import _account, _entries
    store = owner_file(tmp_path, monkeypatch)
    store._write_env_file({'OWNER_TOKEN': 'SYNTHETIC_SHELL_SECRET'})
    reference = _entries(store.env_path)['OWNER_TOKEN']
    code = '''import ctypes as c,sys,importlib.util
spec=importlib.util.spec_from_file_location('keychain_probe',sys.argv[4])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
backend=module.MacOSKeychain(sys.argv[1])
backend.security.SecKeychainOpen.argtypes=[c.c_char_p,c.POINTER(c.c_void_p)]
ref=c.c_void_p()
try:
 status=backend.security.SecKeychainOpen(sys.argv[2].encode(),c.byref(ref))
 if status: raise PermissionError('blocked')
 backend.keychain=ref
 print(backend.get(sys.argv[3]) or 'blocked')
except PermissionError: print('blocked')
from pathlib import Path
print(Path('ordinary.txt').read_text())
'''
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'ordinary.txt').write_text('ordinary-workspace-readable')
    command = shlex.join([sys.executable, '-c', code, store.env_path, str(tmp_path / 'test.keychain-db'),
                          _account('OWNER_TOKEN', reference), str(Path('core/security/keychain.py').resolve())])
    env = {**os.environ, 'PYTHONPATH': str(Path.cwd())}
    # The ciphertext file is explicitly inside the allowed read scope. This
    # must still fail at the OS service boundary, independently of file guards.
    plan = process_sandbox.prepare(command, str(workspace), [str(tmp_path)],
                                    {'preset': mode, 'rules': PRESETS[mode]}, env)
    try:
        result = subprocess.run(plan.argv, shell=plan.shell, cwd=workspace, env=env,
                                capture_output=True, text=True, timeout=10)
    finally:
        if plan.temp_dir:
            shutil.rmtree(plan.temp_dir)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ['blocked', 'ordinary-workspace-readable']
    assert store._read_env_file()['OWNER_TOKEN'] == 'SYNTHETIC_SHELL_SECRET'


def test_full_access_agent_file_tool_cannot_read_os_keychain_storage(tmp_path, monkeypatch):
    from agent.tools.read.read import Read
    from common.runtime_identity import identity_scope
    folder = tmp_path / 'Library/Keychains'
    folder.mkdir(parents=True)
    private = folder / 'synthetic.keychain-db'
    private.write_text('SYNTHETIC_KEYCHAIN_DATABASE')
    monkeypatch.setenv('HOME', str(tmp_path))
    from core.security.run_policy import save_run_policy
    save_run_policy('session', 'full-access-keychain', {'filesystem_mode': 'full-access'})
    with identity_scope(session_id='full-access-keychain'):
        result = Read().execute({'path': str(private)})
    assert result.status == 'error'
    assert 'SYNTHETIC_KEYCHAIN_DATABASE' not in str(result.result)

"""Protected folders constrain actual managed execution, not the trusted host."""
import os
from pathlib import Path
import subprocess
import shutil
import shlex
import sys

import pytest

from common.runtime_identity import identity_scope


def control():
    from core.security.protected_paths import ProtectedPathControl
    return ProtectedPathControl()


def protect(paths):
    owner = control()
    return owner.replace([str(path) for path in paths], expected_revision=owner.inventory()['revision'])


def test_owner_rules_block_files_and_aliases_but_allow_adjacent_paths(tmp_path):
    from agent.tools.read.read import Read
    secret = tmp_path / 'private'
    sibling = tmp_path / 'private-sibling'
    secret.mkdir()
    sibling.mkdir()
    (secret / 'content.txt').write_text('SYNTHETIC_PROTECTED_CONTENT')
    (sibling / 'content.txt').write_text('ordinary allowed content')
    alias = tmp_path / 'alias.txt'
    alias.symlink_to(secret / 'content.txt')
    protect([secret])
    reader = Read({'cwd': str(tmp_path)})
    for path in (secret / 'content.txt', alias):
        result = reader.execute({'path': str(path)})
        assert result.status == 'error'
        assert 'SYNTHETIC_PROTECTED_CONTENT' not in str(result)
    assert reader.execute({'path': str(sibling / 'content.txt')}).status == 'success'


def test_replaced_rule_root_requires_repair_and_owner_can_rebind_original_data(tmp_path):
    from core.security.protected_paths import check_path
    secret = tmp_path / 'private'
    secret.mkdir()
    (secret / 'content.txt').write_text('KEEP')
    saved = protect([secret])
    moved = tmp_path / 'moved-private'
    secret.rename(moved)
    secret.mkdir()
    with pytest.raises(PermissionError, match='repair|changed|replaced'):
        check_path(tmp_path / 'ordinary.txt')
    assert control().inventory()['rules'][0]['status'] == 'needs-repair'
    control().replace([str(moved)], expected_revision=saved['revision'])
    with pytest.raises(PermissionError):
        check_path(moved / 'content.txt')
    check_path(secret / 'new.txt')
    assert (moved / 'content.txt').read_text() == 'KEEP'


def test_protection_cannot_conflict_with_runtime_policy_or_credentials(tmp_path):
    from core.security.policy_store import policy_path
    from config import get_data_root
    for path in (Path(sys.prefix), Path(__file__).resolve().parents[1], policy_path().parent, Path(get_data_root())):
        with pytest.raises(ValueError, match='required|runtime|credential|policy|conflict'):
            protect([path])
    assert control().inventory()['rules'] == []


def test_remove_rule_keeps_files_and_stale_edits_cannot_overwrite_policy(tmp_path):
    from core.security.protected_paths import check_path
    secret = tmp_path / 'private'
    secret.mkdir()
    target = secret / 'keep.txt'
    target.write_text('KEEP_OWNER_FILE')
    saved = protect([secret])
    with pytest.raises(ValueError, match='revision|changed'):
        control().replace([], expected_revision=saved['revision'] - 1)
    control().replace([], expected_revision=saved['revision'])
    check_path(target)
    assert target.read_text() == 'KEEP_OWNER_FILE'


def test_agent_cannot_manage_or_enumerate_protected_folders(tmp_path):
    with identity_scope(session_id='ordinary-agent'):
        with pytest.raises(PermissionError):
            control().inventory()
        with pytest.raises(PermissionError):
            control().replace([str(tmp_path)], expected_revision=0)


def test_interrupted_policy_change_blocks_new_operations_and_can_retry(tmp_path, monkeypatch):
    from core.security import protected_paths
    secret = tmp_path / 'private'
    secret.mkdir()
    original = protected_paths.revoke_managed
    def interrupted():
        raise OSError('Synthetic draining failure')
    monkeypatch.setattr(protected_paths, 'revoke_managed', interrupted)
    with pytest.raises(OSError):
        protect([secret])
    pending = control().inventory()
    assert pending['phase'] == 'changing'
    with pytest.raises(PermissionError):
        protected_paths.check_path(tmp_path / 'ordinary.txt')
    monkeypatch.setattr(protected_paths, 'revoke_managed', original)
    saved = control().replace([str(secret)], expected_revision=pending['revision'])
    assert saved['phase'] == 'ready'
    protected_paths.check_path(tmp_path / 'ordinary.txt')


@pytest.mark.skipif(sys.platform != 'darwin', reason='Delivery verifies real macOS Seatbelt')
@pytest.mark.parametrize('mode', ['read-only', 'workspace-write', 'full-access'])
def test_real_shell_rejects_protected_content_and_allows_ordinary_files(tmp_path, mode):
    from agent.permission.process_sandbox import prepare
    from agent.permission.security_policy import legacy_policy
    from core.security.protected_paths import launch_guard
    from core.workspace.service import resolve_context
    public = tmp_path / 'work'
    secret = public / 'private'
    secret.mkdir(parents=True)
    (secret / 'secret.txt').write_text('SYNTHETIC_PROTECTED_CONTENT')
    (public / 'ordinary.txt').write_text('ALLOWED_NORMAL_OPERATION')
    (public / 'alias.txt').symlink_to(secret / 'secret.txt')
    protect([secret])
    context = resolve_context(None)
    env = {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'}
    command = '/bin/cat private/secret.txt; /bin/cat alias.txt; /bin/ln private/secret.txt linked.txt; /bin/cat linked.txt; '
    command += 'printf BLOCKED > private/secret.txt; /bin/mv ../work ../escape; printf WRITE_OK > new.txt; /bin/cat ordinary.txt'
    plan = prepare(command, str(public), [str(public)],
                   legacy_policy(mode), env, context=context)
    try:
        with launch_guard(plan.protection_revision) as launch:
            process = subprocess.Popen(plan.argv, cwd=public, env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True, start_new_session=True)
            launch.bind(process)
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 0, stderr
        assert stdout == 'ALLOWED_NORMAL_OPERATION'
        assert 'SYNTHETIC_PROTECTED_CONTENT' not in stdout + stderr
        assert (secret / 'secret.txt').read_text() == 'SYNTHETIC_PROTECTED_CONTENT'
        if mode == 'read-only':
            assert not (public / 'new.txt').exists()
        else:
            assert (public / 'new.txt').read_text() == 'WRITE_OK'
    finally:
        if plan.temp_dir:
            shutil.rmtree(plan.temp_dir)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Real extension Seatbelt boundary')
def test_real_extension_can_use_its_granted_directory_but_not_protected_descendants(tmp_path):
    from core.security.extension_sandbox import prepare_extension
    from core.security.protected_paths import launch_guard
    package = tmp_path / 'extension'
    storage = tmp_path / 'storage'
    public = tmp_path / 'allowed'
    secret = public / 'private'
    package.mkdir()
    secret.mkdir(parents=True)
    (secret / 'secret.txt').write_text('SYNTHETIC_PROTECTED_CONTENT')
    (public / 'ordinary.txt').write_text('ALLOWED_EXTENSION_READ')
    protect([secret])
    command = f'/bin/cat {shlex.quote(str(secret / "secret.txt"))}; printf WRITE_OK > {shlex.quote(str(public / "new.txt"))}; /bin/cat {shlex.quote(str(public / "ordinary.txt"))}'
    plan = prepare_extension(['/bin/sh', '-c', command], package, storage, {'PATH': '/usr/bin:/bin'},
                             read_roots=[public], write_roots=[public])
    with launch_guard(plan.protection_revision) as launch:
        process = subprocess.Popen(plan.argv, cwd=plan.cwd, env=plan.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True)
        launch.bind(process)
    stdout, stderr = process.communicate(timeout=8)
    assert process.returncode == 0, stderr
    assert stdout == 'ALLOWED_EXTENSION_READ'
    assert (public / 'new.txt').read_text() == 'WRITE_OK'
    assert 'SYNTHETIC_PROTECTED_CONTENT' not in stdout + stderr


def test_plan_from_an_older_policy_cannot_launch_after_protection_changes(tmp_path):
    from core.security.protected_paths import current_revision, launch_guard
    revision = current_revision()
    secret = tmp_path / 'private'
    secret.mkdir()
    protect([secret])
    with pytest.raises(PermissionError, match='changed'):
        with launch_guard(revision):
            pytest.fail('Stale launch admission must fail before any process starts')


@pytest.mark.skipif(sys.platform != 'darwin', reason='Real managed process reclamation')
def test_policy_change_stops_an_existing_managed_process_and_does_not_revive_it(tmp_path):
    from core.security.protected_paths import launch_guard, current_revision
    secret = tmp_path / 'private'
    secret.mkdir()
    with launch_guard(current_revision()) as launch:
        process = subprocess.Popen(['/bin/sleep', '30'], start_new_session=True)
        launch.bind(process)
    try:
        assert process.poll() is None
        protect([secret])
        process.wait(timeout=4)
        assert process.poll() is not None
        protect([])
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            os.killpg(process.pid, 9)
            process.wait()

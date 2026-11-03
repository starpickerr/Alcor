import os
import shutil
import subprocess
import sys

import pytest

from agent.permission import process_sandbox
from agent.permission.security_policy import default_policy, PRESETS


def _run(plan, cwd, env):
    try:
        return subprocess.run(plan.argv, shell=plan.shell, cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=10)
    finally:
        if plan.temp_dir:
            shutil.rmtree(plan.temp_dir, ignore_errors=True)


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="sandbox backend unavailable")
def test_project_command_cannot_write_outside_actual_sandbox(tmp_path):
    if process_sandbox.capability()["status"] != "enabled":
        pytest.skip("OS sandbox not available")
    work = tmp_path / "project"
    work.mkdir()
    inside, outside = work / "inside.txt", tmp_path / "outside.txt"
    env = os.environ.copy()
    plan = process_sandbox.prepare(f"touch {inside}", str(work), [str(work)], default_policy(), env)
    assert _run(plan, str(work), env).returncode == 0
    assert inside.exists()

    env = os.environ.copy()
    plan = process_sandbox.prepare(f"touch {outside}", str(work), [str(work)], default_policy(), env)
    assert _run(plan, str(work), env).returncode != 0
    assert not outside.exists()


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='sandbox backend unavailable')
def test_workspace_shell_cannot_read_ungranted_outside_file(tmp_path):
    if process_sandbox.capability()['status'] != 'enabled':
        pytest.skip('OS sandbox not available')
    work = tmp_path / 'workspace'
    work.mkdir()
    secret = tmp_path / 'owner-private.txt'
    secret.write_text('PRIVATE_OWNER')
    env = os.environ.copy()
    plan = process_sandbox.prepare(f'/bin/cat "{secret}"', str(work), [str(work)], default_policy(), env)
    result = _run(plan, str(work), env)
    assert result.returncode != 0
    assert 'PRIVATE_OWNER' not in result.stdout


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='sandbox backend unavailable')
def test_full_access_shell_cannot_read_raw_business_database(tmp_path, monkeypatch):
    if process_sandbox.capability()['status'] != 'enabled':
        pytest.skip('OS sandbox not available')
    data = tmp_path / 'user-data'
    data.mkdir()
    secret = data / 'tasks.sqlite'
    secret.write_text('BUSINESS_BODY')
    monkeypatch.setattr('common.state_dir.user_root', lambda *args, **kwargs: data)
    work = tmp_path / 'workspace'
    work.mkdir()
    env = os.environ.copy()
    plan = process_sandbox.prepare(f'/bin/cat "{secret}"', str(work), [str(work)],
        {'preset': 'full-access', 'rules': PRESETS['full-access']}, env)
    result = _run(plan, str(work), env)
    assert result.returncode != 0
    assert 'BUSINESS_BODY' not in result.stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt-specific Unicode paths")
def test_seatbelt_can_write_unicode_project_only(tmp_path):
    if process_sandbox.capability()["status"] != "enabled":
        pytest.skip("Seatbelt unavailable")
    work = tmp_path / "课程资料"
    work.mkdir()
    inside, outside = work / "new.txt", tmp_path / "outside.txt"
    env = os.environ.copy()
    plan = process_sandbox.prepare(f"touch '{inside}'", str(work), [str(work)], default_policy(), env)
    assert _run(plan, str(work), env).returncode == 0
    assert inside.exists()
    env = os.environ.copy()
    plan = process_sandbox.prepare(f"touch '{outside}'", str(work), [str(work)], default_policy(), env)
    assert _run(plan, str(work), env).returncode != 0
    assert not outside.exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt-specific exact path grant")
def test_seatbelt_approval_opens_only_named_outside_file(tmp_path):
    if process_sandbox.capability()["status"] != "enabled":
        pytest.skip("Seatbelt unavailable")
    work = tmp_path / "project"
    work.mkdir()
    allowed, sibling = tmp_path / "approved.txt", tmp_path / "sibling.txt"
    env = os.environ.copy()
    plan = process_sandbox.prepare(f"touch {allowed}", str(work), [str(work)], default_policy(), env,
                                   approved_target=str(allowed))
    assert _run(plan, str(work), env).returncode == 0
    assert allowed.exists()
    env = os.environ.copy()
    plan = process_sandbox.prepare(f"touch {sibling}", str(work), [str(work)], default_policy(), env,
                                   approved_target=str(allowed))
    assert _run(plan, str(work), env).returncode != 0
    assert not sibling.exists()


def test_unavailable_sandbox_fails_closed_for_project_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(process_sandbox, "capability", lambda: {"status": "unavailable", "reason": "probe failed"})
    with pytest.raises(process_sandbox.SandboxUnavailable, match="probe failed"):
        process_sandbox.prepare("true", str(tmp_path), [str(tmp_path)], default_policy(), {})

    with pytest.raises(process_sandbox.SandboxUnavailable, match="probe failed"):
        process_sandbox.prepare("true", str(tmp_path), [str(tmp_path)],
                                {"preset": "full-access", "rules": PRESETS["full-access"]}, {})


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="sandbox backend unavailable")
def test_full_access_shell_child_cannot_read_other_private_work(tmp_path, monkeypatch):
    if process_sandbox.capability()["status"] != "enabled":
        pytest.skip("OS sandbox not available")
    from agent.workspace import project_store
    from agent.workspace.context import resolve_context
    from agent.workspace.shares import create_share, revoke_share

    work = tmp_path / "work"
    monkeypatch.setattr("agent.workspace.context.personal_work_dir", lambda **kwargs: work)
    monkeypatch.setattr("agent.workspace.shares._manifest_file", lambda: tmp_path / "shares.json")
    monkeypatch.setattr(project_store, "_store_file", lambda: str(tmp_path / "projects.json"))
    owner = resolve_context("owner", "alpha", ensure=True)
    recipient = resolve_context("recipient", "alpha", ensure=True)
    secret = owner.root / "secret.txt"
    own = recipient.root / "own.txt"
    secret.write_text("SECRET")
    own.write_text("OWN")
    shared = create_share("owner", "alpha", "secret.txt", "recipient", "read")
    policy = {"preset": "full-access", "rules": PRESETS["full-access"]}

    def run_cat(path):
        env = os.environ.copy()
        plan = process_sandbox.prepare(f"/bin/cat '{path}'", str(recipient.root),
                                       [str(recipient.root)], policy, env, context=recipient)
        return _run(plan, str(recipient.root), env)

    assert run_cat(own).stdout.strip() == "OWN"
    assert run_cat(secret).returncode != 0
    assert run_cat(shared["path"]).stdout.strip() == "SECRET"
    sibling = owner.root / "sibling.txt"
    sibling.write_text("HIDDEN")
    assert run_cat(sibling).returncode != 0
    env = os.environ.copy()
    plan = process_sandbox.prepare(f"/bin/sh -c 'echo CHANGED > {shared['path']}'", str(recipient.root),
                                   [str(recipient.root)], policy, env, context=recipient)
    assert _run(plan, str(recipient.root), env).returncode != 0
    assert open(shared["path"]).read() == "SECRET"
    revoke_share(shared["id"], "owner", "alpha")
    assert run_cat(shared["path"]).returncode != 0


@pytest.mark.skipif(sys.platform not in ("darwin", "linux"), reason="sandbox backend unavailable")
def test_revoking_share_terminates_running_shell_with_old_grant(tmp_path, monkeypatch):
    if process_sandbox.capability()["status"] != "enabled":
        pytest.skip("OS sandbox not available")
    from agent.workspace import project_store
    from agent.workspace.context import resolve_context
    from agent.workspace.shares import create_share, revoke_share

    work = tmp_path / "work"
    monkeypatch.setattr("agent.workspace.context.personal_work_dir", lambda **kwargs: work)
    monkeypatch.setattr("agent.workspace.shares._manifest_file", lambda: tmp_path / "shares.json")
    monkeypatch.setattr(project_store, "_store_file", lambda: str(tmp_path / "projects.json"))
    owner = resolve_context("owner", "alpha", ensure=True)
    recipient = resolve_context("recipient", "alpha", ensure=True)
    (owner.root / "file.txt").write_text("data")
    shared = create_share("owner", "alpha", "file.txt", "recipient", "read")
    env = os.environ.copy()
    policy = {"preset": "full-access", "rules": PRESETS["full-access"]}
    plan = process_sandbox.prepare("sleep 30", str(recipient.root), [str(recipient.root)],
                                   policy, env, context=recipient)
    process = subprocess.Popen(plan.argv, cwd=str(recipient.root), env=env,
                               start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        process_sandbox.register_share_process(process, plan)
        assert process.poll() is None
        revoke_share(shared["id"], "owner", "alpha")
        process.wait(timeout=5)
        assert process.returncode != 0
    finally:
        process_sandbox.unregister_share_process(process)
        if process.poll() is None:
            os.killpg(process.pid, __import__("signal").SIGKILL)
            process.wait()
        if plan.temp_dir:
            shutil.rmtree(plan.temp_dir, ignore_errors=True)


@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='sandbox backend unavailable')
def test_full_access_shell_cannot_read_plugin_email_credentials_or_copies(tmp_path, monkeypatch):
    import shlex
    from common import state_dir
    if process_sandbox.capability()['status'] != 'enabled':
        pytest.skip('OS sandbox not available')
    monkeypatch.setattr(state_dir, 'briefing_db', lambda **_: tmp_path / 'briefing/briefing.db')
    monkeypatch.setattr(state_dir, 'shared_root', lambda: tmp_path / 'shared')
    work = tmp_path / 'work'
    work.mkdir()
    targets = [tmp_path / 'email/email.db', tmp_path / 'email/email.db-wal',
               tmp_path / 'email/.stella-retired-test',
               tmp_path / 'shared/plugin-packages/management/backups/op/email.db']
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('FAKE_CREDENTIAL_FOR_SANDBOX_TEST')
        for command in (f'cat {shlex.quote(str(target))}',
                        f'{shlex.quote(sys.executable)} -c {shlex.quote("import pathlib; print(pathlib.Path(" + repr(str(target)) + ").read_text())")}'):
            env = os.environ.copy()
            plan = process_sandbox.prepare(command, str(work), [str(work)],
                                           {'preset': 'full-access', 'rules': PRESETS['full-access']}, env)
            result = _run(plan, str(work), env)
            assert result.returncode != 0
            assert 'FAKE_CREDENTIAL_FOR_SANDBOX_TEST' not in result.stdout


def test_full_access_shell_has_no_direct_network(tmp_path):
    import shlex
    if process_sandbox.capability()['status'] != 'enabled':
        pytest.skip('OS sandbox not available')
    command = shlex.join([sys.executable, '-I', '-c', "import socket; s=socket.socket(); s.bind(('127.0.0.1',0))"])
    env = os.environ.copy()
    plan = process_sandbox.prepare(command, str(tmp_path), [str(tmp_path)],
        {'preset': 'full-access', 'rules': PRESETS['full-access']}, env)
    assert _run(plan, str(tmp_path), env).returncode != 0


def test_shared_runtime_is_readable_but_not_mutable_even_inside_write_roots(tmp_path, monkeypatch):
    import shlex
    if process_sandbox.capability()['status'] != 'enabled':
        pytest.skip('OS sandbox not available')
    runtime = tmp_path / 'shared-runtime'
    runtime.mkdir()
    target = runtime / 'base.txt'
    target.write_text('VERIFIED_BASE')
    monkeypatch.setattr(process_sandbox, 'runtime_readonly_roots', lambda: [str(runtime)], raising=False)
    env = os.environ.copy()
    script = 'from pathlib import Path; p=Path(' + repr(str(target)) + '); print(p.read_text()); p.write_text("poisoned")'
    plan = process_sandbox.prepare(shlex.join([sys.executable, '-I', '-c', script]), str(tmp_path), [str(tmp_path)],
        {'preset': 'full-access', 'rules': PRESETS['full-access']}, env)
    result = _run(plan, str(tmp_path), env)
    assert 'VERIFIED_BASE' in result.stdout
    assert result.returncode != 0
    assert target.read_text() == 'VERIFIED_BASE'


@pytest.mark.parametrize('mode', ['read-only', 'workspace-write'])
def test_delegated_agents_cannot_use_raw_network_even_when_network_operations_are_allowed(tmp_path, mode):
    import subprocess
    import sys
    from common.runtime_identity import identity_scope
    from core.security.run_policy import resolve_run_policy, save_run_policy
    from core.security.delegated_runtime import prepare_delegated
    with identity_scope(session_id='delegated-network'):
        resolve_run_policy('delegated-network')
        save_run_policy('session', 'delegated-network', {'permission_mode': 'custom', 'filesystem_mode': mode, 'rules': {'network': 'allow'}})
        plan = prepare_delegated(tmp_path, [sys.executable])
        result = subprocess.run([*plan['prefix'], sys.executable, '-c',
            "import socket\ntry:\n s=socket.socket();s.bind(('127.0.0.1',0));print('raw-network')\nexcept OSError:\n print('denied')"],
            env=plan['env'], cwd=tmp_path, capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'denied'


def test_full_access_shell_cannot_read_oauth_tokens_or_staging_files(tmp_path, monkeypatch):
    home = tmp_path / 'owner'
    work = tmp_path / 'project'
    work.mkdir()
    private = home / '.stella'
    private.mkdir(parents=True)
    monkeypatch.setenv('HOME', str(home))
    for name in ('mcp_oauth.json', 'mcp_oauth.json.tmp'):
        path = private / name
        path.write_text('SYNTHETIC_OAUTH_TOKEN')
        env = os.environ.copy()
        plan = process_sandbox.prepare(f'/bin/cat "{path}"', str(work), [str(work)],
            {'preset': 'full-access', 'rules': PRESETS['full-access']}, env)
        result = _run(plan, str(work), env)
        assert result.returncode != 0
        assert 'SYNTHETIC_OAUTH_TOKEN' not in result.stdout

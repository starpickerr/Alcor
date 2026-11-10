import os
import shutil
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from agent.tools.bash.bash import Bash


posix_only = pytest.mark.skipif(Bash._IS_WIN, reason="POSIX shell command")
windows_only = pytest.mark.skipif(not Bash._IS_WIN, reason="Windows integration test")


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux mount namespace')
def test_linux_sandbox_preserves_system_directory_aliases(tmp_path):
    from agent.permission import process_sandbox
    from agent.permission.security_policy import default_policy

    env = os.environ.copy()
    plan = process_sandbox.prepare('printf aliases', str(tmp_path), [str(tmp_path)], default_policy(), env)
    try:
        for path in ('/bin', '/sbin', '/lib', '/lib64'):
            if os.path.islink(path):
                assert ['--symlink', os.readlink(path), path] in [plan.argv[i:i + 3] for i in range(len(plan.argv))]
        result = subprocess.run(plan.argv, shell=plan.shell, cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        assert result.stdout == 'aliases'
    finally:
        shutil.rmtree(plan.temp_dir)


def _windows_pid_command(pid_file):
    path = str(pid_file).replace("\\", "\\\\")
    return (
        f'"{sys.executable}" -c '
        f'"import os,time;open(r\'{path}\',\'w\').write(str(os.getpid()));time.sleep(30)"'
    )


def _windows_pid_is_running(pid):
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        errors="replace",
    )
    return f'"{pid}"' in result.stdout


def _windows_kill_pid_file(pid_file):
    if pid_file.exists():
        subprocess.run(
            ["taskkill", "/F", "/PID", pid_file.read_text()],
            capture_output=True,
        )


@posix_only
def test_fast_command_returns_output_without_progress(tmp_path):
    tool = Bash({"cwd": str(tmp_path)})
    progress = []
    tool.progress_callback = progress.append

    result = tool.execute({"command": "printf fast"})

    assert result.status == "success"
    assert result.result["output"] == "fast"
    assert progress == []


@posix_only
def test_timeout_returns_promptly(tmp_path):
    tool = Bash({"cwd": str(tmp_path)})
    started = time.monotonic()

    result = tool.execute({"command": "sleep 10", "timeout": 1})

    assert result.status == "error"
    assert "timed out after 1 seconds" in result.result
    assert time.monotonic() - started < 3


@posix_only
def test_cancel_stops_a_running_command(tmp_path):
    """A user cancel must not wait out the remaining timeout."""
    tool = Bash({"cwd": str(tmp_path)})
    tool.cancel_event = threading.Event()
    threading.Timer(0.5, tool.cancel_event.set).start()
    started = time.monotonic()

    result = tool.execute({"command": "sleep 30", "timeout": 60})

    assert result.status == "error"
    assert "cancelled" in result.result
    assert time.monotonic() - started < 5


@posix_only
def test_background_process_holding_pipe_does_not_hang(tmp_path):
    tool = Bash({"cwd": str(tmp_path)})
    started = time.monotonic()

    result = tool.execute({"command": "sleep 10 & printf done", "timeout": 3})

    assert result.status == "success"
    assert result.result["output"] == "done"
    assert time.monotonic() - started < 7


@posix_only
def test_output_without_trailing_newline_streams_progress(tmp_path):
    tool = Bash({"cwd": str(tmp_path)})
    progress = []
    tool.progress_callback = progress.append

    result = tool.execute({
        "command": "for i in 1 2 3 4 5; do printf .; sleep 0.3; done",
        "timeout": 5,
    })

    assert result.status == "success"
    assert result.result["output"] == "....."
    assert progress
    assert progress[-1].endswith(".")


def test_windows_kill_uses_taskkill_for_process_tree():
    tool = Bash()
    process = type("Process", (), {"pid": 1234, "poll": lambda self: None, "kill": lambda self: None})()

    with patch.object(tool, "_IS_WIN", True), patch.object(subprocess, "run") as run:
        run.return_value.returncode = 0
        tool._kill_process(process)

    run.assert_called_once_with(
        ["taskkill", "/F", "/T", "/PID", "1234"],
        capture_output=True,
        timeout=5,
    )


def test_windows_kill_falls_back_when_taskkill_fails():
    tool = Bash()
    process = type("Process", (), {"pid": 1234, "poll": lambda self: None, "kill": lambda self: None})()

    with patch.object(tool, "_IS_WIN", True), \
            patch.object(subprocess, "run", side_effect=OSError), \
            patch.object(process, "kill") as kill:
        tool._kill_process(process)

    kill.assert_called_once()


@windows_only
def test_windows_timeout_kills_process_tree(tmp_path):
    pid_file = tmp_path / "timeout-child.pid"
    child_command = _windows_pid_command(pid_file)
    tool = Bash({"cwd": str(tmp_path)})
    started = time.monotonic()

    try:
        with pytest.raises(subprocess.TimeoutExpired):
            tool._run_streaming(
                f'start "" /b {child_command} & ping -n 30 127.0.0.1 >nul',
                1, os.environ.copy(), {},
            )

        assert time.monotonic() - started < 8
        assert pid_file.exists()
        time.sleep(0.5)
        assert not _windows_pid_is_running(pid_file.read_text())
    finally:
        _windows_kill_pid_file(pid_file)


@windows_only
def test_windows_long_running_command_streams_progress(tmp_path):
    tool = Bash({"cwd": str(tmp_path)})
    progress = []
    tool.progress_callback = progress.append

    result = tool._run_streaming(
        f'"{sys.executable}" -u -c '
        '"import sys,time; '
        "[(sys.stdout.write('.'),sys.stdout.flush(),time.sleep(0.3)) "
        'for _ in range(5)]"',
        5, os.environ.copy(), {},
    )

    assert result.returncode == 0
    assert result.stdout == "....."
    assert progress
    assert progress[-1].endswith(".")


@windows_only
def test_windows_background_process_holding_pipe_does_not_hang(tmp_path):
    pid_file = tmp_path / "background-child.pid"
    child_command = _windows_pid_command(pid_file)
    tool = Bash({"cwd": str(tmp_path)})
    started = time.monotonic()

    try:
        result = tool._run_streaming(
            f'start "" /b {child_command} & echo done',
            3, os.environ.copy(), {},
        )

        assert result.returncode == 0
        assert result.stdout.strip() == "done"
        assert time.monotonic() - started < 8
        assert pid_file.exists()
    finally:
        _windows_kill_pid_file(pid_file)


@windows_only
def test_windows_public_command_fails_closed_without_process_isolation(tmp_path):
    tool = Bash({"cwd": str(tmp_path)})
    with patch.object(subprocess, "Popen") as launch:
        result = tool.execute({"command": "echo denied"})

    assert result.status == "error"
    assert "Process isolation is not supported on this platform" in result.result
    launch.assert_not_called()

"""Browser check lifecycle cannot reuse another running application."""
import socket
import sys
import os
import pytest
from scripts.ui_check import run_clients, run_server


def test_occupied_port_is_rejected():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        with pytest.raises(RuntimeError, match='occupied'):
            run_server([sys.executable, '-c', 'pass'], [[sys.executable, '-c', 'pass']], port=listener.getsockname()[1])


def test_failed_server_cannot_pass_client():
    with pytest.raises(RuntimeError, match='exited'):
        run_server([sys.executable, '-c', 'raise SystemExit(9)'], [[sys.executable, '-c', 'pass']])


def test_each_script_runs_and_reports_failure_without_hiding_later_checks(tmp_path, capsys):
    failed, passed = tmp_path / 'failed.py', tmp_path / 'passed.py'
    failed.write_text('raise SystemExit(7)')
    passed.write_text('from pathlib import Path; Path(' + repr(str(tmp_path / 'ran')) + ').touch()')
    assert run_clients([[sys.executable, str(path)] for path in (failed, passed)], dict(os.environ)) == 1
    assert (tmp_path / 'ran').exists()
    output = capsys.readouterr().out
    assert f'RUN ui: {failed}' in output and f'FAIL ui: {failed}' in output
    assert f'RUN ui: {passed}' in output and f'PASS ui: {passed}' in output
    assert 'exit 7' in output and 'exit 0' in output


def test_timeout_fails_only_the_current_script_and_reaps_its_process(tmp_path, capsys):
    slow = tmp_path / 'slow.py'
    slow.write_text('import time; time.sleep(60)')
    assert run_clients([[sys.executable, str(slow)]], dict(os.environ), timeout=.1) == 1
    assert 'timeout after 0.1s' in capsys.readouterr().out

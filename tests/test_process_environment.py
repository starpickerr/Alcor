"""Only platform startup state crosses the Core -> shell/delegated boundary."""

import sys

import pytest


@pytest.mark.parametrize('platform,variables,expected', [
    ('darwin', {'path': '/usr/bin', 'HOME': '/home/test', 'LANG': 'C'},
     {'path': '/usr/bin', 'HOME': '/home/test', 'LANG': 'C'}),
    ('win32', {'Path': 'C:/bin', 'SystemRoot': 'C:/Windows', 'ComSpec': 'cmd.exe'},
     {'Path': 'C:/bin', 'SystemRoot': 'C:/Windows', 'ComSpec': 'cmd.exe', 'PATHEXT': '.COM;.EXE;.BAT;.CMD'}),
    ('win32', {'pathext': '.EXE'}, {'pathext': '.EXE'}),
])
def test_platform_environment_is_explicit_and_case_insensitive(monkeypatch, platform, variables, expected):
    from core.security.process_environment import core_process_environment
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr('os.environ', {
        **variables, 'DATABASE_URL': 'synthetic', 'OPENAI_WORKLOAD_IDENTITY_CONTEXT': 'synthetic',
        'openai_identity_token_file': 'synthetic', 'OPENAI_FEDERATION_RULE_ID': 'synthetic',
        'CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN': 'synthetic', 'NODE_REPL_AUTH_TOKEN': 'synthetic',
        'CODEX_GUARDIAN_DECISIONS_API_KEY': 'synthetic', 'BASH_ENV': '/injected',
        'PYTHONPATH': '/injected', 'NODE_OPTIONS': '--require=/injected', 'DYLD_INSERT_LIBRARIES': '/injected',
        'STELLA_AGENT_RUN_ID': 'forged',
    })
    assert core_process_environment() == expected


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX shell syntax')
def test_actual_bash_child_does_not_inherit_backend_state(tmp_path, monkeypatch):
    from agent.tools.bash.bash import Bash
    monkeypatch.setenv('DATABASE_URL', 'synthetic-backend-value')
    monkeypatch.setenv('OPENAI_WORKLOAD_IDENTITY_CONTEXT', 'synthetic-identity')
    startup = tmp_path / 'startup.sh'
    startup.write_text('printf STARTUP-INJECTED\n')
    monkeypatch.setenv('BASH_ENV', str(startup))
    result = Bash({'cwd': str(tmp_path)}).execute({
        'command': '/bin/bash -c \'printf "db=%s identity=%s" "$DATABASE_URL" "$OPENAI_WORKLOAD_IDENTITY_CONTEXT"\'',
    })
    assert result.status == 'success', result.result
    assert result.result['output'].strip() == 'db= identity='


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX shell syntax')
def test_dotenv_names_are_still_excluded_from_platform_environment(tmp_path, monkeypatch):
    from agent.tools.bash.bash import Bash
    credentials = tmp_path / 'credentials'
    credentials.write_text('LANG=synthetic-private-value\n')
    monkeypatch.setattr('agent.tools.bash.bash.expand_path', lambda path: str(credentials))
    monkeypatch.setenv('LANG', 'synthetic-private-value')
    result = Bash({'cwd': str(tmp_path)}).execute({'command': 'printf "lang=%s" "$LANG"'})
    assert result.status == 'success', result.result
    assert result.result['output'].strip() == 'lang='


@pytest.mark.parametrize('mode', ['read-only', 'full-access'])
@pytest.mark.skipif(sys.platform not in ('darwin', 'linux'), reason='OS sandbox backend unavailable')
def test_delegated_environment_keeps_private_home_and_drops_backend_state(tmp_path, monkeypatch, mode):
    import subprocess
    from types import SimpleNamespace

    from agent.permission.security_policy import default_policy, PRESETS
    from core.security.delegated_runtime import prepare_delegated

    policy = {**default_policy(), 'preset': mode, 'rules': PRESETS[mode]}
    monkeypatch.setattr('agent.runtime.tool_invocation._current_authorization', lambda *args:
        (mode, {'policy': policy, 'cwd': str(tmp_path), 'scope': SimpleNamespace(write_roots=[])}, None))
    monkeypatch.setenv('DATABASE_URL', 'synthetic-backend-value')
    monkeypatch.setenv('OPENAI_WORKLOAD_IDENTITY_CONTEXT', 'synthetic-identity')
    plan = prepare_delegated(tmp_path, ['/usr/bin/env'])
    result = subprocess.run([*plan['prefix'], '/usr/bin/env'], cwd=tmp_path, env=plan['env'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'synthetic' not in result.stdout
    assert f'HOME={tmp_path}/.home' in result.stdout

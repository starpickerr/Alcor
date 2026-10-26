"""Core-issued output roots remain constrained by the actual subject policy."""
from pathlib import Path

import pytest

from common.runtime_identity import identity_scope
from core.security.service import SecurityService


@pytest.mark.parametrize('mode,inside,outside', [
    ('read-only', False, False), ('workspace-write', True, False), ('full-access', True, True),
])
def test_mcp_output_uses_current_filesystem_policy(tmp_path, monkeypatch, mode, inside, outside):
    from core.security.run_policy import save_run_policy
    from core.workspace.service import WorkspaceService
    monkeypatch.setattr('common.state_dir.shared_root', lambda: tmp_path / 'state')
    monkeypatch.setattr('core.security.migration.ensure_migrated', lambda: None)
    save_run_policy('session', 'output-session', {'filesystem_mode': mode})
    with identity_scope(session_id='output-session'):
        workspace = WorkspaceService.context().root
        for target, allowed in [(workspace / 'output', inside), (tmp_path / 'outside', outside)]:
            if allowed:
                SecurityService.authorize_filesystem(target, operation='write')
            else:
                with pytest.raises(PermissionError):
                    SecurityService.authorize_filesystem(target, operation='write')


def test_mcp_output_cannot_cover_sensitive_or_runtime_roots(tmp_path, native_settings_action):
    from core.security.mcp_runtime import output_scope
    from common.state_dir import shared_root
    roots = [Path('/'), Path(__file__).resolve().parents[1], shared_root().resolve()]
    roots[-1].mkdir(parents=True, exist_ok=True)
    for root in roots:
        with pytest.raises(PermissionError):
            output_scope(root)
    target = tmp_path / 'output'
    target.mkdir()
    link = tmp_path / 'link'
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(PermissionError):
        output_scope(link)


def test_mcp_output_replacement_invalidates_issued_scope(tmp_path, native_settings_action):
    from core.security.mcp_runtime import output_scope
    target = tmp_path / 'output'
    target.mkdir()
    scope = output_scope(target)
    target.rename(tmp_path / 'retired')
    target.mkdir()
    with pytest.raises(PermissionError, match='changed'):
        scope.check()


def test_output_temporary_cleanup_does_not_follow_links_or_scan_user_files(tmp_path, native_settings_action):
    from core.security.mcp_runtime import output_scope, output_temporary
    output, outside = tmp_path / 'output', tmp_path / 'outside'
    output.mkdir()
    outside.mkdir()
    (outside / 'kept').write_text('KEEP_PRIVATE')
    (output / '.elearning-user-kept').write_text('KEEP_OUTPUT')
    with output_temporary(output_scope(output)) as temporary:
        temporary.check()
        (temporary.path / 'partial').write_text('PARTIAL_DOWNLOAD')
        (temporary.path / 'link').symlink_to(outside, target_is_directory=True)
    assert not temporary.path.exists()
    assert (outside / 'kept').read_text() == 'KEEP_PRIVATE'
    assert (output / '.elearning-user-kept').read_text() == 'KEEP_OUTPUT'


def test_output_temporary_root_move_cleans_original_directory_only(tmp_path, native_settings_action):
    from core.security.mcp_runtime import output_scope, output_temporary
    output, retired = tmp_path / 'output', tmp_path / 'retired'
    output.mkdir()
    with output_temporary(output_scope(output)) as temporary:
        (temporary.path / 'partial').write_text('PARTIAL')
        output.rename(retired)
        output.mkdir()
        (output / 'kept').write_text('KEEP_NEW_ROOT')
        with pytest.raises(PermissionError):
            temporary.check()
    assert list(retired.iterdir()) == []
    assert (output / 'kept').read_text() == 'KEEP_NEW_ROOT'


def test_output_temporary_replacement_preserves_the_new_user_directory(tmp_path, native_settings_action):
    from core.security.mcp_runtime import output_scope, output_temporary
    output = tmp_path / 'output'
    output.mkdir()
    with pytest.raises(PermissionError, match='changed'):
        with output_temporary(output_scope(output)) as temporary:
            (temporary.path / 'partial').write_text('PARTIAL')
            moved = output / 'moved-owned'
            temporary.path.rename(moved)
            temporary.path.mkdir()
            (temporary.path / 'kept').write_text('KEEP_REPLACEMENT')
    assert list(moved.iterdir()) == []
    assert (temporary.path / 'kept').read_text() == 'KEEP_REPLACEMENT'


@pytest.mark.parametrize('managed_temporary', [False, True])
def test_real_output_profile_allows_directory_walk_but_denies_sibling_contents(tmp_path, native_settings_action, managed_temporary):
    from contextlib import ExitStack
    import json
    import subprocess
    import sys
    from core.mcp.managed import ManagedMcpConfig
    from core.runtime.paths import source_digest
    from core.security.mcp_runtime import output_scope, output_temporary, prepare_mcp
    package, output, sibling = (tmp_path / name for name in ('package', 'output', 'sibling'))
    for path in (package, output, sibling):
        path.mkdir()
    (sibling / 'private.txt').write_text('private sibling')
    script = package / 'probe.py'
    script.write_text('''import json,os,sys
from pathlib import Path
output,other=map(Path,sys.argv[1:])
fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
for component in output.parts[1:]:
 child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
 os.close(fd)
 fd=child
os.close(fd)
(output/'export.txt').write_text('export')
try: value=(other/'private.txt').read_text()
except OSError: value='denied'
try: (other/'injected.txt').write_text('injected'); write='allowed'
except OSError: write='denied'
print(json.dumps([value,write,os.environ.get('STELLA_MCP_OUTPUT_TEMP')]))
''')
    config = ManagedMcpConfig(dict(name='output-profile', command=sys.executable,
        args=[str(script), str(output), str(sibling)]), data_root=tmp_path / 'private',
        package_digest=source_digest(package), output_scope=output_scope(output))
    with ExitStack() as stack:
        expected_temporary = None
        if managed_temporary:
            config.output_temporary = stack.enter_context(output_temporary(config.output_scope))
            expected_temporary = str(config.output_temporary.path)
        plan, _ = prepare_mcp(config, {'STELLA_MCP_OUTPUT_TEMP': str(sibling)})
        process = subprocess.run(plan.argv, env=plan.env, cwd=plan.cwd, capture_output=True, text=True, timeout=5)
        assert process.returncode == 0, process.stderr
        assert json.loads(process.stdout) == ['denied', 'denied', expected_temporary]
    assert (output / 'export.txt').read_text() == 'export'
    assert not (sibling / 'injected.txt').exists()

"""Protected folders constrain actual managed execution, not the trusted host."""
import os
from pathlib import Path
import subprocess
import shutil
import shlex
import sys
import threading

import pytest

from common.runtime_identity import identity_scope


def control():
    from core.security.protected_paths import ProtectedPathControl
    return ProtectedPathControl()


def protect(paths):
    owner = control()
    return owner.replace([str(path) for path in paths], expected_revision=owner.inventory()['revision'])


@pytest.mark.parametrize('extension', ['docx', 'xlsx', 'pptx', 'pdf'])
def test_allowed_documents_still_parse_through_checked_file_access(tmp_path, extension):
    from agent.tools.read.read import Read
    target = tmp_path / f'ordinary.{extension}'
    marker = 'ALLOWED_DOCUMENT_TEXT'
    if extension == 'docx':
        from docx import Document
        document = Document()
        document.add_paragraph(marker)
        document.save(target)
    elif extension == 'xlsx':
        from openpyxl import Workbook
        document = Workbook()
        document.active.append([marker])
        document.save(target)
        document.close()
    elif extension == 'pptx':
        from pptx import Presentation
        document = Presentation()
        slide = document.slides.add_slide(document.slide_layouts[1])
        slide.shapes.title.text = marker
        document.save(target)
    else:
        from pypdf import PdfWriter
        document = PdfWriter()
        document.add_blank_page(width=100, height=100)
        document.write(target)
    result = Read({'cwd': str(tmp_path)}).execute({'path': str(target)})
    assert result.status == 'success', result
    if extension != 'pdf':
        assert marker in str(result.result)
    else:
        assert result.result['total_pages'] == 1


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


def test_retargeted_read_path_is_rechecked_at_the_actual_file_open(tmp_path, monkeypatch):
    from agent.tools.read.read import Read
    public = tmp_path / 'ordinary.txt'
    secret = tmp_path / 'private'
    secret.mkdir()
    target = secret / 'secret.txt'
    target.write_text('SYNTHETIC_PROTECTED_CONTENT')
    public.write_text('ordinary data')
    protect([secret])
    reader = Read({'cwd': str(tmp_path)})
    original = reader._read_text
    def replace_then_read(*args, **kwargs):
        public.unlink()
        public.symlink_to(target)
        return original(*args, **kwargs)
    monkeypatch.setattr(reader, '_read_text', replace_then_read)
    result = reader.execute({'path': str(public)})
    assert result.status == 'error'
    assert 'SYNTHETIC_PROTECTED_CONTENT' not in str(result.result)


@pytest.mark.parametrize('tool_name', ['write', 'edit'])
def test_retargeting_after_review_cannot_overwrite_a_protected_file(tmp_path, monkeypatch, tool_name):
    import importlib
    module = importlib.import_module(f'agent.tools.{tool_name}.{tool_name}')
    tool = getattr(module, tool_name.title())({'cwd': str(tmp_path)})
    public = tmp_path / 'ordinary.txt'
    public.write_text('ordinary data')
    secret = tmp_path / 'private'
    secret.mkdir()
    target = secret / 'secret.txt'
    target.write_text('KEEP_PROTECTED_CONTENT')
    protect([secret])
    review = module.syntax_review
    def replace_after_review(*args, **kwargs):
        result = review(*args, **kwargs)
        public.unlink()
        public.symlink_to(target)
        return result
    monkeypatch.setattr(module, 'syntax_review', replace_after_review)
    arguments = {'path': str(public), 'content': 'WRITE_ATTEMPT', 'oldText': 'ordinary', 'newText': 'EDIT_ATTEMPT'}
    result = tool.execute(arguments)
    assert result.status == 'error'
    assert target.read_text() == 'KEEP_PROTECTED_CONTENT'


def test_policy_change_rejects_an_already_open_read_without_waiting_for_its_parser(tmp_path):
    from core.security.file_access import checked_open
    secret = tmp_path / 'private'
    secret.mkdir()
    path = secret / 'text.txt'
    path.write_text('SYNTHETIC_PROTECTED_CONTENT')
    done = threading.Event()
    failures = []
    def enable_protection():
        try:
            protect([secret])
        except Exception as error:
            failures.append(error)
        finally:
            done.set()
    worker = threading.Thread(target=enable_protection)
    try:
        with pytest.raises(PermissionError):
            with checked_open(path, 'rb') as file:
                worker.start()
                assert done.wait(1), 'An idle open parser must not prevent protection updates'
                assert not failures, failures
                file.read()
    finally:
        worker.join(3)
    assert not worker.is_alive()


def test_recursive_agent_search_cannot_return_protected_content_through_external_backend(tmp_path):
    from agent.tools.search_files.search_files import SearchFiles
    root = tmp_path / 'search'
    secret = root / 'private'
    secret.mkdir(parents=True)
    (root / 'normal.txt').write_text('SEARCH_MARKER_PUBLIC')
    (secret / 'secret.txt').write_text('SEARCH_MARKER_PRIVATE')
    protect([secret])
    search = SearchFiles({'cwd': str(root)})
    search._pick_backend = lambda: search._backend_grep
    with identity_scope(session_id='source-probe'):
        result = search.execute({'target': 'content', 'pattern': 'SEARCH_MARKER', 'path': str(root)})
    assert result.status == 'success'
    assert 'SEARCH_MARKER_PUBLIC' in str(result.result)
    assert 'SEARCH_MARKER_PRIVATE' not in str(result.result)


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

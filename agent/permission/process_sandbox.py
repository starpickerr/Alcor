"""OS-enforced boundaries for shell commands and their child processes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

from agent.permission.security_policy import rule
from core.runtime.paths import runtime_readonly_roots


class SandboxUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessPlan:
    argv: list[str] | str
    shell: bool
    temp_dir: str | None
    status: str
    share_lease: tuple[tuple[str, str, str, str], ...] = ()
    protection_revision: int = 0


_lease_lock = threading.RLock()
_share_processes: dict[int, tuple[subprocess.Popen, frozenset[str]]] = {}


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           capture_output=True, timeout=5, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()


def register_share_process(process: subprocess.Popen, plan: ProcessPlan) -> None:
    """Bind a live shell tree to each recipient grant in its sandbox profile."""
    if not plan.share_lease:
        return
    from agent.workspace.shares import share_access
    with _lease_lock:
        try:
            valid = all(share_access(path, session, agent) is not None
                        for _, path, session, agent in plan.share_lease)
        except (ValueError, OSError, RuntimeError):
            valid = False
        if not valid:
            _stop_process(process)
            raise SandboxUnavailable("A shared-file grant was revoked before shell launch.")
        _share_processes[process.pid] = (process, frozenset(row[0] for row in plan.share_lease))


def unregister_share_process(process: subprocess.Popen) -> None:
    with _lease_lock:
        _share_processes.pop(process.pid, None)


def revoke_share_processes(share_id: str) -> None:
    """Stop foreground/background shell trees that retained a revoked grant."""
    with _lease_lock:
        for process, grants in list(_share_processes.values()):
            if share_id in grants:
                _stop_process(process)


def sandbox_executable():
    name = 'sandbox-exec' if sys.platform == 'darwin' else 'bwrap'
    found = shutil.which(name)
    standard = '/usr/bin/' + name
    return found or (standard if os.path.isfile(standard) and os.access(standard, os.X_OK) else None)


def capability() -> dict:
    """Probe actual launch ability, not just whether a binary is on PATH."""
    if sys.platform == "darwin":
        executable = sandbox_executable()
        probe = [executable, "-p", "(version 1) (allow default)", "/usr/bin/true"] if executable else None
    elif sys.platform.startswith("linux"):
        executable = sandbox_executable()
        probe = ([executable, "--die-with-parent", "--ro-bind", "/", "/", "--proc", "/proc",
                  "--dev-bind", "/dev", "/dev", "--", "/bin/true"] if executable else None)
    else:
        return {"status": "unavailable", "reason": "Process isolation is not supported on this platform."}
    if not probe:
        return {"status": "unavailable", "reason": "The process sandbox dependency is missing."}
    try:
        result = subprocess.run(probe, capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "reason": "The process sandbox could not start."}
    if result.returncode:
        return {"status": "unavailable", "reason": "The process sandbox is installed but cannot run here."}
    return {"status": "enabled", "reason": "Shell child processes run inside an OS sandbox."}


def _seatbelt_literal(path: str) -> str:
    # Seatbelt reads UTF-8 path literals but does not decode JSON's \u escapes.
    # Escaping a Chinese workspace name makes an otherwise allowed directory
    # appear read-only to every shell child process.
    return json.dumps(os.path.realpath(os.path.expanduser(path)), ensure_ascii=False)


def _protected_paths() -> tuple[list[str], list[str]]:
    from common.state_dir import shared_root, user_root
    from agent.workspace.shares import _manifest_file
    from agent.tools.utils.credentials import protected_credential_paths, protected_credential_folders
    files = sorted(protected_credential_paths()) + [str(_manifest_file())]
    folders = sorted(protected_credential_folders())
    folders.append(str(user_root() / 'briefing' / 'elearning-sessions'))
    folders.append(str(shared_root() / "skills"))
    folders.append(str(user_root()))
    # Core-managed tenant data is private even when another user appears
    # after this process starts. Ordinary workspace roots remain separate.
    folders.append(str(shared_root() / 'users'))
    return files, folders


def prepare(command: str, cwd: str, roots: list[str], policy: dict, env: dict,
            *, approved_target: str | None = None, context=None) -> ProcessPlan:
    """Build one command plan; a constrained launch fails closed if isolation fails."""
    constrained = rule(policy, "workspace_outside") != "allow"
    from core.security.protected_paths import sandbox_paths
    protection_revision, protected_folders = sandbox_paths()
    state = capability()
    if state["status"] != "enabled":
        raise SandboxUnavailable(state["reason"])

    if context is None:
        from core.workspace.service import resolve_context
        from common.runtime_identity import current_identity
        identity = current_identity()
        try:
            context = resolve_context(identity.execution_session_id or identity.session_id,
                                      identity.agent_id, task_id=identity.task_id)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            raise SandboxUnavailable(f"Private workspace context is unavailable: {exc}") from exc

    from agent.workspace.shares import list_shares, share_access
    try:
        shared = list_shares(context.session_id, context.agent_id) if context.session_id and not context.task_id else []
    except (ValueError, OSError, RuntimeError) as exc:
        raise SandboxUnavailable("Private Work grants are unavailable.") from exc
    shared = [row for row in shared if share_access(row["path"], context.session_id, context.agent_id)]

    temp_dir = tempfile.mkdtemp(prefix="stella-shell-")
    env.update({"TMPDIR": temp_dir, "TMP": temp_dir, "TEMP": temp_dir,
                "XDG_CACHE_HOME": temp_dir})
    write_roots = list(dict.fromkeys(os.path.realpath(path) for path in [*roots, temp_dir]))
    writable = rule(policy, 'workspace_write') != 'deny'
    if not writable:
        write_roots = [temp_dir]
    files, folders = _protected_paths()
    protected = " ".join(f"(deny file-read* file-write* (literal {_seatbelt_literal(path)}))"
                         for path in files)
    protected += " " + " ".join(f"(deny file-read* file-write* (subpath {_seatbelt_literal(path)}))"
                                  for path in folders)
    if sys.platform == "darwin":
        permissions = ""
        if constrained:
            read_roots = [cwd, *roots, *(policy.get('extra_roots') or []), temp_dir,
                          '/System', '/usr', '/bin', '/sbin', '/Library', '/private/etc', sys.prefix, sys.base_prefix,
                          str(Path(__file__).resolve().parents[2] / 'core')]
            permissions += '(deny file-read*) (allow file-read-metadata) (allow file-read* (literal "/")) '
            permissions += ' '.join(f'(allow file-read* (subpath {_seatbelt_literal(path)}))' for path in read_roots)
            permissions += ' (allow file-read* file-write* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random")) '
            permissions += "(deny file-write*) " + " ".join(
                f"(allow file-write* (subpath {_seatbelt_literal(path)}))" for path in write_roots)
            if approved_target and writable:
                permissions += f" (allow file-read* file-write* (literal {_seatbelt_literal(approved_target)}))"
        # Operation permissions never grant a raw socket to an untrusted child.
        # Networking must use a Core-owned connector, independent of this mode.
        permissions += " (deny network*)"
        # Host services can decrypt credentials or proxy private file/network
        # access. Execution modes never authorize a child to become a trusted
        # Keychain client; connectors remain in Core.
        permissions += " (deny mach-lookup)"
        work = os.path.realpath(context.work_root)
        private_rules = [f"(deny file-read* file-write* (literal {_seatbelt_literal(work)}))"]
        for name in ("sessions", "automations", "shared"):
            private_rules.append(f"(deny file-read* file-write* (subpath {_seatbelt_literal(os.path.join(work, name))}))")
        own = context.private_root
        if own and own.is_dir():
            own_real = os.path.realpath(own)
            private_rules.append(f"(allow file-read* (subpath {_seatbelt_literal(own_real)}))")
            if writable and (not constrained or any(os.path.commonpath((own_real, root)) == root for root in write_roots)):
                private_rules.append(f"(allow file-write* (subpath {_seatbelt_literal(own_real)}))")
        for row in shared:
            file_path = _seatbelt_literal(row["path"])
            private_rules.append(f"(allow file-read* (literal {file_path}))")
            if writable and (row["access"] == "write" or row["owner_session"] == context.session_id):
                private_rules.append(f"(allow file-write* (literal {file_path}))")
        immutable = ' '.join(f'(deny file-write* (subpath {_seatbelt_literal(path)}))'
                             for path in runtime_readonly_roots())
        owner_protection = ' '.join(
            f'(deny file-read* file-write* (subpath {_seatbelt_literal(folder)})) ' + ' '.join(
                f'(deny file-write* (literal {_seatbelt_literal(str(parent))}))'
                for parent in Path(folder).parents)
            for folder in protected_folders)
        if not constrained and not writable:
            permissions += f' (deny file-write*) (allow file-write* (subpath {_seatbelt_literal(temp_dir)}))'
        profile = f"(version 1) (allow default) {permissions} {protected} {' '.join(private_rules)} {immutable} {owner_protection}"
        argv = [sandbox_executable(), "-p", profile, "/bin/sh", "-c", command]
    else:
        executable = sandbox_executable()
        isolation = ["--unshare-pid", "--unshare-net"]
        if constrained:
            argv = [executable, "--die-with-parent", *isolation, "--proc", "/proc",
                    "--dev-bind", "/dev", "/dev"]
            read_roots = [cwd, *roots, *(policy.get('extra_roots') or []),
                          '/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc', sys.prefix, sys.base_prefix,
                          str(Path(__file__).resolve().parents[2] / 'core')]
            for path in dict.fromkeys(os.path.realpath(path) for path in read_roots if os.path.exists(path)):
                argv.extend(['--ro-bind', path, path])
            # Merged-/usr systems still launch shells and loaders through these aliases.
            for path in ('/bin', '/sbin', '/lib', '/lib64'):
                if os.path.islink(path):
                    argv.extend(['--symlink', os.readlink(path), path])
            for path in write_roots:
                argv.extend(["--bind", path, path])
            if approved_target and writable:
                target = os.path.realpath(approved_target)
                if not os.path.exists(target):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    raise SandboxUnavailable("This Linux sandbox cannot grant one new outside file; authorize its directory explicitly.")
                argv.extend(["--bind", target, target])
            argv.extend(["--chdir", cwd, "--", "/bin/sh", "-c", command])
        else:
            argv = [executable, "--die-with-parent", *isolation, "--bind" if writable else "--ro-bind", "/", "/", "--proc", "/proc",
                    "--dev-bind", "/dev", "/dev", "--chdir", cwd, "--", "/bin/sh", "-c", command]
            if not writable:
                argv[argv.index('--'):argv.index('--')] = ['--bind', temp_dir, temp_dir]
        # Hide control data inside the mount namespace even in full-access
        # mode. The tool gate also rejects direct requests for these paths,
        # but a script must not be able to read them indirectly.
        sandbox_options = []
        # Freeze runtime before private masks so broad mounts cannot uncover secrets.
        for path in runtime_readonly_roots():
            if os.path.isdir(path):
                sandbox_options.extend(['--ro-bind', path, path])
        for path in files:
            if os.path.isfile(path):
                sandbox_options.extend(["--ro-bind", "/dev/null", os.path.realpath(path)])
        for path in folders:
            if os.path.isdir(path):
                sandbox_options.extend(["--tmpfs", os.path.realpath(path)])
        work = os.path.realpath(context.work_root)
        if os.path.isdir(work):
            for name in ("sessions", "automations", "shared"):
                category = os.path.join(work, name)
                if os.path.isdir(category):
                    sandbox_options.extend(["--tmpfs", category])
                    own = context.private_root
                    if name != "shared" and own and own.is_dir() and os.path.commonpath((str(own), category)) == category:
                        own_real = os.path.realpath(own)
                        sandbox_options.extend(["--dir", os.path.dirname(own_real)])
                        own_writable = writable and (not constrained or any(os.path.commonpath((own_real, root)) == root for root in write_roots))
                        sandbox_options.extend(["--bind" if own_writable else "--ro-bind", own_real, own_real])
                    elif name == "shared":
                        for row in shared:
                            path = os.path.realpath(row["path"])
                            sandbox_options.extend(["--dir", os.path.dirname(path)])
                            share_writable = writable and (row["access"] == "write" or row["owner_session"] == context.session_id)
                            sandbox_options.extend(["--bind" if share_writable else "--ro-bind", path, path])
        for folder in protected_folders:
            sandbox_options.extend(['--tmpfs', folder])
        if constrained:
            sandbox_options.extend(['--remount-ro', '/'])
        argv[argv.index("--"):argv.index("--")] = sandbox_options
    leases = tuple((row["id"], row["path"], context.session_id, context.agent_id)
                   for row in shared if row["recipient_session"] == context.session_id and row["active"])
    return ProcessPlan(argv, False, temp_dir, "enabled", leases, protection_revision)


def current_policy_and_roots(cwd: str) -> tuple[dict, list[str]]:
    from agent.runtime.tool_invocation import _current_authorization
    from common.runtime_identity import current_identity
    identity = current_identity()
    _, context, _ = _current_authorization(None, identity.agent_id, identity.session_id, cwd, False)
    policy = context["policy"]
    roots = list(context["scope"].write_roots)
    roots.extend(policy.get("extra_roots") or [])
    return policy, roots

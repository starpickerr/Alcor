"""Restrict untrusted extensions to runtime code and their private storage."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

from agent.permission.process_sandbox import SandboxUnavailable, capability, sandbox_executable


@dataclass(frozen=True)
class ExtensionPlan:
    argv: list[str]
    cwd: str
    env: dict
    state: dict
    protection_revision: int = 0


def validate_source_root(package):
    """Public extension inputs cannot include registered Core private state."""
    from agent.tools.utils.credentials import (
        is_credential_path, protected_credential_folders, protected_credential_paths,
    )
    package = Path(package).resolve()
    from core.security.protected_paths import sandbox_paths
    _, folders = sandbox_paths()
    if any(package.is_relative_to(Path(folder)) or Path(folder).is_relative_to(package) for folder in folders):
        raise PermissionError('Extension source overlaps an Owner-protected folder; select a separate source directory')
    if is_credential_path(str(package)) or any(
            Path(path).is_relative_to(package)
            for path in {*protected_credential_paths(), *protected_credential_folders()}):
        raise PermissionError('Extension source overlaps Core credential or private storage; select a separate source directory')
    return package


def prepare_extension(command, package, storage, env, *, trusted=False, writable=True,
                      read_roots=(), write_roots=(), executable_reads=(), directory_reads=(), network=False,
                      read_package=True):
    from core.security.protected_paths import sandbox_paths
    protection_revision, protected_folders = sandbox_paths()
    package, storage = Path(package).resolve(), Path(storage).resolve()
    # Builders may execute a snapshot inside their already-bound candidate.
    # Raw source roots are validated before copying into that namespace.
    if read_package and not any(package.is_relative_to(Path(root).resolve())
                                for root in (storage, *read_roots, *write_roots)):
        validate_source_root(package)
    storage.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage.chmod(0o700)
    clean = {key: str(value) for key, value in env.items() if not key.startswith(('LD_', 'DYLD_', 'PYTHON')) and
             key not in {'NODE_OPTIONS', 'NODE_PATH', 'BASH_ENV', 'ENV'}}
    core = Path(__file__).resolve().parents[2]
    clean['PYTHONPATH'] = str(core)
    clean['PYTHONDONTWRITEBYTECODE'] = '1'
    # Legacy trust facts establish neither OS isolation nor permission to skip
    # it. First-party and third-party workers use the same process boundary.
    state = capability()
    if state['status'] != 'enabled':
        raise SandboxUnavailable(state['reason'])
    owned_roots = list(dict.fromkeys([storage, *(Path(path).resolve() for path in write_roots)]))
    for directory in owned_roots:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_home = storage / '.home'
    temporary = storage / '.tmp'
    private_home.mkdir(exist_ok=True, mode=0o700)
    temporary.mkdir(exist_ok=True, mode=0o700)
    clean.update(HOME=str(private_home), USERPROFILE=str(private_home), TMPDIR=str(temporary), TMP=str(temporary),
                 TEMP=str(temporary), XDG_CACHE_HOME=str(private_home), XDG_CONFIG_HOME=str(private_home),
                 PYTHONDONTWRITEBYTECODE='1')
    # Core chooses import roots; no caller import injection survives.
    roots = [Path(path).resolve() for path in ('/System', '/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc', '/Library', '/private/etc',
             sys.prefix, sys.base_prefix, str(core / 'core'),
             *([str(package)] if read_package else []), *map(str, read_roots)) if Path(path).exists()]
    executable_reads = (*executable_reads, core / 'common' / 'log.py')
    initializer = core / 'common' / '__init__.py'
    if initializer.is_file():
        executable_reads = (*executable_reads, initializer)
    if sys.platform == 'darwin':
        def literal(path):
            return json.dumps(str(path), ensure_ascii=False)
        # dyld opens the root directory during launch; this literal does not
        # grant descendants (which remain subject to the exact read roots).
        profile = '(version 1) (deny default) (allow process-fork process-exec sysctl-read) (allow signal (target self)) (allow file-read-metadata) (allow file-read* (literal "/")) '
        if network:
            profile += '(allow network*) '
        profile += ' '.join(f'(allow file-read* (subpath {literal(path)}))' for path in roots)
        profile += f' (allow file-read* (literal {literal(core)}))'
        profile += f' (allow file-read* (literal {literal(core / "common")}))'
        profile += ' '.join(f'(allow file-read* (subpath {literal(path)}))' for path in owned_roots)
        profile += ' '.join(f'(allow file-write* (subpath {literal(path)}))'
                            for path in (owned_roots if writable else [private_home, temporary]))
        profile += ' '.join(f'(allow file-read* (literal {literal(Path(path).resolve())}))' for path in executable_reads)
        # Descriptor-relative file APIs open each ancestor with O_DIRECTORY.
        # Permit only those directory objects, never their descendants.
        profile += ' '.join(f'(allow file-read* (literal {literal(Path(path).resolve())}))' for path in directory_reads)
        profile += ' (allow file-read* file-write* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))'
        for folder in protected_folders:
            profile += f' (deny file-read* file-write* (subpath {literal(folder)}))'
            profile += ' '.join(f' (deny file-write* (literal {literal(str(parent))}))' for parent in Path(folder).parents)
        argv = [sandbox_executable(), '-p', profile, *command]
    else:
        argv = [sandbox_executable(), '--die-with-parent', '--unshare-all', '--new-session']
        if network:
            argv += ['--share-net']
        for path in dict.fromkeys(roots):
            argv += ['--ro-bind', str(path), str(path)]
        for path in ('/bin', '/sbin', '/lib', '/lib64'):
            if os.path.islink(path):
                argv += ['--symlink', os.readlink(path), path]
        for path in executable_reads:
            argv += ['--ro-bind', str(Path(path).resolve()), str(Path(path).resolve())]
        for path in owned_roots:
            argv += ['--bind' if writable else '--ro-bind', str(path), str(path)]
        if not writable:
            for path in (private_home, temporary):
                argv += ['--bind', str(path), str(path)]
        for folder in protected_folders:
            argv += ['--tmpfs', folder]
        argv += ['--proc', '/proc', '--dev', '/dev', '--remount-ro', '/',
                 '--chdir', str(storage), '--', *command]
    return ExtensionPlan(argv, str(storage), clean, {'mode': 'isolated', 'network': bool(network), 'reason': 'OS file isolation enabled; process networking allowed.' if network else 'OS file and network isolation enabled.'}, protection_revision)

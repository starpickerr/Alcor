"""Core-owned local MCP launch identity and live trust checks."""
import ast
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import secrets
import stat
import sys

from core.security.runtime_trust import RuntimeTrustStore


@dataclass(frozen=True)
class McpOutputScope:
    root: Path
    device: int
    inode: int

    def check(self):
        current = output_scope(self.root)
        if (current.device, current.inode) != (self.device, self.inode):
            raise PermissionError('MCP output directory changed; retry the operation')


def output_scope(value):
    """Bind one existing owner-selected directory; never accept a wire grant."""
    from agent.tools.utils.credentials import protected_credential_paths, protected_credential_folders
    from core.runtime.paths import runtime_readonly_roots
    from core.security.service import SecurityService
    root = Path(value)
    if not root.is_absolute() or '..' in root.parts:
        raise PermissionError('MCP output requires an existing absolute directory')
    if any(path.is_symlink() for path in (root, *root.parents) if str(path) not in {'/var', '/tmp'}):
        raise PermissionError('MCP output directory cannot use symlinks')
    root = root.resolve(strict=True)
    protected = {*protected_credential_paths(), *protected_credential_folders(), *runtime_readonly_roots()}
    if not root.is_dir() or any(root.is_relative_to(path) or Path(path).is_relative_to(root) for path in protected):
        raise PermissionError('MCP output cannot cover protected resources or runtime code')
    SecurityService.authorize_filesystem(root, operation='write')
    stat = root.stat()
    return McpOutputScope(root, stat.st_dev, stat.st_ino)


_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)


def _directory_identity(descriptor):
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino


def _clear_output_temporary(descriptor):
    """Only visit the operation-owned namespace; never follow directory links."""
    for name in os.listdir(descriptor):
        entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(entry.st_mode):
            child = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
            try:
                if _directory_identity(child) != (entry.st_dev, entry.st_ino):
                    raise PermissionError('MCP temporary directory changed during cleanup')
                _clear_output_temporary(child)
            finally:
                os.close(child)
            current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (entry.st_dev, entry.st_ino):
                raise PermissionError('MCP temporary directory changed during cleanup')
            os.rmdir(name, dir_fd=descriptor)
        else:
            os.unlink(name, dir_fd=descriptor)


@dataclass(frozen=True)
class McpOutputTemporary:
    scope: McpOutputScope
    name: str
    parent: int
    descriptor: int

    @property
    def path(self):
        return self.scope.root / self.name

    def _check_entry(self):
        current = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != _directory_identity(self.descriptor):
            raise PermissionError('MCP temporary directory changed; retry the operation')

    def check(self):
        self.scope.check()
        self._check_entry()


@contextmanager
def output_temporary(scope):
    """Keep cleanup ownership alive even if the short-lived output Worker dies."""
    scope.check()
    parent = os.open(scope.root, _DIRECTORY_FLAGS)
    descriptor = None
    name = '.stella-mcp-output-' + secrets.token_hex(16)
    try:
        if _directory_identity(parent) != (scope.device, scope.inode):
            raise PermissionError('MCP output directory changed; retry the operation')
        os.mkdir(name, mode=0o700, dir_fd=parent)
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        temporary = McpOutputTemporary(scope, name, parent, descriptor)
        try:
            temporary.check()
            yield temporary
        finally:
            _clear_output_temporary(descriptor)
            temporary._check_entry()
            os.rmdir(name, dir_fd=parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def launch_identity(config):
    command = shutil.which(config['command']) or config['command']
    digest = sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode())
    files = [Path(command)] + [Path(arg) for arg in config.get('args', [])
                              if isinstance(arg, str) and Path(arg).suffix in {'.py', '.js', '.mjs', '.cjs', '.sh'}]
    for path in files:
        if path.is_file():
            digest.update(str(path.resolve()).encode())
            digest.update(path.read_bytes())
    package = files[1].resolve().parent if len(files) > 1 and files[1].is_file() else Path(command).resolve().parent
    if ((len(files) > 1 and files[1].is_file())
            or (files[0].is_file() and files[0].read_bytes().startswith(b'#!'))):
        # Bind trust to local imported implementation too, without opening
        # account databases, credential files or interpreter caches.
        for directory, folders, names in os.walk(package):
            folders[:] = sorted(name for name in folders if not name.startswith('.') and name not in {'__pycache__', 'build', 'dist', 'venv', 'node_modules'} and not name.endswith('.egg-info'))
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix in {'.py', '.pyi', '.js', '.mjs', '.cjs', '.so', '.dylib', '.node'}:
                    digest.update(path.relative_to(package).as_posix().encode())
                    digest.update(path.read_bytes())
    return digest.hexdigest(), package


def environment_identity(config):
    """Scope dependencies by executable inputs, independently of UI/enablement."""
    return launch_identity({'command': config['command'], 'args': config.get('args', []),
                            'env': config.get('env') or {}})[0]


def _fixed_executable_entry(config):
    if Path(config['command']).name in {'npx', 'uvx', 'bunx'} or set(config.get('args', [])) & {'-m', '-c', '-e', '--eval'}:
        return False
    command = Path(shutil.which(config['command']) or config['command']).resolve()
    # Console entrypoints can import implementation from an unrelated install.
    # Only local Python bundles have a determinable package identity here; an
    # opaque console/module launcher must use isolation or an explicit script.
    if not command.is_file() or not command.read_bytes().startswith(b'#!'):
        return True
    first_line = command.read_bytes().splitlines()[0]
    if b'python' not in first_line.lower():
        return False
    try:
        tree = ast.parse(command.read_text())
    except (ValueError, SyntaxError, UnicodeError):
        return False
    for node in ast.walk(tree):
        names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                 [node.module] if isinstance(node, ast.ImportFrom) and not node.level and node.module else [])
        for name in names:
            root = name.split('.')[0]
            if root in {'importlib', 'pkg_resources'}:
                return False
            if root not in sys.stdlib_module_names and not any(
                    path.exists() for path in (command.parent / (root + '.py'), command.parent / root / '__init__.py')):
                return False
    return True


def _verify_bound_implementation(config, package):
    from core.mcp.managed import ManagedMcpConfig
    from core.runtime.paths import source_digest, bundle_digest
    if not isinstance(config, ManagedMcpConfig) or not (config.data_root is not None or config.legacy_roots):
        return
    explicit_script = any(isinstance(arg, str) and arg.endswith(('.py', '.js', '.mjs', '.cjs'))
                          for arg in config.get('args', []))
    implementation_digest = source_digest if explicit_script else bundle_digest
    if not config.package_digest or implementation_digest(package) != config.package_digest:
        raise PermissionError('MCP implementation does not match the Core-pinned distribution')


def prepare_mcp(config, env):
    env = dict(env)
    # Wire/configuration environment cannot grant output staging authority.
    env.pop('STELLA_MCP_OUTPUT_TEMP', None)
    from common.runtime_identity import current_identity
    from common.state_dir import user_root
    from core.security.extension_sandbox import prepare_extension
    user_id = current_identity().user_id or 'local-default'
    digest, package = launch_identity(config)
    key = dict(user_id=user_id, kind='mcp', identifier=config['name'], digest=digest)
    if RuntimeTrustStore().choice(**key) is False:
        raise PermissionError('MCP runtime identity was revoked')
    from core.mcp.managed import ManagedMcpConfig
    from core.runtime.paths import source_digest
    _verify_bound_implementation(config, package)
    if isinstance(config, ManagedMcpConfig) and config.output_scope is not None:
        config.output_scope.check()
    if isinstance(config, ManagedMcpConfig) and config.output_temporary is not None:
        temporary = config.output_temporary
        if not isinstance(temporary, McpOutputTemporary) or temporary.scope != config.output_scope:
            raise PermissionError('MCP temporary output requires a matching Core-owned scope')
        temporary.check()
        env['STELLA_MCP_OUTPUT_TEMP'] = str(temporary.path)
    from core.mcp.lifecycle import McpMaintenanceConfig
    maintenance = isinstance(config, McpMaintenanceConfig)
    environment_digest = config.original_launch_digest if maintenance else environment_identity(config)
    name = Path(config['command']).name.lower()
    args = config.get('args', [])
    if name in {'npx', 'uvx', 'bunx', 'npm', 'pip', 'pip3', 'uv'}:
        raise ValueError('Select a fixed installed entry; implicit package installers cannot be launched')
    from agent.tools.mcp.mcp_client import _STDIO_ENV_SENSITIVE
    from core.mcp.managed import _installed_digest
    owner_credentials = (config.credential_env if isinstance(config, ManagedMcpConfig)
                         and config.data_root is not None and config.package_digest == _installed_digest(config['name'])
                         else frozenset())
    for variable, value in env.items():
        sensitive = (variable.upper().endswith(_STDIO_ENV_SENSITIVE) or variable.upper() in {
            'PASSWORD', 'PASSWD', 'TOKEN', 'SECRET', 'CREDENTIAL', 'APIKEY', 'GITHUB_TOKEN', 'GH_TOKEN'})
        if value and sensitive and variable not in owner_credentials:
            raise PermissionError('Raw credentials cannot be passed to a local MCP; use a trusted connector')
    if not _fixed_executable_entry(config):
        raise PermissionError('Select a fixed local implementation and explicit script entry')
    environment = None
    command = [config['command'], *args]
    from core.runtime.environments import EnvironmentStore
    if name.startswith('python'):
        index = next((index for index, arg in enumerate(args) if isinstance(arg, str) and arg.endswith('.py')), None)
        if index is None or any(arg not in {'-u', '-I', '-B', '-s', '-E'} for arg in args[:index]) or not Path(args[index]).is_file():
            raise PermissionError('Python MCP requires a fixed local explicit script entry')
        environment = EnvironmentStore().prepare_python(kind='mcp', identifier=config['name'], user=user_id,
            package=package, digest=source_digest(package), rebuild=maintenance, launch_digest=environment_digest)
        command = [environment['python'], *args]
    elif name in {'node', 'nodejs'}:
        # Match isolated npm/build preparation; do not load host OpenSSL config.
        env['OPENSSL_CONF'] = os.devnull
        if not args or not isinstance(args[0], str) or not args[0].endswith(('.js', '.mjs', '.cjs')) or not Path(args[0]).is_file():
            raise ValueError('Node MCP requires a fixed explicit script entry')
        environment = EnvironmentStore().prepare_node(kind='mcp', identifier=config['name'], user=user_id,
            package=package, digest=source_digest(package), rebuild=maintenance, launch_digest=environment_digest)
        command = [environment['node'], str(Path(environment['path']) / 'project' / Path(args[0]).name), *args[1:]]
    storage = (config.maintenance_storage if maintenance else
               user_root() / 'mcp-runtime' / sha256(config['name'].encode()).hexdigest())
    if any(path.is_symlink() for path in [storage, *storage.parents] if path != Path('/')):
        # macOS /var aliases are canonicalized by user_root; only actual storage
        # ancestors are considered below by resolving the configured root.
        root = user_root().resolve()
        if storage.is_symlink() or storage.parent.is_symlink() or root != user_root().absolute():
            raise PermissionError('MCP private storage cannot use symlinks')
    reads = [Path(environment['path'])] if environment else []
    libraries = []
    if environment and environment.get('runtime') == 'node':
        reads.append(environment['base']['prefix'])
        libraries = environment['base']['libraries']
    writes = []
    if isinstance(config, ManagedMcpConfig) and config.data_root is not None:
        for root in [config.data_root, *config.legacy_roots]:
            if any(path.is_symlink() for path in (root, *root.parents) if str(path) not in {'/var', '/tmp'}):
                raise PermissionError('MCP bound storage cannot use symlinks')
        # These roots are supplied by Core owner adapters, never JSON/tool input.
        writes = [config.data_root.resolve()]
        reads.extend(root.resolve() for root in config.legacy_roots if root.exists())
        if config.output_scope is not None:
            writes.append(config.output_scope.root)
    directory_reads = (tuple(config.output_scope.root.parents)
                       if isinstance(config, ManagedMcpConfig) and config.output_scope is not None else ())
    plan = prepare_extension(command, package, storage, env, read_roots=reads, write_roots=writes,
                             executable_reads=libraries, directory_reads=directory_reads)
    if environment:
        plan.state['environment'] = environment
    return plan, key


def check_mcp(client):
    if not getattr(client, '_runtime_key', None):
        return
    key = client._runtime_key
    digest, package = launch_identity(client.config)
    try:
        _verify_bound_implementation(client.config, package)
    except PermissionError:
        client.shutdown()
        raise
    from core.mcp.lifecycle import McpMaintenanceConfig, check_admission
    if not isinstance(client.config, McpMaintenanceConfig):
        try:
            check_admission(key['identifier'], key['user_id'], config=client.config,
                            launch_digest=client.environment.get('launch_digest') if client.environment else None)
            if client.environment is not None:
                from core.runtime.environments import EnvironmentStore
                selected = EnvironmentStore().current('mcp', key['identifier'], key['user_id'],
                                                      launch_digest=client.environment.get('launch_digest'))
                if selected and selected['fingerprint'] == client.environment['fingerprint'] and selected['id'] != client.environment['id']:
                    raise PermissionError('MCP dependency environment changed; restart the connection')
        except (ValueError, OSError):
            client.shutdown()
            raise
    choice = RuntimeTrustStore().choice(**key)
    if digest != key['digest'] or choice is False:
        client.shutdown()
        raise PermissionError('MCP runtime trust or launch content changed; restart the connection')

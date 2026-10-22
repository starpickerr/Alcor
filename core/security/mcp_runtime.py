"""Core-owned local MCP launch identity and live trust checks."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil

from core.security.runtime_trust import RuntimeTrustStore


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
    if len(files) > 1 and files[1].is_file():
        # Bind trust to local imported implementation too, without opening
        # account databases, credential files or interpreter caches.
        for directory, folders, names in os.walk(package):
            folders[:] = sorted(name for name in folders if not name.startswith('.') and name != '__pycache__')
            for name in sorted(names):
                path = Path(directory) / name
                if path.suffix in {'.py', '.pyi', '.js', '.mjs', '.cjs', '.so', '.dylib', '.node'}:
                    digest.update(path.relative_to(package).as_posix().encode())
                    digest.update(path.read_bytes())
    return digest.hexdigest(), package


def prepare_mcp(config, env):
    from common.runtime_identity import current_identity
    from common.state_dir import user_root
    from core.mcp.managed import ManagedMcpConfig
    from core.security.extension_sandbox import prepare_extension
    user_id = current_identity().user_id or 'local-default'
    digest, package = launch_identity(config)
    key = dict(user_id=user_id, kind='mcp', identifier=config['name'], digest=digest)
    choice = RuntimeTrustStore().choice(**key)
    trusted = choice if choice is not None else isinstance(config, ManagedMcpConfig)
    if trusted and Path(config['command']).name in {'npx', 'uvx', 'bunx'}:
        raise PermissionError('Install a fixed local MCP package before choosing trusted runtime.')
    storage = user_root() / 'mcp-runtime' / sha256(config['name'].encode()).hexdigest()
    if any(path.is_symlink() for path in [storage, *storage.parents] if path != Path('/')):
        # macOS /var aliases are canonicalized by user_root; only actual storage
        # ancestors are considered below by resolving the configured root.
        root = user_root().resolve()
        if storage.is_symlink() or storage.parent.is_symlink() or root != user_root().absolute():
            raise PermissionError('MCP private storage cannot use symlinks')
    runtime = Path(config['command']).absolute().parent.parent
    reads = [runtime] if Path(config['command']).name.startswith('python') and (runtime / 'pyvenv.cfg').is_file() else []
    plan = prepare_extension([config['command'], *config.get('args', [])], package, storage, env,
                             trusted=trusted, read_roots=reads)
    return plan, key


def check_mcp(client):
    if not getattr(client, '_runtime_key', None):
        return
    key = client._runtime_key
    digest, _ = launch_identity(client.config)
    from core.mcp.managed import ManagedMcpConfig
    choice = RuntimeTrustStore().choice(**key)
    allowed = choice if choice is not None else isinstance(client.config, ManagedMcpConfig)
    if digest != key['digest'] or client.sandbox_state.get('mode') == 'trusted' and not allowed:
        client.shutdown()
        raise PermissionError('MCP runtime trust or launch content changed; restart the connection')

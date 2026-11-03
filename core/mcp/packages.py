"""Owner-time fixed package preparation; MCP startup never invokes installers."""
from hashlib import sha256
import json
import shutil
from pathlib import Path
import re
import tempfile

from core.runtime.environments import EnvironmentStore
from core.runtime.paths import source_digest
from core.plugins.package_ops import _atomic_json
from core.plugins.locks import package_lock


def package_spec(config):
    launcher = Path(config.get('command') or '').name
    args = list(config.get('args') or [])
    if not all(isinstance(value, str) and '\0' not in value for value in args):
        raise ValueError('Package arguments must be a string array')
    if launcher == 'npx':
        if args and args[0] in {'-y', '--yes'}:
            args.pop(0)
        pattern = r'((?:@[a-z0-9][a-z0-9_.-]*/)?[a-z0-9][a-z0-9_.-]*)@([0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?)'
    elif launcher == 'uvx':
        pattern = r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([0-9]+(?:\.[0-9]+)+(?:[A-Za-z0-9.-]+)?)'
    else:
        raise ValueError('Package preparation supports npx or uvx with an exact version')
    match = re.fullmatch(pattern, args.pop(0)) if args else None
    if not match:
        raise ValueError('Select a fixed package entry: npx [-y] package@1.2.3 or uvx package==1.2.3; ranges and launcher options are unsupported')
    entry = config.get('entrypoint')
    if entry is not None and (not isinstance(entry, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', entry)):
        raise ValueError('Package entrypoint must name one installed console entry')
    return launcher, *match.groups(), args, entry


def _tree(record):
    # Bind every installed implementation file, including symlink targets.
    root = Path(record['path']) / ('project' if record.get('runtime') == 'node' else 'python')
    digest = sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) and resolved != Path(record['python'] if record.get('runtime') != 'node' else record['node']).resolve():
                # Virtualenv interpreter aliases may point at the verified shared base.
                if record.get('runtime') == 'node' or resolved != Path(record['base']['executable']).resolve():
                    raise PermissionError('Prepared package symlink escaped its environment')
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(str(resolved).encode())
        elif path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def prepared_entry(config, store=None):
    from common.runtime_identity import current_identity
    from core.security.mcp_runtime import environment_identity
    package_spec(config)
    store = store or EnvironmentStore()
    user = current_identity().user_id or 'local-default'
    record = store.current('mcp', config['name'], user, launch_digest=environment_identity(config))
    if not record or not record.get('launcher'):
        raise PermissionError('Prepare this exact package version in MCP settings before starting it')
    launcher = record['launcher']
    if launcher['spec'] != list(package_spec(config)) or launcher['content_digest'] != _tree(record):
        raise PermissionError('Prepared package content changed; prepare it again in MCP settings')
    entry = Path(launcher['entry'])
    if not entry.resolve().is_relative_to(Path(record['path'])) or not entry.is_file():
        raise PermissionError('Prepared entry escaped its managed environment')
    argv = [record['node'], str(entry)] if record.get('runtime') == 'node' else [record['python'], str(entry)]
    return record, [*argv, *package_spec(config)[3]]


def prepare_package(config):
    from common.runtime_identity import current_identity
    from core.security.control import require_owner
    from core.security.mcp_runtime import environment_identity, launch_identity
    from core.security.runtime_trust import RuntimeTrustStore
    require_owner()
    spec = package_spec(config)
    launcher, name, version, arguments, entry = spec
    user = current_identity().user_id or 'local-default'
    identity = launch_identity(config)[0]
    if RuntimeTrustStore().choice(user_id=user, kind='mcp', identifier=config['name'], digest=identity) is False:
        raise PermissionError('This MCP launch identity was revoked')
    store = EnvironmentStore()
    launch_digest = environment_identity(config)
    with package_lock(store.root, 'prepare-mcp-' + store._owner('mcp', config['name'], user, launch_digest), exclusive=True, timeout=130):
        source = Path(tempfile.mkdtemp(prefix='stella-mcp-package-')).resolve()
        candidate = None
        try:
            if launcher == 'npx':
                _atomic_json(source / 'package.json', {'name':'stella-mcp-environment', 'version':'1.0.0', 'private':True, 'dependencies':{name:version}})
                base = store._node_base()
                result = store._execute([base['executable'], base['installer'], 'install', '--package-lock-only', '--ignore-scripts', '--no-audit', '--no-fund',
                    '--registry=https://registry.npmjs.org', '--cache', str(source / '.npm-cache')], package=source, candidate=source,
                    read_roots=[base['prefix']], executable_reads=base['libraries'], cwd=source, network=True)
                if result.returncode:
                    raise RuntimeError('Pinned npm lock preparation failed; configuration and selected environment were retained')
                # Lock metadata is retained; resolver caches are not runtime inputs.
                shutil.rmtree(source / '.npm-cache', ignore_errors=True)
                candidate = store.prepare_node(kind='mcp', identifier=config['name'], user=user, package=source,
                    digest=source_digest(source), launch_digest=launch_digest, rebuild=True)
                package = Path(candidate['path']) / 'project' / 'node_modules' / name
                metadata = json.loads((package / 'package.json').read_text())
                if metadata.get('name') != name or metadata.get('version') != version:
                    raise PermissionError('Installed npm package identity changed')
                bins = metadata.get('bin') or {}
                if isinstance(bins, str):
                    bins = {name.split('/')[-1]:bins}
                selected = entry or (name.split('/')[-1] if name.split('/')[-1] in bins else next(iter(bins)) if len(bins) == 1 else None)
                if selected not in bins or not isinstance(bins[selected], str):
                    raise ValueError('Select one package bin using the entrypoint configuration field')
                target = (package / bins[selected]).resolve(strict=True)
                if not target.is_relative_to(package.resolve()):
                    raise PermissionError('npm bin escaped its package')
            else:
                from core.runtime.tools import ManagedTools
                uv = ManagedTools().uv()
                (source / 'requirements.in').write_text(name + '==' + version + '\n')
                result = store._execute([str(uv), '--no-config', 'pip', 'compile', '--generate-hashes', '--only-binary=:all:',
                    '--index-url=https://pypi.org/simple', '--output-file', str(source / 'requirements.lock'),
                    str(source / 'requirements.in')], package=source, candidate=source, read_roots=[uv.parent], cwd=source, network=True)
                if result.returncode:
                    raise RuntimeError('Pinned Python lock preparation failed; configuration and selected environment were retained')
                candidate = store.prepare_python(kind='mcp', identifier=config['name'], user=user, package=source,
                    digest=source_digest(source), launch_digest=launch_digest, rebuild=True)
                import configparser
                choices = {}
                for path in (Path(candidate['path']) / 'python').glob('lib/python*/site-packages/*.dist-info/entry_points.txt'):
                    metadata = path.with_name('METADATA').read_text()
                    from email.parser import Parser
                    info = Parser().parsestr(metadata)
                    def normalized(value):
                        return re.sub('[-_.]+', '-', value).lower()
                    if normalized(info.get('Name', '')) != normalized(name) or info.get('Version') != version:
                        continue
                    parser = configparser.ConfigParser()
                    parser.read(path)
                    choices.update(dict(parser['console_scripts']) if parser.has_section('console_scripts') else {})
                selected = entry or (name if name in choices else next(iter(choices)) if len(choices) == 1 else None)
                if selected not in choices or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', selected):
                    raise ValueError('Select one package console script using the entrypoint configuration field')
                target = Path(candidate['path']) / 'python' / 'bin' / selected
            # Keep dependency identity separate from the owner-selected runtime entry.
            candidate['launcher'] = dict(spec=list(spec), entry=str(target), content_digest=_tree(candidate))
            _atomic_json(Path(candidate['path']) / 'environment.json', candidate)
            if launch_identity(config)[0] != identity:
                raise PermissionError('MCP configuration changed during package preparation')
            # Selection is owner-controlled; protocol validation happens on first startup.
            store.activate(candidate, validate=lambda _: launch_identity(config)[0] == identity)
            from core.mcp.lifecycle import McpLifecycle
            from agent.tools.tool_manager import ToolManager
            McpLifecycle(ToolManager())._stop_connections(config['name'], launch_digest)
            return candidate
        except BaseException:
            if candidate:
                store.fail_candidate(candidate)
            raise
        finally:
            shutil.rmtree(source)

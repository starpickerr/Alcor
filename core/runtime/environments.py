"""Isolated dependency preparation and verified selection; business data stays owned.

Interpreters are shared read-only. Every extension/version has a separate
site-packages tree. Third-party builds run only inside the existing OS sandbox.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from core.plugins.locks import package_lock
from core.plugins.package_ops import _atomic_json, _sha256_tree
from core.runtime.paths import source_digest


class EnvironmentStore:
    def __init__(self, root=None):
        if root is None:
            from common.state_dir import shared_root
            root = shared_root() / 'managed-runtimes'
        root = Path(root).absolute()
        if any(path.is_symlink() for path in (root, *root.parents) if str(path) not in {'/var', '/tmp'}):
            raise PermissionError('runtime storage cannot use symlinks')
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / 'bases').mkdir(exist_ok=True, mode=0o700)

    @staticmethod
    def _owner(kind, identifier, user):
        if kind not in {'plugin', 'mcp'} or not all(isinstance(value, str) and value and '\x00' not in value for value in (identifier, user)):
            raise ValueError('invalid environment owner')
        return sha256(json.dumps([kind, identifier, user]).encode()).hexdigest()

    def _folder(self, kind, identifier, user):
        folder = self.root / 'environments' / self._owner(kind, identifier, user)
        if any(path.is_symlink() for path in (self.root, folder.parent, folder)):
            raise PermissionError('Environment storage namespace cannot use symlinks')
        return folder

    @staticmethod
    def _base():
        executable = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
        digest = sha256(executable.read_bytes()).hexdigest()
        version = '.'.join(map(str, sys.version_info[:3]))
        return dict(id='python-' + version + '-' + digest[:16], executable=str(executable),
                    version=version, digest=digest, prefix=str(Path(sys.base_prefix).resolve()))

    @staticmethod
    def _node_base():
        node, npm = shutil.which('node'), shutil.which('npm')
        if not node or not npm:
            raise RuntimeError('Node.js and npm are required; no Core dependency fallback is available')
        executable, installer = Path(node).resolve(), Path(npm).resolve()
        digest = sha256(executable.read_bytes()).hexdigest()
        env = {key: value for key, value in os.environ.items() if key in {'PATH', 'SYSTEMROOT', 'WINDIR'}}
        version = subprocess.run([str(executable), '--version'], env=env, capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        return dict(id='node-' + version + '-' + digest[:16], executable=str(executable),
                    version=version, digest=digest, prefix=str(executable.parent.parent),
                    installer=str(installer), installer_digest=sha256(installer.read_bytes()).hexdigest(),
                    libraries=EnvironmentStore._native_libraries(executable))

    @staticmethod
    def _native_libraries(executable):
        if sys.platform != 'darwin':
            return {}
        pending, found = [Path(executable)], {}
        def expand(value, binary):
            return value.replace('@loader_path', str(binary.parent)).replace('@executable_path', str(Path(executable).parent))

        def rpaths(binary):
            lines = subprocess.run(['/usr/bin/otool', '-l', str(binary)], capture_output=True,
                                   text=True, check=True, timeout=5).stdout.splitlines()
            return [expand(lines[index + 2].strip().split(' (offset ', 1)[0].removeprefix('path '), binary)
                    for index, line in enumerate(lines) if line.strip() == 'cmd LC_RPATH']

        inherited = rpaths(Path(executable))
        while pending:
            binary = pending.pop()
            paths = [*rpaths(binary), *inherited]
            identities = subprocess.run(['/usr/bin/otool', '-D', str(binary)], capture_output=True,
                                        text=True, check=True, timeout=5).stdout.splitlines()[1:]
            output = subprocess.run(['/usr/bin/otool', '-L', str(binary)], capture_output=True,
                                    text=True, check=True, timeout=5).stdout
            for line in output.splitlines()[1:]:
                name = line.strip().split(' (', 1)[0]
                if name in identities or name.startswith(('/usr/lib/', '/System/')):
                    continue  # dyld shared cache / platform library roots
                name = expand(name, binary)
                if name.startswith('@rpath/'):
                    options = [str(Path(root) / name.removeprefix('@rpath/')) for root in paths]
                    name = next((path for path in options if Path(path).is_file()), name)
                if name.startswith('@') or not Path(name).is_absolute() or not Path(name).is_file():
                    raise RuntimeError('Shared runtime has an unresolved native dependency')
                library = Path(name).resolve()
                if str(library) not in found:
                    found[str(library)] = sha256(library.read_bytes()).hexdigest()
                    pending.append(library)
        return found

    def _execute(self, argv, *, package, candidate, write=True, read_roots=(), executable_reads=(), cwd=None):
        from core.security.extension_sandbox import prepare_extension
        from common.process_tree import stop_process_tree
        env = {key: value for key, value in os.environ.items() if key in {'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'SYSTEMROOT', 'WINDIR'}}
        env.update(UV_OFFLINE='1', UV_NO_CACHE='1', UV_NO_CONFIG='1', PYTHONNOUSERSITE='1',
                   NPM_CONFIG_OFFLINE='true', NPM_CONFIG_USERCONFIG=os.devnull, OPENSSL_CONF=os.devnull)
        build = candidate / 'build'
        plan = prepare_extension(argv, package, build, env, writable=write,
                                 read_roots=[candidate, *read_roots], write_roots=[candidate] if write else [],
                                 executable_reads=executable_reads)
        process = subprocess.Popen(plan.argv, cwd=str(cwd) if cwd else plan.cwd, env=plan.env, start_new_session=True,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate(timeout=120)
            return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
        finally:
            stop_process_tree(process, owned_session=True)

    def prepare_python(self, *, kind, identifier, user, package, digest, build=None, rebuild=False):
        folder = self._folder(kind, identifier, user)
        package = Path(package).resolve()
        lock = package / 'requirements.lock'
        if ((package / 'requirements.txt').is_file() or (package / 'pyproject.toml').is_file()) and not lock.is_file():
            raise ValueError('Extension dependencies require a pinned requirements.lock with hashes')
        uv, wheels = None, []
        if lock.is_file() and lock.read_text().strip():
            from core.runtime.artifacts import python_lock, python_wheels
            from core.runtime.tools import ManagedTools
            python_lock(lock)
            uv = ManagedTools().uv()
            wheels = python_wheels(lock)
        base = self._base()
        inputs = json.dumps([digest, source_digest(package) if package.exists() else '', base['id'], build], sort_keys=True)
        fingerprint = sha256(inputs.encode()).hexdigest()
        with package_lock(self.root, self._owner(kind, identifier, user), exclusive=True, timeout=130):
            if not rebuild:
                for marker in folder.glob('*/environment.json'):
                    record = json.loads(marker.read_text())
                    if record.get('fingerprint') == fingerprint and record.get('state') == 'ready':
                        return self._record(record)
            candidate = folder / uuid.uuid4().hex
            candidate.mkdir(parents=True, mode=0o700)
            python = candidate / 'python' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            record = dict(id=candidate.name, kind=kind, identifier=identifier, user=user, path=str(candidate), python=str(python),
                base_id=base['id'], base=base, package=str(package), package_digest=digest, fingerprint=fingerprint, state='preparing')
            _atomic_json(candidate / 'environment.json', record)
            try:
                prepared = self._execute([base['executable'], '-I', '-m', 'venv', '--without-pip', str(candidate / 'python')],
                                         package=package, candidate=candidate)
                if prepared.returncode:
                    raise RuntimeError('Python environment creation failed inside OS isolation')
                if uv:
                    links = [value for folder in dict.fromkeys(str(wheel.parent) for wheel in wheels) for value in ('--find-links', folder)]
                    installed = self._execute([str(uv), '--no-config', '--offline', '--no-cache', 'pip', 'sync', '--python', str(python),
                        '--no-index', '--require-hashes', '--only-binary', ':all:', *links, str(lock)], package=package, candidate=candidate,
                        read_roots=[uv.parent, *(wheel.parent for wheel in wheels)])
                    if installed.returncode:
                        raise RuntimeError('Locked dependency preparation failed; required verified wheels must be available offline')
                if build:
                    if not isinstance(build, list) or not build or not all(isinstance(value, str) and value for value in build):
                        raise ValueError('Build entry must be an explicit argument array')
                    result = self._execute([str(python) if value == '{python}' else value for value in build], package=package, candidate=candidate)
                    if result.returncode:
                        raise RuntimeError('Extension build failed inside OS isolation')
                # Recheck immutable inputs after every build; a build may not
                # rewrite the package or the shared interpreter it depends on.
                if self._base() != base or (source_digest(package) if package.exists() else '') != json.loads(inputs)[1]:
                    raise PermissionError('Environment inputs changed during preparation')
                record['state'] = 'ready'
                _atomic_json(candidate / 'environment.json', record)
                _atomic_json(self.root / 'bases' / (base['id'] + '.json'), base)
                return record
            except BaseException:
                record['state'] = 'failed'
                _atomic_json(candidate / 'environment.json', record)
                raise

    def prepare_node(self, *, kind, identifier, user, package, digest, build=None, rebuild=False):
        folder = self._folder(kind, identifier, user)
        package = Path(package).resolve()
        if not (package / 'package-lock.json').is_file():
            raise ValueError('Node dependencies require package-lock.json')
        # The snapshot must never dereference an extension-controlled alias into
        # an owner directory. npm/build scripts cannot change the source tree.
        for directory, folders, files in os.walk(package, followlinks=False):
            for name in [*folders, *files]:
                if (Path(directory) / name).is_symlink():
                    raise PermissionError('Node source cannot contain symlinks')
            folders[:] = [name for name in folders if name not in {'.git', 'node_modules'}]
        base = self._node_base()
        inputs = [digest, _sha256_tree(package), base['id'], build]
        fingerprint = sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
        with package_lock(self.root, self._owner(kind, identifier, user), exclusive=True, timeout=130):
            if not rebuild:
                for marker in folder.glob('*/environment.json'):
                    record = json.loads(marker.read_text())
                    if record.get('fingerprint') == fingerprint and record.get('state') == 'ready':
                        return self._record(record)
            candidate = folder / uuid.uuid4().hex
            candidate.mkdir(parents=True, mode=0o700)
            project = candidate / 'project'
            record = dict(id=candidate.name, runtime='node', kind=kind, identifier=identifier, user=user,
                path=str(candidate), node=base['executable'], base_id=base['id'], base=base, package=str(package),
                package_digest=digest, fingerprint=fingerprint, state='preparing')
            _atomic_json(candidate / 'environment.json', record)
            reads = [base['prefix'], str(Path(base['installer']).parent.parent)]
            try:
                shutil.copytree(package, project, ignore=shutil.ignore_patterns('.git', 'node_modules'))
                from core.runtime.artifacts import npm_archives
                archives = npm_archives(project / 'package-lock.json')
                for archive in archives:
                    seeded = self._execute([base['executable'], base['installer'], 'cache', 'add', str(archive),
                        '--offline', '--ignore-scripts', '--cache', str(candidate / 'npm-cache')], package=project,
                        candidate=candidate, read_roots=[*reads, archive.parent], executable_reads=base['libraries'], cwd=project)
                    if seeded.returncode:
                        raise RuntimeError('Verified Node artifact could not be staged inside OS isolation')
                result = self._execute([base['executable'], base['installer'], 'ci', '--offline', '--ignore-scripts',
                    '--no-audit', '--no-fund', '--cache', str(candidate / 'npm-cache')],
                    package=project, candidate=candidate, read_roots=reads, executable_reads=base['libraries'], cwd=project)
                if result.returncode:
                    raise RuntimeError('Locked Node dependencies could not be prepared offline inside OS isolation')
                if build:
                    if not isinstance(build, list) or not build or not all(isinstance(value, str) and value for value in build):
                        raise ValueError('Build entry must be an explicit argument array')
                    result = self._execute([base['executable'] if value == '{node}' else value for value in build],
                        package=project, candidate=candidate, read_roots=reads, executable_reads=base['libraries'], cwd=project)
                    if result.returncode:
                        raise RuntimeError('Node build failed inside OS isolation')
                if self._node_base() != base or _sha256_tree(package) != inputs[1]:
                    raise PermissionError('Environment inputs changed during preparation')
                record['state'] = 'ready'
                _atomic_json(candidate / 'environment.json', record)
                _atomic_json(self.root / 'bases' / (base['id'] + '.json'), base)
                return record
            except BaseException:
                record['state'] = 'failed'
                _atomic_json(candidate / 'environment.json', record)
                raise

    def _record(self, record):
        owner = self._folder(record['kind'], record['identifier'], record['user'])
        if not re.fullmatch('[0-9a-f]{32}', record['id']):
            raise PermissionError('Invalid environment identity')
        candidate = owner / record['id']
        if candidate.is_symlink() or candidate.resolve() != Path(record['path']) or not candidate.resolve().is_relative_to(self.root):
            raise PermissionError('Environment escaped managed storage')
        saved = json.loads((candidate / 'environment.json').read_text())
        base = self._node_base() if saved.get('runtime') == 'node' else self._base()
        if saved != record or saved['state'] != 'ready' or saved['base'] != base:
            raise PermissionError('Environment or shared runtime changed; rebuild required')
        if saved.get('runtime') != 'node' and Path(saved['python']) != candidate / 'python' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'):
            raise PermissionError('Environment interpreter identity changed')
        return saved

    def run(self, record, arguments):
        record = self._record(record)
        if record.get('runtime') == 'node':
            project = Path(record['path']) / 'project'
            return self._execute([record['node'], *arguments], package=project, candidate=Path(record['path']),
                write=False, read_roots=[record['base']['prefix']], executable_reads=record['base']['libraries'], cwd=project)
        return self._execute([record['python'], *arguments], package=Path(record['package']), candidate=Path(record['path']), write=False)

    def activate(self, record, *, validate):
        record = self._record(record)
        folder = self._folder(record['kind'], record['identifier'], record['user'])
        with package_lock(self.root, self._owner(record['kind'], record['identifier'], record['user']), exclusive=True):
            if validate(record) is not True:
                raise RuntimeError('Candidate validation failed; the previous environment remains selected')
            self._record(record)
            _atomic_json(folder / 'current.json', record)
        return record

    def current(self, kind, identifier, user):
        pointer = self._folder(kind, identifier, user) / 'current.json'
        if not pointer.is_file():
            return None
        return self._record(json.loads(pointer.read_text()))

    def remove(self, kind, identifier, user):
        with package_lock(self.root, self._owner(kind, identifier, user), exclusive=True):
            pointer = self._folder(kind, identifier, user) / 'current.json'
            pointer.unlink(missing_ok=True)
            # Dependency cache may be collected later. Credential and business
            # storage are never passed to this service or recursively removed.

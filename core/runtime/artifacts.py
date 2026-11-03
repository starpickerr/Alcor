"""Content-addressed public downloads; untrusted installers never get sockets."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlsplit

from core.plugins.locks import package_lock
from core.security.http_transport import open_request


class ArtifactCache:
    def __init__(self, root=None):
        if root is None:
            from common.state_dir import shared_root
            root = shared_root() / 'managed-runtimes' / 'downloads'
        root = Path(root).absolute()
        if any(path.is_symlink() for path in (root, *root.parents) if str(path) not in {'/var', '/tmp'}):
            raise PermissionError('Artifact storage cannot use symlinks')
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def fetch(self, url, *, algorithm, digest, filename, maximum=128 * 1024 * 1024):
        if (algorithm not in {'sha256', 'sha512'} or not isinstance(digest, str)
                or not re.fullmatch('[a-f0-9]{' + str(hashlib.new(algorithm).digest_size * 2) + '}', digest)
                or not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]{0,240}', filename)):
            raise ValueError('Artifact requires a safe filename and fixed content digest')
        folder = self.root / algorithm / digest
        if (self.root / algorithm).is_symlink() or folder.is_symlink():
            raise PermissionError('Artifact digest namespace cannot use symlinks')
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / filename
        with package_lock(self.root, digest, exclusive=True):
            if folder.is_symlink() or path.is_symlink():
                raise PermissionError('Artifact cache entry cannot be a symlink')
            if path.exists():
                if hashlib.new(algorithm, path.read_bytes()).hexdigest() != digest:
                    raise PermissionError('Cached artifact integrity check failed')
                return path
            descriptor, temporary = tempfile.mkstemp(prefix='.download-', dir=folder)
            hasher = hashlib.new(algorithm)
            try:
                with os.fdopen(descriptor, 'wb') as output, open_request('GET', url, category='public-packages', maximum=maximum) as response:
                    if response.status != 200:
                        raise RuntimeError('Package registry did not return a downloadable artifact')
                    while chunk := response.read1(65536):
                        hasher.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if hasher.hexdigest() != digest:
                    raise PermissionError('Downloaded artifact integrity check failed')
                os.chmod(temporary, 0o400)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return path


def registry_json(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in {'pypi.org', 'registry.npmjs.org', 'api.github.com'}:
        raise PermissionError('Unsupported public registry metadata service')
    with open_request('GET', url, headers={'Accept': 'application/json'}, category='public-packages', maximum=8 * 1024 * 1024) as response:
        if response.status != 200:
            raise RuntimeError('Package registry metadata is unavailable')
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise ValueError('Invalid package registry metadata')
    return value


def python_lock(path):
    from packaging.requirements import Requirement, InvalidRequirement
    output = []
    for line in Path(path).read_text().replace('\\\n', ' ').splitlines():
        line = line.split(' #', 1)[0].strip()
        if not line or line.startswith('#'):
            continue
        hashes = set(re.findall(r'--hash=sha256:([a-f0-9]{64})(?=\s|$)', line))
        requirement = re.sub(r'--hash=sha256:[a-f0-9]{64}(?=\s|$)', '', line).strip()
        try:
            req = Requirement(requirement)
        except InvalidRequirement as error:
            raise ValueError('Python lock contains an unsupported requirement') from error
        versions = list(req.specifier)
        if req.url or len(versions) != 1 or versions[0].operator != '==' or '*' in versions[0].version or not hashes:
            raise ValueError('Python lock requires exact versions and SHA256 content hashes')
        if not req.marker or req.marker.evaluate():
            output.append((req.name, versions[0].version, hashes))
    return output


def python_wheels(lock, cache=None):
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename, canonicalize_name
    cache = cache or ArtifactCache()
    supported = list(sys_tags())
    result = []
    for name, version, hashes in python_lock(lock):
        # A lock digest and compatible wheel filename suffice for an offline
        # cache hit. Rehash every use; registry availability is not authority.
        cached = []
        algorithm = cache.root / 'sha256'
        if algorithm.is_symlink():
            raise PermissionError('Artifact digest namespace cannot use symlinks')
        for digest in sorted(hashes):
            folder = algorithm / digest
            if folder.is_symlink():
                raise PermissionError('Artifact digest namespace cannot use symlinks')
            for path in folder.glob('*.whl'):
                try:
                    wheel_name, wheel_version, _, tags = parse_wheel_filename(path.name)
                except ValueError:
                    continue
                from packaging.version import Version
                if wheel_name != canonicalize_name(name) or wheel_version != Version(version):
                    continue
                ranks = [supported.index(tag) for tag in tags if tag in supported]
                if not ranks:
                    continue
                if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise PermissionError('Cached artifact integrity check failed')
                cached.append((min(ranks), path))
        if cached:
            result.append(min(cached, key=lambda item: (item[0], str(item[1])))[1])
            continue
        metadata = registry_json(f'https://pypi.org/pypi/{quote(name, safe="")}/{quote(version, safe="")}/json')
        if canonicalize_name(metadata['info']['name']) != canonicalize_name(name) or metadata['info']['version'] != version:
            raise PermissionError('Registry package identity does not match the lock')
        choices = []
        for item in metadata.get('urls') or []:
            filename = item.get('filename', '')
            digest = (item.get('digests') or {}).get('sha256')
            if not filename.endswith('.whl') or digest not in hashes or item.get('yanked'):
                continue
            _, _, _, tags = parse_wheel_filename(filename)
            ranks = [supported.index(tag) for tag in tags if tag in supported]
            parsed = urlsplit(item.get('url', ''))
            if ranks and parsed.scheme == 'https' and parsed.hostname == 'files.pythonhosted.org':
                choices.append((min(ranks), item))
        if not choices:
            raise RuntimeError('No locked, hash-verified binary wheel is available for this runtime')
        item = min(choices, key=lambda value: value[0])[1]
        result.append(cache.fetch(item['url'], algorithm='sha256', digest=item['digests']['sha256'], filename=item['filename']))
    return result


def npm_archives(lock, cache=None):
    import base64
    import binascii
    cache = cache or ArtifactCache()
    manifest = json.loads(Path(lock).read_text())
    if manifest.get('lockfileVersion') not in {2, 3} or not isinstance(manifest.get('packages'), dict):
        raise ValueError('Node environment requires a complete npm v2/v3 lockfile')
    result = []
    for name, package in manifest['packages'].items():
        if not name:
            continue
        url, integrity = package.get('resolved', ''), package.get('integrity', '')
        if package.get('link') or url.startswith('file:'):
            local = (Path(lock).parent / url.removeprefix('file:')).resolve()
            if not local.is_relative_to(Path(lock).resolve().parent):
                raise PermissionError('Local Node dependencies cannot escape the project snapshot')
            if not local.exists():
                raise ValueError('Local Node dependency is missing')
            continue
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname != 'registry.npmjs.org' or parsed.username or parsed.password:
            raise PermissionError('Node registry dependency must use a fixed supported HTTPS artifact')
        # npm's exact SRI binding is the cache identity; weaker legacy SHA1
        # locks must be regenerated by the owner, not silently widened.
        algorithm, separator, encoded = integrity.partition('-')
        try:
            digest = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError('Node lock has invalid artifact integrity') from error
        if not separator or algorithm not in {'sha256', 'sha512'} or len(digest) != hashlib.new(algorithm).digest_size:
            raise ValueError('Node registry artifact requires SHA256/SHA512 integrity')
        result.append(cache.fetch(url, algorithm=algorithm, digest=digest.hex(), filename='package.tgz'))
    return list(dict.fromkeys(result))

"""HMAC-SHA256 signed plugin ZIP packages (first-edition trusted catalog)."""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10000


def _validate_members(archive, *, maximum):
    members = archive.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS or sum(info.file_size for info in members) > maximum:
        raise ValueError('Plugin archive exceeds expansion size or member limit')
    seen, aliases, kinds = set(), {}, {}
    for info in members:
        name = info.filename[:-1] if info.is_dir() else info.filename
        parts = name.split('/')
        if ('\0' in info.orig_filename or '\\' in name or ':' in name
                or any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in parts)):
            raise ValueError('Plugin archive has an invalid member path')
        if name in seen:
            raise ValueError('Plugin archive contains duplicate members')
        seen.add(name)
        mode = stat.S_IFMT(info.external_attr >> 16)
        if mode not in {0, stat.S_IFDIR if info.is_dir() else stat.S_IFREG} or info.is_dir() and info.file_size:
            raise PermissionError('Plugin archives cannot contain links or special files')
        for index in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:index])
            key = unicodedata.normalize('NFC', prefix).casefold()
            if key in aliases and aliases[key] != prefix:
                raise ValueError('Plugin archive contains filesystem aliases')
            aliases[key] = prefix
            directory = index < len(parts) or info.is_dir()
            if key in kinds and kinds[key] != directory:
                raise ValueError('Plugin archive member conflicts with a directory')
            kinds[key] = directory
    return members


def sign_bytes(payload: bytes, *, secret: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_signature(payload: bytes, signature_hex: str, *, secret: bytes) -> bool:
    expected = sign_bytes(payload, secret=secret)
    return hmac.compare_digest(expected, signature_hex)


def signing_secret_from_env() -> bytes:
    raw = os.environ.get("STELLA_PLUGIN_SIGNING_SECRET", "").encode("utf-8").strip()
    if raw:
        return raw
    # Packaged desktop: <Resources>/backend/stella-backend/stella-backend → ../../plugin-signing.secret
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parents[2] / "plugin-signing.secret"
        if candidate.is_file():
            secret = candidate.read_bytes().strip()
            if secret:
                return secret
    raise RuntimeError("STELLA_PLUGIN_SIGNING_SECRET is required for signed packages")


def write_signed_zip(source_dir: Path, archive: Path, *, secret: bytes) -> Path:
    """Pack ``source_dir`` into a ZIP with sibling ``.sig`` metadata member."""
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    buf_path = archive.with_suffix(".unsigned.zip")
    with zipfile.ZipFile(buf_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        root = Path(source_dir)
        from core.plugins.distribution import _package_files

        for path in _package_files(root):
            zf.write(path, path.relative_to(root).as_posix())
    payload = buf_path.read_bytes()
    signature = sign_bytes(payload, secret=secret)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("package.zip", payload)
        zf.writestr(
            "signature.json",
            json.dumps({"alg": "hmac-sha256", "signature": signature}, indent=2) + "\n",
        )
    buf_path.unlink(missing_ok=True)
    return archive


def unpack_signed_zip(archive: Path, dest: Path, *, secret: bytes | None = None) -> Path:
    """Verify all inputs, then publish a new extraction; never overwrite data."""
    secret = secret if secret is not None else signing_secret_from_env()
    dest = Path(dest)
    if dest.exists() or dest.is_symlink():
        raise FileExistsError('Plugin extraction requires an unused destination')
    if Path(archive).stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError('Plugin archive exceeds size limit')
    with zipfile.ZipFile(archive, "r") as zf:
        members = _validate_members(zf, maximum=MAX_ARCHIVE_BYTES + 16384)
        if {info.filename for info in members} != {'signature.json', 'package.zip'}:
            raise ValueError('Invalid signed package wrapper')
        if zf.getinfo('signature.json').file_size > 16384 or zf.getinfo('package.zip').file_size > MAX_ARCHIVE_BYTES:
            raise ValueError('Signed package wrapper exceeds size limit')
        meta = json.loads(zf.read("signature.json"))
        payload = zf.read("package.zip")
    if not isinstance(meta, dict) or meta.get('alg') != 'hmac-sha256' or not isinstance(meta.get('signature'), str):
        raise ValueError('Invalid plugin signature metadata')
    if not verify_signature(payload, meta['signature'], secret=secret):
        raise PermissionError("plugin package signature invalid")
    with zipfile.ZipFile(io.BytesIO(payload), 'r') as package:
        members = _validate_members(package, maximum=MAX_UNPACKED_BYTES)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='extract-', dir=dest.parent) as temporary:
            stage = Path(temporary) / 'verified'
            out = stage / 'content'
            out.mkdir(parents=True)
            for info in members:
                path = out / info.filename
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(info) as source, path.open('xb') as output:
                        shutil.copyfileobj(source, output, length=65536)
            if dest.exists() or dest.is_symlink():
                raise FileExistsError('Plugin extraction destination changed')
            os.rename(stage, dest)
    return dest / 'content'

"""Pinned managed installer tools, separate from Core's Python environment."""

from email.parser import Parser
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import zipfile

from core.plugins.package_ops import _atomic_json
from core.plugins.locks import package_lock
from core.runtime.artifacts import ArtifactCache, registry_json, python_wheels

UV_VERSION = "0.13.5"


class ManagedTools:
    def __init__(self, root=None):
        if root is None:
            from common.state_dir import shared_root

            root = shared_root() / "managed-runtimes" / "tools"
        self.root = ArtifactCache(root).root

    def current_uv(self):
        marker = self.root / "uv.json"
        if not marker.exists():
            return None
        record = json.loads(marker.read_text())
        if record.get("version") != UV_VERSION or not isinstance(
            record.get("digest"), str
        ):
            raise PermissionError("Managed uv identity changed")
        path = self.root / record["digest"] / "uv"
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or not path.is_file()
            or sha256(path.read_bytes()).hexdigest() != record["digest"]
        ):
            raise PermissionError("Managed uv binary integrity check failed")
        return path

    def install_uv_wheel(self, wheel):
        """Core calls this only with a hash-verified PyPI wheel, never wire input."""
        with zipfile.ZipFile(wheel) as archive:
            metadata_name = f"uv-{UV_VERSION}.dist-info/METADATA"
            binary_name = f"uv-{UV_VERSION}.data/scripts/uv"
            if (
                archive.namelist().count(metadata_name) != 1
                or archive.namelist().count(binary_name) != 1
            ):
                raise PermissionError(
                    "Managed tool wheel has an unexpected or ambiguous identity"
                )
            if (
                archive.getinfo(metadata_name).file_size > 65536
                or archive.getinfo(binary_name).file_size > 100 * 1024 * 1024
            ):
                raise PermissionError("Managed tool wheel exceeds the allowed size")
            metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
            if metadata.get("Name") != "uv" or metadata.get("Version") != UV_VERSION:
                raise PermissionError(
                    "Managed tool distribution identity does not match the pinned tool"
                )
            # Never extract the archive tree or execute package install scripts.
            content = archive.read(binary_name)
        digest = sha256(content).hexdigest()
        with package_lock(self.root, "uv", exclusive=True):
            folder = self.root / digest
            if folder.is_symlink():
                raise PermissionError("Managed tool storage cannot use symlinks")
            folder.mkdir(exist_ok=True, mode=0o700)
            path = folder / "uv"
            if path.is_symlink():
                raise PermissionError("Managed tool cannot be a symlink")
            descriptor, temporary = tempfile.mkstemp(prefix=".uv-", dir=folder)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.chmod(temporary, 0o500)
                os.replace(temporary, path)
                _atomic_json(
                    self.root / "uv.json",
                    dict(
                        version=UV_VERSION,
                        digest=digest,
                        wheel_digest=sha256(Path(wheel).read_bytes()).hexdigest(),
                    ),
                )
            finally:
                Path(temporary).unlink(missing_ok=True)
        return self.current_uv()

    def uv(self):
        existing = self.current_uv()
        if existing:
            return existing
        metadata = registry_json(f"https://pypi.org/pypi/uv/{UV_VERSION}/json")
        hashes = {
            row["digests"]["sha256"]
            for row in metadata.get("urls") or []
            if row.get("filename", "").endswith(".whl")
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".uv-lock-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w") as output:
                output.write(
                    "uv=="
                    + UV_VERSION
                    + "".join(" --hash=sha256:" + value for value in sorted(hashes))
                    + "\n"
                )
            wheels = python_wheels(temporary)
            if len(wheels) != 1:
                raise PermissionError("Pinned uv wheel is unavailable for this runtime")
            return self.install_uv_wheel(wheels[0])
        finally:
            Path(temporary).unlink(missing_ok=True)

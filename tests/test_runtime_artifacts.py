"""Only verified public artifacts enter the read-only dependency cache."""

from hashlib import sha256

import pytest

pytest_plugins = ["tests.test_controlled_http"]


def test_artifact_cache_verifies_content_reuses_it_and_rejects_tampering(
    tmp_path, local_service, synthetic_registry_transport
):
    from core.runtime.artifacts import ArtifactCache

    url, received, _, _ = local_service
    cache = ArtifactCache(tmp_path / "artifacts")
    path = cache.fetch(
        url + "/package",
        algorithm="sha256",
        digest=sha256(b"DATA").hexdigest(),
        filename="public.whl",
    )
    assert path.read_bytes() == b"DATA"
    assert (
        cache.fetch(
            url + "/package",
            algorithm="sha256",
            digest=sha256(b"DATA").hexdigest(),
            filename="public.whl",
        )
        == path
    )
    assert received == ["/package"]
    path.chmod(0o600)
    path.write_bytes(b"POISON")
    with pytest.raises(PermissionError, match="integrity|digest"):
        cache.fetch(
            url + "/package",
            algorithm="sha256",
            digest=sha256(b"DATA").hexdigest(),
            filename="public.whl",
        )


def test_artifact_hash_failure_never_publishes_and_paths_cannot_escape(
    tmp_path, local_service, synthetic_registry_transport
):
    from core.runtime.artifacts import ArtifactCache

    url, _, _, _ = local_service
    cache = ArtifactCache(tmp_path / "artifacts")
    with pytest.raises(PermissionError, match="integrity|digest"):
        cache.fetch(
            url + "/package", algorithm="sha256", digest="a" * 64, filename="public.whl"
        )
    assert not list((tmp_path / "artifacts").rglob("*.whl"))
    with pytest.raises(ValueError):
        cache.fetch(
            url + "/package",
            algorithm="sha256",
            digest="a" * 64,
            filename="../escape.whl",
        )
    assert not (tmp_path / "escape.whl").exists()


def test_python_lock_requires_fixed_versions_and_content_hashes(tmp_path):
    from core.runtime.artifacts import python_lock

    path = tmp_path / "requirements.lock"
    for content in (
        "requests>=2\n",
        "requests==2.32.0\n",
        "--extra-index-url https://unknown.invalid\n",
        "requests @ file:///owner/secret\n",
    ):
        path.write_text(content)
        with pytest.raises(ValueError):
            python_lock(path)
    path.write_text("example==1.2.3 \\\n    --hash=sha256:" + "a" * 64 + "\n")
    assert python_lock(path) == [("example", "1.2.3", {"a" * 64})]


def test_managed_tool_extracts_only_expected_binary_and_verifies_cached_identity(
    tmp_path,
):
    import zipfile
    from core.runtime.tools import ManagedTools

    wheel = tmp_path / "uv.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("uv-0.13.5.dist-info/METADATA", "Name: uv\nVersion: 0.13.5\n")
        archive.writestr("uv-0.13.5.data/scripts/uv", b"SYNTHETIC_BINARY_NOT_EXECUTED")
        archive.writestr("unrelated.py", b"must_not_extract")
    tools = ManagedTools(tmp_path / "tools")
    binary = tools.install_uv_wheel(wheel)
    assert binary.read_bytes() == b"SYNTHETIC_BINARY_NOT_EXECUTED"
    assert not list((tmp_path / "tools").rglob("unrelated.py"))
    binary.chmod(0o600)
    binary.write_bytes(b"POISON")
    with pytest.raises(PermissionError):
        tools.current_uv()


def test_managed_tool_rejects_wrong_distribution_or_ambiguous_binary(tmp_path):
    import zipfile
    from core.runtime.tools import ManagedTools

    wheel = tmp_path / "wrong.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "uv-0.13.5.dist-info/METADATA", "Name: attacker\nVersion: 0.13.5\n"
        )
        archive.writestr("uv-0.13.5.data/scripts/uv", b"do_not_execute")
    with pytest.raises(PermissionError):
        ManagedTools(tmp_path / "tools").install_uv_wheel(wheel)
    assert not list((tmp_path / "tools").rglob("uv"))


def test_npm_lock_rejects_unverified_or_mutable_network_sources(tmp_path):
    import json
    from core.runtime.artifacts import npm_archives

    lock = tmp_path / "package-lock.json"
    for resolved, integrity in [
        ("https://registry.npmjs.org/x/-/x.tgz", ""),
        ("git+ssh://host/repo#main", "sha512-YQ=="),
        ("file:///owner/secret", "sha512-YQ=="),
    ]:
        lock.write_text(
            json.dumps(
                {
                    "lockfileVersion": 3,
                    "packages": {
                        "": {},
                        "node_modules/x": {
                            "resolved": resolved,
                            "integrity": integrity,
                        },
                    },
                }
            )
        )
        with pytest.raises((ValueError, PermissionError)):
            npm_archives(lock)


def test_python_locked_wheels_reuse_verified_offline_cache_without_registry_lookup(tmp_path, monkeypatch):
    from core.runtime.artifacts import ArtifactCache, python_wheels
    cache = ArtifactCache(tmp_path / 'cache')
    digest = sha256(b'LOCKED_WHEEL').hexdigest()
    wheel = cache.root / 'sha256' / digest / 'fixture_pkg-1.2.3-py3-none-any.whl'
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b'LOCKED_WHEEL')
    lock = tmp_path / 'requirements.lock'
    lock.write_text('fixture-pkg==1.2.3 --hash=sha256:' + digest)
    def registry_unavailable(url):
        raise OSError('Registry is offline; cached locked wheels must remain usable')
    monkeypatch.setattr('core.runtime.artifacts.registry_json', registry_unavailable)
    assert python_wheels(lock, cache) == [wheel]
    wheel.write_bytes(b'POISON')
    with pytest.raises(PermissionError, match='integrity|digest'):
        python_wheels(lock, cache)


@pytest.fixture
def synthetic_registry_transport(monkeypatch, local_service):
    # This fixture validates download/cache behavior with a declared local test
    # origin. Public-service destination policy has separate real socket tests.
    from core.security.http_transport import open_request
    url, _, _, _ = local_service
    def local_download(method, destination, **kwargs):
        assert destination.startswith(url + '/')
        assert kwargs.pop('category') == 'public-packages'
        return open_request(method, destination, **kwargs)
    monkeypatch.setattr('core.runtime.artifacts.open_request', local_download)


def test_artifact_cache_rejects_redirected_digest_namespace_before_writing(tmp_path, local_service, synthetic_registry_transport):
    from core.runtime.artifacts import ArtifactCache
    cache = ArtifactCache(tmp_path / 'cache')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (cache.root / 'sha256').symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError, match='symlink|redirect'):
        cache.fetch(local_service[0] + '/artifact', algorithm='sha256', digest=sha256(b'DATA').hexdigest(), filename='fixed.whl')
    assert list(outside.iterdir()) == []

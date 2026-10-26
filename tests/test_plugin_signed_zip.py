"""Signed ZIP install path for remote catalog entries (D / R10)."""

from __future__ import annotations

import json
from pathlib import Path
import io
import zipfile

import pytest

from core.plugins.distribution import fetch_and_install_from_catalog
from core.plugins.package_ops import PackageStore
from core.plugins.signing import sign_bytes, verify_signature, write_signed_zip


def _signed_members(archive, members):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, 'w') as package:
        for name, value in members:
            package.writestr(name, value)
    payload = inner.getvalue()
    with zipfile.ZipFile(archive, 'w') as wrapper:
        wrapper.writestr('package.zip', payload)
        wrapper.writestr('signature.json', json.dumps({
            'alg': 'hmac-sha256', 'signature': sign_bytes(payload, secret=b'synthetic-test-key')}))


@pytest.mark.parametrize('layout', ['traversal', 'duplicate', 'symlink', 'case-alias'])
def test_signed_package_rejects_ambiguous_or_linked_members_before_extracting(tmp_path, layout):
    from core.plugins.signing import unpack_signed_zip
    members = [('plugin.json', b'{}')]
    if layout == 'traversal':
        members.append(('../outside.txt', b'escape'))
    elif layout == 'duplicate':
        members.append(('plugin.json', b'overwritten'))
    elif layout == 'case-alias':
        members.append(('PLUGIN.JSON', b'overwritten on default macOS filesystem'))
    else:
        link = zipfile.ZipInfo('linked.py')
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        members.append((link, b'/owner/private.py'))
    archive = tmp_path / 'signed.zip'
    if layout == 'duplicate':
        with pytest.warns(UserWarning, match='Duplicate name'):
            _signed_members(archive, members)
    else:
        _signed_members(archive, members)
    destination = tmp_path / 'unpacked'
    with pytest.raises((ValueError, PermissionError)):
        unpack_signed_zip(archive, destination, secret=b'synthetic-test-key')
    assert not destination.exists()
    assert not (tmp_path / 'outside.txt').exists()


def test_failed_signature_does_not_delete_existing_destination(tmp_path):
    from core.plugins.signing import unpack_signed_zip
    archive = tmp_path / 'signed.zip'
    _signed_members(archive, [('plugin.json', b'{}')])
    destination = tmp_path / 'existing'
    destination.mkdir()
    (destination / 'data').write_text('KEEP_EXISTING_DATA')
    with pytest.raises((PermissionError, FileExistsError)):
        unpack_signed_zip(archive, destination, secret=b'wrong-key')
    assert (destination / 'data').read_text() == 'KEEP_EXISTING_DATA'


def test_signed_package_expansion_limit_is_checked_before_extracting(tmp_path, monkeypatch):
    from core.plugins import signing
    archive = tmp_path / 'signed.zip'
    _signed_members(archive, [('large.txt', b'x' * 2048)])
    monkeypatch.setattr(signing, 'MAX_UNPACKED_BYTES', 1024, raising=False)
    destination = tmp_path / 'unpacked'
    with pytest.raises(ValueError, match='size|limit|large'):
        signing.unpack_signed_zip(archive, destination, secret=b'synthetic-test-key')
    assert not destination.exists()


def test_roundtrip_sign_and_verify():
    payload = b"hello-plugin"
    sig = sign_bytes(payload, secret=b"test-secret")
    assert verify_signature(payload, sig, secret=b"test-secret")
    assert not verify_signature(payload, sig, secret=b"wrong")


def test_signed_zip_install_from_catalog_url(tmp_path, monkeypatch):
    sample = Path("samples/plugins/alpha")
    archive = tmp_path / "alpha.zip"
    secret = b"stella-dev-signing-key"
    write_signed_zip(sample, archive, secret=secret)

    # Digest in catalog is of the unpacked tree (same filters as production).
    from core.plugins.package_ops import _sha256_tree

    catalog = {
        "coreApi": "1.0.0",
        "plugins": [
            {
                "pluginId": "stella.sample.alpha",
                "hostApi": "^1.0.0",
                "path": str(tmp_path / "missing-dir"),
                "url": archive.as_uri(),
                "sha256": _sha256_tree(sample),
                "signatureAlg": "hmac-sha256",
            }
        ],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("STELLA_PLUGIN_SIGNING_SECRET", secret.decode("ascii"))
    store = PackageStore(tmp_path / "pkg")
    result = fetch_and_install_from_catalog(
        catalog_path=catalog_path,
        plugin_id="stella.sample.alpha",
        package_store=store,
    )
    assert result["status"] == "installed"
    assert store.current_package("stella.sample.alpha") is not None


def test_bad_signature_rejects(tmp_path, monkeypatch):
    sample = Path("samples/plugins/alpha")
    archive = tmp_path / "alpha.zip"
    write_signed_zip(sample, archive, secret=b"correct")
    catalog = {
        "coreApi": "1.0.0",
        "plugins": [
            {
                "pluginId": "stella.sample.alpha",
                "hostApi": "^1.0.0",
                "path": str(tmp_path / "missing"),
                "url": archive.as_uri(),
                "sha256": "0" * 64,
                "signatureAlg": "hmac-sha256",
            }
        ],
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("STELLA_PLUGIN_SIGNING_SECRET", "wrong-secret")
    store = PackageStore(tmp_path / "pkg")
    result = fetch_and_install_from_catalog(
        catalog_path=catalog_path,
        plugin_id="stella.sample.alpha",
        package_store=store,
    )
    assert result["status"] in {"signature_invalid", "digest_mismatch", "failed"}

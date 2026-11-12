"""CI must verify companion commits and bytes before running Legacy tests."""
from hashlib import sha256
import json

import pytest

from scripts import check_legacy_sources as check


def test_checkout_rejects_an_unexpected_commit(tmp_path, monkeypatch):
    calls = []

    def revision(command, **kwargs):
        calls.append(command)
        return 'a' * 40 + '\n'

    monkeypatch.setattr(check.subprocess, 'check_output', revision)
    check.verify_checkout(tmp_path, 'a' * 40)
    with pytest.raises(ValueError, match='expected commit'):
        check.verify_checkout(tmp_path, 'b' * 40)
    assert calls[0] == ['git', '-C', str(tmp_path), 'rev-parse', 'HEAD']


@pytest.mark.parametrize('domain', ['email', 'plugins'])
def test_companion_digest_accepts_pinned_bytes_and_rejects_tampering(tmp_path, monkeypatch, domain):
    product = tmp_path / 'Alcor'
    resources = product / 'resources'
    resources.mkdir(parents=True)
    package = tmp_path / 'companion'
    content_root = package / 'plugins/sample' if domain == 'plugins' else package
    content_root.mkdir(parents=True)
    source = content_root / 'source.py'
    source.write_bytes(b'PINNED_SOURCE')
    digest = sha256(b'source.py\0PINNED_SOURCE\0').hexdigest()
    if domain == 'plugins':
        catalog = {'plugins': [{'pluginId': 'stella.sample', 'sourceSha256': digest}]}
        filename = 'default-plugins.json'
    else:
        catalog = {'providers': {domain: {'sourceSha256': digest}}}
        filename = 'mcp-providers.json'
    (resources / filename).write_text(json.dumps(catalog))
    monkeypatch.setattr(check, 'ROOT', product)
    monkeypatch.setattr(check, 'verify_checkout', lambda *_: None)
    sources = {domain: {'path': 'companion', 'revision': 'a' * 40}}
    check.verify_sources(tmp_path, sources)
    source.write_bytes(b'TAMPERED_SOURCE')
    with pytest.raises(ValueError, match='source digest mismatch'):
        check.verify_sources(tmp_path, sources)

"""Signed distribution uses real controlled IO and preserves selected packages."""
import http.server
import json
from pathlib import Path
import threading

import pytest

from core.plugins.distribution import fetch_and_install_from_catalog
from core.plugins.package_ops import PackageStore, _sha256_tree
from core.plugins.signing import write_signed_zip


@pytest.fixture
def distribution(tmp_path, monkeypatch):
    from core.security import http_transport
    from core.security.connections import service_id

    archive = tmp_path / 'signed.zip'
    write_signed_zip(Path('samples/plugins/alpha'), archive, secret=b'synthetic-release-key')
    monkeypatch.setenv('STELLA_PLUGIN_SIGNING_SECRET', 'synthetic-release-key')
    payload = archive.read_bytes()
    started, finish = threading.Event(), threading.Event()
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/private-target')
                self.end_headers()
                return
            self.send_response(404 if self.path == '/missing' else 200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            if self.path == '/slow':
                self.wfile.flush()
                started.set()
                finish.wait(5)
            try:
                self.wfile.write(payload)
            except OSError:
                pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    url = f'http://127.0.0.1:{server.server_port}'
    # Test only: permit exactly this synthetic socket. Category/service leases
    # and actual network IO remain real; production still rejects private IPs.
    real_destination = http_transport.destination
    def synthetic_destination(target, **kwargs):
        assert target.startswith(url + '/')
        assert kwargs['allow_configured_local'] is False
        return real_destination(target, configured_origin=service_id(url)[1])
    monkeypatch.setattr(http_transport, 'destination', synthetic_destination)
    catalog = tmp_path / 'catalog.json'
    def install(path='/signed.zip'):
        catalog.write_text(json.dumps({'coreApi': '1.0.0', 'plugins': [{
            'pluginId': 'stella.sample.alpha', 'hostApi': '^1.0.0',
            'url': url + path, 'sha256': _sha256_tree(Path('samples/plugins/alpha')),
        }]}))
        return fetch_and_install_from_catalog(catalog_path=catalog,
            plugin_id='stella.sample.alpha', package_store=store)
    store = PackageStore(tmp_path / 'store')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield install, store, received, started, finish
    finally:
        finish.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_distribution_honors_disabled_registry_connection_and_cleans_staging(distribution):
    from core.security.connections import ConnectionControl
    install, store, received, _, _ = distribution
    disabled = ConnectionControl().save('public-packages', enabled=False, expected_revision=0)
    try:
        result = install()
        assert result['status'] == 'failed'
        assert 'disabled' in result['error'].lower()
        assert received == []
        assert store.current_package('stella.sample.alpha') is None
        assert list(store.staging.iterdir()) == []
    finally:
        ConnectionControl().save('public-packages', enabled=True, expected_revision=disabled['revision'])


def test_distribution_cancellation_preserves_old_package_and_reopen_does_not_revive_download(distribution):
    from core.security.connections import ConnectionControl
    install, store, _, started, finish = distribution
    assert install()['status'] == 'installed'
    old = store.current_package('stella.sample.alpha')
    outcomes = []
    worker = threading.Thread(target=lambda: outcomes.append(install('/slow')))
    worker.start()
    try:
        assert started.wait(2)
        disabled = ConnectionControl().save('public-packages', enabled=False, expected_revision=0)
        worker.join(2)
        assert not worker.is_alive(), 'Download did not stop when connection was revoked'
        assert outcomes[0]['status'] == 'failed'
        assert store.current_package('stella.sample.alpha') == old
        assert list(store.staging.iterdir()) == []
        ConnectionControl().save('public-packages', enabled=True, expected_revision=disabled['revision'])
        finish.set()
        assert outcomes[0]['status'] == 'failed'
        assert install()['status'] == 'installed'
    finally:
        finish.set()
        worker.join(6)


@pytest.mark.parametrize('path', ['/redirect', '/missing'])
def test_distribution_does_not_follow_redirect_or_install_http_error(distribution, path):
    install, store, received, _, _ = distribution
    result = install(path)
    assert result['status'] == 'failed'
    assert received == [path]
    assert store.current_package('stella.sample.alpha') is None
    assert list(store.staging.iterdir()) == []

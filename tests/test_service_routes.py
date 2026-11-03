"""Configured routes use the saved proxy and retain target, TLS and live guards."""
import http.server
import ipaddress
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone

import pytest

from core.security.http_transport import configured_route, destination, open_request
from core.security.connections import service_id
from core.security.service import SecurityService
from core.plugins.caller import caller_origin_scope


@pytest.fixture
def proxy(monkeypatch, tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'model.invalid')])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('model.invalid')]), critical=False)
            .sign(key, hashes.SHA256()))
    certfile, keyfile = tmp_path / 'test.crt', tmp_path / 'test.key'
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    monkeypatch.setenv('SSL_CERT_FILE', str(certfile))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certfile, keyfile)
    received = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_CONNECT(self):
            received.append(('CONNECT', self.path, self.headers.get('Proxy-Authorization')))
            self.send_response(200)
            self.end_headers()
            self.wfile.flush()
            try:
                stream = tls.wrap_socket(self.connection, server_side=True)
                self.connection = stream
                self.rfile = stream.makefile('rb')
                self.wfile = stream.makefile('wb')
                self.close_connection = False
                self.handle_one_request()
            except ssl.SSLError:
                self.close_connection = True
        def do_POST(self):
            received.append(('POST', self.path, self.headers.get('Proxy-Authorization'), self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.send_header('Content-Length', '2')
            self.end_headers()
            self.wfile.write(b'OK')
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    saved = {'proxy': f'http://test:proxy-password@127.0.0.1:{server.server_port}'}
    monkeypatch.setattr('config.conf', lambda: saved)
    def catalog():
        return [dict(id=service_id(url)[0], endpoint=service_id(url)[1], owners=['model:synthetic'])
                for url in ['http://model.invalid', 'https://model.invalid', 'https://wrong.invalid']]
    monkeypatch.setattr('core.security.connections.connection_catalog', catalog)
    original = socket.getaddrinfo
    def dns(host, *args, **kwargs):
        assert host == '127.0.0.1', 'Target DNS must be resolved by the configured proxy'
        return original(host, *args, **kwargs)
    monkeypatch.setattr(socket, 'getaddrinfo', dns)
    try:
        yield saved, received
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_saved_proxy_routes_without_target_dns_and_keeps_proxy_auth_out_of_tunnel(proxy, scheme):
    saved, received = proxy
    url = scheme + '://model.invalid/chat'
    route = configured_route(url, owner='model:')
    with caller_origin_scope('desktop'):
        permit = SecurityService.authorize_egress(url, b'INPUT', sources=('private:model-context',))
        with open_request('POST', url, body=b'INPUT', permit=permit, **route) as response:
            assert response.read() == b'OK'
    if scheme == 'https':
        assert received[0][:2] == ('CONNECT', 'model.invalid:443')
        assert received[0][2].startswith('Basic ')
        assert received[1] == ('POST', '/chat', None, b'INPUT')
    else:
        assert received[0][:2] == ('POST', url)
        assert received[0][2].startswith('Basic ')


def test_proxy_preserves_target_tls_verification(proxy):
    url = 'https://wrong.invalid/chat'
    route = configured_route(url, owner='model:')
    with caller_origin_scope('desktop'):
        permit = SecurityService.authorize_egress(url, b'INPUT', sources=('private:model-context',))
        with pytest.raises(ssl.SSLCertVerificationError):
            open_request('POST', url, body=b'INPUT', permit=permit, **route)
    assert not any(row[0] == 'POST' for row in proxy[1])


def test_proxy_mutation_and_revocation_invalidate_route_before_connect(proxy):
    from core.security.connections import ConnectionStore
    saved, received = proxy
    url = 'https://model.invalid/chat'
    with caller_origin_scope('desktop'):
        permit = SecurityService.authorize_egress(url, b'INPUT', sources=('private:model-context',))
        route = configured_route(url, owner='model:')
        old = saved['proxy']
        saved['proxy'] = 'http://127.0.0.1:1'
        with pytest.raises(PermissionError, match='Proxy configuration changed'):
            open_request('POST', url, body=b'INPUT', permit=permit, **route)
        saved['proxy'] = old
        from core.security.http_transport import proxy_endpoint
        proxy_id = service_id(proxy_endpoint(old)[0])[0]
        ConnectionStore().save('local-default', proxy_id, False, 0)
        with pytest.raises(PermissionError):
            open_request('POST', url, body=b'INPUT', permit=permit, **route)
        assert received == []


def test_unsupported_proxy_fails_before_approval_or_dns(monkeypatch):
    from core.security.requests_transport import post
    from agent.permission.gateway import SecurityGateway
    monkeypatch.setattr('config.conf', lambda: {'proxy':'socks5://127.0.0.1:7890'})
    url = 'https://model.invalid'
    monkeypatch.setattr('core.security.connections.connection_catalog', lambda: [dict(id=service_id(url)[0], endpoint=service_id(url)[1], owners=['model:test'])])
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: pytest.fail('preflight reached DNS'))
    before = SecurityGateway.pending()
    with pytest.raises(PermissionError, match='HTTP or Mixed'):
        post(url, json={'messages':[]})
    assert SecurityGateway.pending() == before


def test_localhost_and_explicit_lan_ranges_reject_rebinding_and_special_addresses(monkeypatch):
    monkeypatch.setattr('core.security.connections.connection_catalog', lambda: [])
    def answer(ip):
        monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, 1, 6, '', (ip, 80))])
    answer('127.0.0.1')
    assert destination('http://localhost', configured_origin='http://localhost:80').addresses
    answer('10.1.2.3')
    with pytest.raises(PermissionError):
        destination('http://localhost', configured_origin='http://localhost:80')
    assert destination('http://lan.invalid', configured_origin='http://lan.invalid:80', local_networks=['10.1.2.0/24']).addresses
    for ip in ['10.2.0.1', '169.254.169.254', '198.18.0.1']:
        answer(ip)
        with pytest.raises(PermissionError):
            destination('http://lan.invalid', configured_origin='http://lan.invalid:80', local_networks=['10.1.2.0/24'])
    assert not ipaddress.ip_address('198.18.0.1').is_global


def test_proxy_disable_then_enable_cannot_revive_prepared_route(proxy, monkeypatch):
    from core.security.connections import ConnectionStore
    from core.security.http_transport import proxy_endpoint
    saved, received = proxy
    url = 'https://model.invalid/chat'
    with caller_origin_scope('desktop'):
        route = configured_route(url, owner='model:')
        permit = SecurityService.authorize_egress(url, b'INPUT', sources=('private:model-context',))
        proxy_id = service_id(proxy_endpoint(saved['proxy'])[0])[0]
        store = ConnectionStore()
        revision = store.state('local-default', proxy_id)['revision']
        store.save('local-default', proxy_id, False, revision)
        store.save('local-default', proxy_id, True, revision + 1)
        monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('stale route reached DNS'))
        with pytest.raises(PermissionError, match='Proxy connection changed'):
            open_request('POST', url, body=b'INPUT', permit=permit, **route)
    assert received == []

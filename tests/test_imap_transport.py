"""Real TLS IMAP sockets exercise controlled authentication and cancellation."""
import socket
import socketserver
import ssl
import subprocess
import threading

import pytest


@pytest.fixture
def imap_server(tmp_path, monkeypatch):
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=127.0.0.1', '-addext', 'subjectAltName=IP:127.0.0.1',
                    '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    entered, release = threading.Event(), threading.Event()
    commands = []
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'* OK synthetic IMAP ready\r\n')
            try:
                while raw := self.rfile.readline():
                    commands.append(raw)
                    tag, command, *_ = raw.decode().split()
                    if command == 'CAPABILITY':
                        self.wfile.write(b'* CAPABILITY IMAP4rev1\r\n')
                    elif command == 'LOGIN':
                        if not any(value in raw.decode() for value in ('SYNTHETIC_PASSWORD', 'SYNTHETIC_REPLACEMENT')):
                            self.wfile.write(tag.encode() + b' NO bad login\r\n')
                            continue
                    elif command == 'LIST':
                        self.wfile.write(b'* LIST (\\HasNoChildren) "/" "INBOX"\r\n')
                    elif command == 'EXAMINE':
                        self.wfile.write(b'* 1 EXISTS\r\n* OK [UIDVALIDITY 7] stable mailbox\r\n')
                    elif command == 'UID':
                        if raw.split()[2] == b'SEARCH':
                            self.wfile.write(b'* SEARCH 1\r\n')
                        elif raw.split()[2] == b'FETCH':
                            if b'HOLD_FETCH' in commands:
                                entered.set()
                                release.wait(4)
                            message = (b'From: sender@example.test\r\nSubject: Synthetic email\r\n'
                                       b'Message-ID: <test@example.test>\r\n\r\nordinary mail body\r\n')
                            self.wfile.write(b'* 1 FETCH (UID 1 BODY[] {' + str(len(message)).encode() +
                                             b'}\r\n' + message + b')\r\n')
                    elif command == 'NOOP':
                        entered.set()
                        release.wait(4)
                    elif command == 'LOGOUT':
                        self.wfile.write(b'* BYE logging out\r\n')
                    self.wfile.write(tag.encode() + b' OK completed\r\n')
                    if command == 'LOGOUT':
                        break
            except OSError:
                pass
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True
        def get_request(self):
            stream, address = super().get_request()
            try:
                return context.wrap_socket(stream, server_side=True), address
            except BaseException:
                stream.close()
                raise
    server = Server(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    create_context = ssl.create_default_context
    def client_context():
        return create_context(cafile=str(cert))
    monkeypatch.setattr(ssl, 'create_default_context', client_context)
    try:
        yield server.server_address[1], commands, entered, release, create_context
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def connect(port, **kwargs):
    from core.security.imap_transport import connect_imap
    return connect_imap('127.0.0.1', port, username='alice', password='SYNTHETIC_PASSWORD', **kwargs)


def test_real_tls_authentication_list_and_logout(imap_server):
    port, commands, *_ = imap_server
    client = connect(port)
    assert client.list()[0] == 'OK'
    assert b'INBOX' in client.list()[1][0]
    assert client.logout()[0] == 'BYE'
    assert any(b'LOGIN alice "SYNTHETIC_PASSWORD"' in command for command in commands)


def test_connection_disable_interrupts_actual_imap_read_and_does_not_revive(imap_server):
    from core.security.connections import ConnectionControl, service_id
    port, _, entered, release, _ = imap_server
    identifier, _ = service_id(f'imaps://127.0.0.1:{port}')
    control = ConnectionControl(catalog=lambda: [dict(id=identifier)])
    client = connect(port)
    errors = []
    def read():
        try:
            client.noop()
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=read)
    worker.start()
    assert entered.wait(2)
    try:
        disabled = control.save(identifier, enabled=False, expected_revision=0)
        worker.join(2)
        assert not worker.is_alive() and errors
        with pytest.raises(PermissionError):
            connect(port)
        control.save(identifier, enabled=True, expected_revision=disabled['revision'])
        with pytest.raises((PermissionError, OSError)):
            client.list()
        release.set()
        fresh = connect(port)
        assert fresh.list()[0] == 'OK'
        fresh.logout()
    finally:
        release.set()
        client.shutdown()
        worker.join(2)


def test_untrusted_tls_certificate_rejected_before_login(imap_server, monkeypatch):
    port, commands, *_, create_context = imap_server
    monkeypatch.setattr(ssl, 'create_default_context', create_context)
    with pytest.raises(ssl.SSLCertVerificationError):
        connect(port)
    assert not any(b'LOGIN' in command for command in commands)


def test_hostname_resolving_to_private_address_cannot_use_numeric_local_exception(imap_server, monkeypatch):
    from core.security.imap_transport import connect_imap
    port, commands, *_ = imap_server
    lookup = socket.getaddrinfo
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, *a, **kw: lookup('127.0.0.1', *a, **kw))
    with pytest.raises(PermissionError, match='private|special'):
        connect_imap('mail.example.test', port, username='alice', password='SYNTHETIC_PASSWORD')
    assert not commands


def test_raw_reference_is_not_sent_as_an_imap_password(imap_server):
    from core.security.imap_transport import connect_imap
    from core.security.credential_values import REFERENCE
    port, commands, *_ = imap_server
    with pytest.raises(PermissionError):
        connect_imap('127.0.0.1', port, username='alice', password=REFERENCE + 'a' * 32)
    assert not any(b'LOGIN' in command for command in commands)


def test_imaps_service_cannot_be_sent_through_http_transport():
    from core.security.http_transport import destination
    with pytest.raises(ValueError):
        destination('imaps://127.0.0.1:993')


def mailbox(port, tmp_path):
    from tests.email_domain import EmailService
    from core.security.email_credentials import EmailCredentialStore
    service = EmailService(tmp_path / 'email.db')
    row = service.save_account({'host': '127.0.0.1', 'port': port, 'user': 'alice',
                                'password': 'SYNTHETIC_PASSWORD'})
    store = EmailCredentialStore(service.db_path, user_id='local-default')
    store.migrate()
    return store, row['id']


def test_original_account_connector_uses_current_password_after_rotation(imap_server, tmp_path):
    port, commands, *_ = imap_server
    store, account = mailbox(port, tmp_path)
    original = store.connect(account)
    assert original.list()[0] == 'OK'
    view = store.inventory()[0]
    store.save_password(account, 'SYNTHETIC_REPLACEMENT', expected_revision=view['revision'])
    with pytest.raises(PermissionError):
        original.list()
    original.shutdown()
    fresh = store.connect(account)
    assert fresh.list()[0] == 'OK'
    fresh.logout()
    assert any(b'LOGIN alice "SYNTHETIC_REPLACEMENT"' in command for command in commands)


def test_account_keychain_lock_interrupts_inflight_imap_and_blocks_reauthentication(imap_server, tmp_path, isolated_keychain):
    import ctypes
    port, commands, entered, release, _ = imap_server
    store, account = mailbox(port, tmp_path)
    client = store.connect(account)
    errors = []
    def read():
        try:
            client.noop()
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=read)
    worker.start()
    assert entered.wait(2)
    backend = isolated_keychain(store.path)
    backend.security.SecKeychainLock.argtypes = [ctypes.c_void_p]
    try:
        assert backend.security.SecKeychainLock(backend.keychain) == 0
        worker.join(2)
        assert not worker.is_alive() and errors
        attempts = sum(b'LOGIN' in command for command in commands)
        with pytest.raises(OSError):
            store.connect(account)
        assert sum(b'LOGIN' in command for command in commands) == attempts
    finally:
        release.set()
        client.shutdown()
        worker.join(2)


def test_existing_email_business_protocol_works_with_trusted_account_opener(imap_server, tmp_path):
    from tests.email_domain import EmailService
    port, _, *_ = imap_server
    store, account = mailbox(port, tmp_path)
    service = EmailService(store.path, opener=lambda: store.connect(account))
    assert service.test_connection({'id': account})['folder_accessible'] is True
    assert service.list_folders(account_id=account)['folders'] == [{'name': 'INBOX', 'label': 'INBOX'}]
    result = service.fetch_messages(account_id=account)
    assert result['cursors'] == {'imap_uid': '1', 'imap_uidvalidity': '7'}
    assert len(result['items']) == 1
    assert result['items'][0]['title'] == 'Synthetic email'
    assert 'SYNTHETIC_PASSWORD' not in str(result)
    assert 'stella-keychain:' not in str(result)
    assert service.get_cursors(account) == {}, 'Read must only propose cursors'

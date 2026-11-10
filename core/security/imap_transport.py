"""Trusted mailbox connector transport; no raw network callback for extensions."""
from contextvars import copy_context
import imaplib
import socket
import ssl

from core.security.connections import ConnectionControl, service_id
from core.security.credential_values import REFERENCE
from core.security.http_transport import _IO, destination


class _IMAP(imaplib.IMAP4_SSL):
    def __init__(self, host, port, io, target, *, timeout):
        self._io, self._target = io, target
        self._shutdown_started = False
        try:
            super().__init__(host, port, ssl_context=ssl.create_default_context(), timeout=timeout)
        except BaseException:
            self.shutdown()
            raise

    def _create_socket(self, timeout):
        family, address = self._target.addresses[0]
        stream = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        stream.settimeout(timeout)
        self._io.attach_socket(stream)
        stream.connect(address)
        self._io.check()
        stream = self.ssl_context.wrap_socket(stream, server_hostname=self.host, do_handshake_on_connect=False)
        self._io.attach_socket(stream)
        stream.do_handshake()
        self._io.check()
        return stream

    def _command(self, name, *args):
        self._io.check()
        return super()._command(name, *args)

    def shutdown(self):
        if self._shutdown_started:
            return
        self._shutdown_started = True
        # Interrupt a pending read before disposing its buffered reader.
        self._io.close()
        if getattr(self, 'file', None) is not None:
            self.file.close()


def connect_imap(host, port, *, username, password, check=None, cancel_event=None, timeout=30):
    """One TLS-authenticated account, pinned DNS and revocable service lease."""
    if (not isinstance(password, str) or not password or password.startswith(REFERENCE) or
            not isinstance(username, str) or not username or
            any(char in username + password for char in '\r\n\0')):
        raise PermissionError('Mailbox authentication requires a valid resolved credential')
    literal_host = '[' + host + ']' if ':' in host else host
    identifier, _ = service_id(f'imaps://{literal_host}:{port}')
    lease = ConnectionControl().begin(identifier)
    io = _IO([lease])
    client = None
    try:
        if check is not None:
            context = copy_context()
            lease.guards.append(lambda: context.copy().run(check))
        if cancel_event is not None:
            def active():
                if cancel_event.is_set():
                    raise PermissionError('Mailbox operation was cancelled')
            lease.guards.append(active)
        # Reuse the address rules, not the HTTP protocol. Only an explicitly
        # supplied numeric local endpoint receives the local-service exception.
        target_url = f'https://{literal_host}:{port}'
        target = destination(target_url, configured_origin=service_id(target_url)[1])
        io.check()
        client = _IMAP(host, port, io, target, timeout=timeout)
        try:
            client.login(username, password)
        except imaplib.IMAP4.error:
            client.shutdown()
            raise PermissionError('Mailbox authentication failed; check the original account settings') from None
        io.check()
        return client
    except BaseException:
        io.close()
        if client is not None:
            client.shutdown()
        raise

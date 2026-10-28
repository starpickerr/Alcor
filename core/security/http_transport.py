"""Core HTTP transport: pin checked addresses and own every in-flight socket.

This module is a transport, not a resource grant. Callers must also supply
trusted provenance and pass SecurityService's egress check before sending data.
No proxy environment, redirects, CONNECT, upgrades or generic tunnels are used.
"""

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import threading
from urllib.parse import urlsplit

from core.security.connections import ConnectionControl, service_id


@dataclass(frozen=True)
class Destination:
    host: str
    port: int
    secure: bool
    path: str
    addresses: tuple


def destination(url, *, configured_origin=None, allow_configured_local=True):
    if not isinstance(url, str) or any(ord(char) < 33 for char in url) or "\\" in url:
        raise ValueError("Invalid HTTP destination")
    parsed = urlsplit(url)
    identifier, origin = service_id(url)
    if parsed.fragment or not parsed.port and parsed.netloc.endswith(":"):
        raise ValueError("Invalid HTTP destination")
    host = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not 0 < port < 65536:
        raise ValueError("Invalid service port")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    # A numeric private service explicitly configured by its owner is scoped
    # to that exact origin. Hostname rebinding cannot obtain this exception.
    from core.security.connections import connection_catalog

    configured_local = bool(
        literal
        and allow_configured_local
        and (
            configured_origin == origin
            or any(
                row.get("id") == identifier and row.get("endpoint") == origin
                for row in connection_catalog()
            )
        )
    )
    addresses = tuple(
        dict.fromkeys(
            (family, address)
            for family, socktype, protocol, _, address in socket.getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            )
        )
    )
    if not addresses:
        raise PermissionError("Destination has no usable addresses")
    for family, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_unspecified
            or getattr(ip, "ipv4_mapped", None)
            or getattr(ip, "sixtofour", None)
            or getattr(ip, "teredo", None)
            or ip in ipaddress.ip_network("64:ff9b::/96")
            or ip in ipaddress.ip_network("64:ff9b:1::/48")
            or not ip.is_global
            and not (configured_local and ip == literal)
        ):
            raise PermissionError(
                "Destination resolves to a disallowed private or special address"
            )
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return Destination(host, port, parsed.scheme == "https", path, addresses)


class _IO:
    def __init__(self, leases):
        self.leases = leases
        self.lock = threading.Lock()
        self.read_lock = threading.Lock()
        self.socket = None
        self.response = None
        self.cancelled = False
        for lease in leases:
            lease.attach(self.cancel)

    def check(self):
        for lease in self.leases:
            lease.check()
        with self.lock:
            if self.cancelled:
                raise PermissionError("HTTP request was revoked or closed")

    def attach_socket(self, stream):
        with self.lock:
            if self.cancelled:
                stream.close()
                raise PermissionError(
                    "HTTP request was revoked before socket registration"
                )
            self.socket = stream
        self.check()

    def cancel(self):
        with self.lock:
            self.cancelled = True
            stream, response = self.socket, self.response
            self.socket = None
        # Interrupt synchronously, before the owner mutation returns. Buffered
        # reader disposal may wait for its reader lock, so that part is async.
        if stream is not None:
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            stream.close()
        if response is not None:

            def dispose():
                with self.read_lock:
                    response.close()

            threading.Thread(target=dispose, name="http-dispose", daemon=True).start()

    def close(self):
        self.cancel()
        for lease in self.leases:
            lease.detach(self.cancel)
            lease.close()


class Response:
    def __init__(self, raw, io, *, maximum):
        self.raw, self.io = raw, io
        self.status = raw.status
        self.reason = raw.reason
        self.headers = raw.headers
        self.maximum, self.total = maximum, 0

    def _read(self, method, amount):
        self.io.check()
        with self.io.read_lock:
            self.io.check()
            try:
                value = method(
                    min(amount, self.maximum - self.total + 1)
                    if amount is not None
                    else self.maximum - self.total + 1
                )
            except (OSError, http.client.HTTPException):
                self.io.check()
                raise
        self.io.check()
        self.total += len(value)
        if self.total > self.maximum:
            self.close()
            raise ValueError("HTTP response exceeded the allowed size")
        return value

    def read(self, amount=None):
        return self._read(self.raw.read, amount)

    def read1(self, amount=65536):
        return self._read(self.raw.read1, amount)

    def readline(self, amount=-1):
        return self._read(self.raw.readline, None if amount < 0 else amount)

    def close(self):
        self.io.close()

    def __enter__(self):
        self.io.check()
        return self

    def __exit__(self, *args):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line


def open_request(
    method,
    url,
    *,
    body=None,
    headers=None,
    timeout=30,
    category=None,
    maximum=128 * 1024 * 1024,
    permit=None,
    configured_origin=None,
    cancel_event=None,
    related_services=(),
    allow_configured_local=True,
):
    if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
        raise PermissionError("This HTTP operation is not supported")
    if body is not None and not isinstance(body, bytes):
        raise ValueError("HTTP body must be an immutable byte snapshot")
    if permit is not None or body is not None or method not in {"GET", "HEAD"}:
        from core.security.egress import EgressPermit

        if not isinstance(permit, EgressPermit):
            raise PermissionError(
                "External data requires a SecurityService egress receipt"
            )
        permit.check_request(url, body or b"", method=method, headers=headers, category=category)
    if category not in {None, "public-browser", "public-packages"}:
        raise ValueError("Unknown transport category")
    headers = dict(headers or {})
    if any(
        key.lower()
        in {
            "host",
            "connection",
            "upgrade",
            "transfer-encoding",
            "content-length",
            "proxy-authorization",
            "proxy-connection",
        }
        for key in headers
    ):
        raise PermissionError("Caller cannot replace HTTP transport control headers")
    identifier, _ = service_id(url)
    control = ConnectionControl()
    leases = [control.begin(identifier)]
    io = None
    try:
        if category:
            leases.append(control.begin(category))
        for service in set(related_services) - {identifier, category}:
            leases.append(control.begin(service))
        if permit is not None:
            for lease in leases:
                lease.guards.append(permit.check)
        if cancel_event is not None:

            def active():
                if cancel_event.is_set():
                    raise PermissionError("HTTP request was cancelled")

            for lease in leases:
                lease.guards.append(active)
        io = _IO(leases)
        target = destination(url, configured_origin=configured_origin,
                             allow_configured_local=category is None and allow_configured_local)
        io.check()
        # Exactly one DNS resolution above. connect() receives the numeric
        # sockaddr directly, while TLS verifies the original hostname.
        connection = http.client.HTTPConnection(
            target.host, target.port, timeout=timeout
        )
        family, address = target.addresses[0]
        stream = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        stream.settimeout(timeout)
        io.attach_socket(stream)
        stream.connect(address)
        io.check()
        if target.secure:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_default_certs()
            context.set_alpn_protocols(["http/1.1"])
            stream = context.wrap_socket(stream, server_hostname=target.host, do_handshake_on_connect=False)
            io.attach_socket(stream)
            stream.do_handshake()
            io.check()
        connection.sock = stream
        io.check()
        connection.request(
            method, target.path, body=body, headers={**headers, "Connection": "close"}
        )
        io.check()
        raw = connection.getresponse()
        with io.lock:
            io.response = raw
        io.check()
        if 300 <= raw.status < 400 or raw.status == 101:
            raise PermissionError(
                "HTTP redirects and protocol upgrades require a separately authorized destination"
            )
        return Response(raw, io, maximum=maximum)
    except BaseException:
        if io:
            io.close()
        else:
            for lease in leases:
                lease.close()
        raise

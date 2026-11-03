"""Core HTTP transport: pin checked addresses and own every in-flight socket.

This module is a transport, not a resource grant. Callers must also supply
trusted provenance and pass SecurityService's egress check before sending data.
Only the saved HTTP proxy is used for configured services; redirects stay disabled.
"""

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import threading
import base64
from contextvars import copy_context
from urllib.parse import urlsplit

from core.security.connections import ConnectionControl, service_id


@dataclass(frozen=True)
class Destination:
    host: str
    port: int
    secure: bool
    path: str
    addresses: tuple


def destination(url, *, configured_origin=None, allow_configured_local=True, local_networks=(), resolve=True):
    if not isinstance(url, str) or any(ord(char) < 33 for char in url) or "\\" in url:
        raise ValueError("Invalid HTTP destination")
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'}:
        raise ValueError('HTTP transport requires an HTTP(S) destination')
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
    configured_local = allow_configured_local and configured_origin == origin
    private_ranges = tuple(map(ipaddress.ip_network, ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7']))
    networks = tuple(ipaddress.ip_network(value, strict=True) for value in local_networks)
    if any(not any(network.version == allowed.version and network.subnet_of(allowed) for allowed in private_ranges) for network in networks):
        raise ValueError('Local service networks must be explicit private ranges')
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    if not resolve and literal is None:
        return Destination(host, port, parsed.scheme == "https", path, ())
    addresses = tuple(
        dict.fromkeys(
            (family, address)
            for family, socktype, protocol, _, address in (socket.getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
            ) if resolve else ())
        )
    )
    if not resolve:
        addresses = ((socket.AF_INET6 if literal.version == 6 else socket.AF_INET, (host, port)),)
    if not addresses:
        raise PermissionError("Destination has no usable addresses")
    local = [ipaddress.ip_address(address[0]).is_global for _, address in addresses]
    if any(local) and not all(local):
        raise PermissionError('Mixed public and private DNS answers are not allowed')
    for family, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if (
            family not in {socket.AF_INET, socket.AF_INET6}
            or ip.is_multicast
            or ip.is_reserved and not ip.is_loopback
            or ip.is_link_local
            or ip.is_unspecified
            or getattr(ip, "ipv4_mapped", None)
            or getattr(ip, "sixtofour", None)
            or getattr(ip, "teredo", None)
            or ip in ipaddress.ip_network("64:ff9b::/96")
            or ip in ipaddress.ip_network("64:ff9b:1::/48")
            or not ip.is_global
            and not (configured_local and (
                literal is not None and ip == literal and (ip.is_loopback or any(ip in network for network in private_ranges))
                or host == 'localhost' and ip.is_loopback
                or any(ip in network for network in networks)
            ))
        ):
            raise PermissionError(
                "Destination resolves to a disallowed private or special address"
            )
    return Destination(host, port, parsed.scheme == "https", path, addresses)


def configured_route(url, *, owner=None, config=None):
    """Snapshot the owner-selected route without DNS or opening a socket."""
    from config import conf
    from core.security.connections import ConnectionStore, connection_catalog, current_user
    origin = service_id(url)[1]
    rows = connection_catalog()
    row = next((row for row in rows if row.get('endpoint') == origin
                and any(value.startswith(owner) for value in row.get('owners', []))), None) if owner else None
    if config is not None:
        from copy import deepcopy
        configuration = deepcopy(dict(config))
        if service_id(config.get('url') or '')[1] != origin:
            raise PermissionError('MCP route does not match its configured origin')
        networks = tuple(config.get('localNetworks') or ())
    else:
        networks = tuple((row or {}).get('localNetworks') or ())
    configured = config is not None or row is not None
    configuration_store = conf()
    network_revision = getattr(configuration_store, 'network_revision', 0)
    saved_proxy = configuration_store.get('proxy') or ''
    proxy = saved_proxy if configured else ''
    # Validate options before authorization can queue a prompt.
    destination(url, configured_origin=origin if configured else None, local_networks=networks, resolve=False)
    if proxy:
        proxy_id = service_id(proxy_endpoint(proxy)[0])[0]
        proxy_user = current_user()
        proxy_store = ConnectionStore()
        proxy_state = proxy_store.state(proxy_user, proxy_id)
        if not proxy_state['enabled']:
            raise PermissionError('Proxy connection is disabled')
    context = copy_context()
    def check():
        def current():
            if (conf() is not configuration_store or getattr(conf(), 'network_revision', 0) != network_revision
                    or (conf().get('proxy') or '') != saved_proxy):
                raise PermissionError('Proxy configuration changed; restart the request')
            if proxy and proxy_store.state(proxy_user, proxy_id) != proxy_state:
                raise PermissionError('Proxy connection changed; restart the request')
            if config is not None:
                if dict(config) != configuration:
                    raise PermissionError('MCP route configuration changed')
                from core.security.mcp_credentials import StoredMcpConfig, _config
                if isinstance(config, StoredMcpConfig):
                    _, saved, _ = config.credential_store._read()
                    if config['name'] not in saved or _config(config['name'], saved[config['name']]) != configuration:
                        raise PermissionError('Saved MCP route changed; reconnect')
            if row is not None and row not in connection_catalog():
                raise PermissionError('Configured receiving service changed')
        context.copy().run(current)
    return dict(configured_origin=origin if configured else None, proxy=proxy,
                local_networks=networks, route_check=check)


def proxy_endpoint(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or not parsed.hostname or parsed.path not in {'', '/'}
            or parsed.query or parsed.fragment or any(ord(char) < 33 for char in value) or "\\" in value):
        raise PermissionError('Use an HTTP or Mixed proxy URL (http://host:port) in network settings')
    host = '[' + parsed.hostname + ']' if ':' in parsed.hostname else parsed.hostname
    endpoint = f'http://{host}:{parsed.port or 80}'
    authentication = None
    if parsed.username is not None:
        from urllib.parse import unquote
        authentication = 'Basic ' + base64.b64encode(
            (unquote(parsed.username) + ':' + unquote(parsed.password or '')).encode()).decode('ascii')
    return endpoint, authentication


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
    proxy=None,
    local_networks=(),
    route_check=None,
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
    identifier, origin = service_id(url)
    if proxy and (category is not None or configured_origin != origin):
        raise PermissionError('Proxy requires an exact configured service origin')
    proxy_url, proxy_auth = proxy_endpoint(proxy) if proxy else (None, None)
    control = ConnectionControl()
    leases = [control.begin(identifier)]
    io = None
    try:
        if proxy_url:
            leases.append(control.begin(service_id(proxy_url)[0]))
        if route_check:
            for lease in leases:
                lease.guards.append(route_check)
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
        io.check()
        target = destination(url, configured_origin=configured_origin, local_networks=local_networks,
                             allow_configured_local=category is None and allow_configured_local, resolve=not proxy_url)
        if proxy_url:
            # The configured proxy resolves the target. Never bless FakeIP globally.
            try:
                literal = ipaddress.ip_address(target.host)
            except ValueError:
                literal = None
            if literal is not None or target.host == 'localhost':
                destination(url, configured_origin=configured_origin, local_networks=local_networks)
        peer = destination(proxy_url, configured_origin=proxy_url) if proxy_url else target
        io.check()
        # Exactly one DNS resolution above. connect() receives the numeric
        # sockaddr directly, while TLS verifies the original hostname.
        connection = http.client.HTTPConnection(
            peer.host, peer.port, timeout=timeout
        )
        family, address = peer.addresses[0]
        stream = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        stream.settimeout(timeout)
        io.attach_socket(stream)
        stream.connect(address)
        io.check()
        if proxy_url and target.secure:
            tunnel_headers = {'Proxy-Authorization': proxy_auth} if proxy_auth else {}
            connection.set_tunnel(target.host, target.port, headers=tunnel_headers)
            connection.sock = stream
            connection._tunnel()
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
            method, url if proxy_url and not target.secure else target.path, body=body,
            headers={**headers, **({'Proxy-Authorization': proxy_auth} if proxy_auth and not target.secure else {}),
                     'Host': ('[' + target.host + ']' if ':' in target.host else target.host) + ':' + str(target.port),
                     "Connection": "close"}
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

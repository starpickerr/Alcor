"""Destination validation and revocation use real local sockets and synthetic data."""

import http.server
import socket
import threading

import pytest


def test_public_transport_rejects_private_and_mixed_dns_answers(monkeypatch):
    from core.security.http_transport import destination
    from core.security import connections

    monkeypatch.setattr(connections, "connection_catalog", lambda: [])
    for values in [
        ("127.0.0.1",),
        ("10.0.0.1",),
        ("::1",),
        ("169.254.169.254",),
        ("8.8.8.8", "127.0.0.1"),
    ]:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *args, **kwargs: [
                (
                    socket.AF_INET6 if ":" in ip else socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (ip, 443),
                )
                for ip in values
            ],
        )
        with pytest.raises(PermissionError):
            destination("https://untrusted.invalid/private")
    for url in (
        "file:///etc/passwd",
        "https://user:password@example.com/",
        "https://example.com/#fragment",
        "https://example.com\\@127.0.0.1/",
    ):
        with pytest.raises((ValueError, PermissionError)):
            destination(url)


@pytest.fixture
def local_service(monkeypatch):
    from core.security import connections
    from core.security.connections import service_id

    received = []
    body_started, body_finish = threading.Event(), threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/secret-target")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", "4")
            self.end_headers()
            if self.path == "/slow":
                self.wfile.flush()
                body_started.set()
                body_finish.wait(5)
            try:
                self.wfile.write(b"DATA")
            except OSError:
                pass

        def do_POST(self):
            import json

            received.append(self.path)
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert isinstance(request["messages"], list)
            assert self.headers["Authorization"] == "Bearer synthetic-test-key"
            body = b'{"id":"synthetic-response","choices":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}"
    identifier, origin = service_id(url)
    monkeypatch.setattr(
        connections,
        "connection_catalog",
        lambda: [
            dict(
                id=identifier,
                endpoint=origin,
                owners=["model:local"],
                name="Synthetic local model",
            )
        ],
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield url, received, body_started, body_finish
    finally:
        body_finish.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_redirects_are_not_followed_and_proxy_environment_is_ignored(
    local_service, monkeypatch
):
    from core.security.http_transport import open_request

    url, received, _, _ = local_service
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    with open_request("GET", url + "/safe") as result:
        assert result.read() == b"DATA"
    with pytest.raises(PermissionError, match="redirect|Redirect"):
        open_request("GET", url + "/redirect")
    assert received == ["/safe", "/redirect"]


def test_disabling_connection_interrupts_real_stream_and_old_response_stays_revoked(
    local_service,
):
    from core.security.http_transport import open_request
    from core.security.connections import ConnectionControl, service_id

    url, _, started, finish = local_service
    identifier, _ = service_id(url)
    response = open_request("GET", url + "/slow")
    outcomes = []

    def read():
        try:
            outcomes.append(response.read())
        except (PermissionError, OSError):
            outcomes.append("revoked")

    reader = threading.Thread(target=read)
    reader.start()
    assert started.wait(2)
    control = ConnectionControl()
    disabled = control.save(identifier, enabled=False, expected_revision=0)
    reader.join(2)
    assert not reader.is_alive() and outcomes == ["revoked"]
    control.save(identifier, enabled=True, expected_revision=disabled["revision"])
    finish.set()
    with pytest.raises(PermissionError):
        response.read()
    with open_request("GET", url + "/fresh") as fresh:
        assert fresh.read() == b"DATA"


def test_openai_model_uses_controlled_transport_and_connection_revocation(
    local_service,
):
    from models.openai.openai_http_client import OpenAIHTTPClient
    from tests.fixtures.egress import approve_synthetic_egress
    from core.security.connections import ConnectionControl, service_id

    url, received, _, _ = local_service
    client = OpenAIHTTPClient(api_key="synthetic-test-key", api_base=url)
    with approve_synthetic_egress(url):
        result = client.chat_completions(
            model="test",
            messages=[{"role": "user", "content": "synthetic private context"}],
        )
    assert result["id"] == "synthetic-response"
    assert received == ["/chat/completions"]
    identifier, _ = service_id(url)
    ConnectionControl().save(identifier, enabled=False, expected_revision=0)
    with pytest.raises(PermissionError):
        client.chat_completions(model="test", messages=[])
    assert received == ["/chat/completions"]


@pytest.mark.parametrize("provider", ["xunfei", "dashscope", "qwen", "zhipu", "glm-4"])
def test_unadapted_sdk_and_websocket_providers_do_not_use_uncontrolled_network(
    provider,
):
    from models.bot_factory import create_bot

    with pytest.raises(PermissionError, match="controlled|adapter|受控"):
        create_bot(provider)


def test_browser_cannot_start_private_or_uncontrolled_processes_when_protection_is_unavailable(monkeypatch):
    from agent.tools.browser import browser_service
    def forbidden_driver():
        raise AssertionError('Browser driver must not start before managed isolation is ready')
    monkeypatch.setattr(browser_service, 'sync_playwright', forbidden_driver)
    service = browser_service.BrowserService({'cdp_endpoint': 'http://127.0.0.1:1'})
    with pytest.raises(PermissionError, match='controlled|隔离|isolation|protection'):
        service._launch_browser()


@pytest.mark.parametrize('tool_name', ['fetch', 'search'])
def test_public_connection_switch_blocks_web_tools_before_any_socket(monkeypatch, tmp_path, tool_name):
    from core.security.connections import ConnectionControl
    ConnectionControl().save('public-browser', enabled=False, expected_revision=0)
    def forbidden_socket(*args, **kwargs):
        raise AssertionError('Disabled public connection reached a socket')
    monkeypatch.setattr(socket, 'socket', forbidden_socket)
    if tool_name == 'fetch':
        from agent.tools.web_fetch.web_fetch import WebFetch
        with pytest.raises(PermissionError):
            WebFetch({'cwd': str(tmp_path)})._safe_get('https://example.invalid')
    else:
        from agent.tools.web_search.web_search import WebSearch
        with pytest.raises(PermissionError):
            WebSearch()._post('https://example.invalid', json={'query': 'synthetic task'})


def test_public_browser_cannot_reuse_a_configured_private_service(local_service):
    from core.security.http_transport import open_request
    url, received, _, _ = local_service
    with pytest.raises(PermissionError, match='private|special'):
        open_request('GET', url + '/owner-only', category='public-browser')
    assert received == []


def test_disabling_connection_interrupts_a_real_stalled_tls_handshake(monkeypatch):
    import time
    from core.security import connections
    from core.security.connections import ConnectionControl, service_id
    from core.security.http_transport import open_request

    server = socket.socket()
    server.bind(('127.0.0.1', 0))
    server.listen()
    url = f'https://127.0.0.1:{server.getsockname()[1]}'
    identifier, origin = service_id(url)
    monkeypatch.setattr(connections, 'connection_catalog', lambda: [dict(id=identifier, endpoint=origin)])
    started, closed = threading.Event(), threading.Event()

    def stall_tls():
        stream, _ = server.accept()
        try:
            stream.settimeout(3)
            assert stream.recv(65536)  # Receive the real TLS ClientHello.
            started.set()
            if stream.recv(1) == b'':
                closed.set()
        except OSError:
            pass
        finally:
            stream.close()

    peer = threading.Thread(target=stall_tls, daemon=True)
    peer.start()
    outcomes = []
    def request():
        try:
            open_request('GET', url, timeout=3)
        except Exception as error:
            outcomes.append(type(error))
    client = threading.Thread(target=request, daemon=True)
    client.start()
    try:
        assert started.wait(2)
        began = time.monotonic()
        ConnectionControl().save(identifier, enabled=False, expected_revision=0)
        client.join(.6)
        assert not client.is_alive(), 'Revoked TLS handshake still owns a live socket'
        assert closed.wait(.3), 'Remote peer did not observe revoked TLS socket close'
        assert time.monotonic() - began < 1
        assert outcomes
    finally:
        server.close()
        client.join(4)
        peer.join(4)


def test_voice_http_respects_the_same_connection_switch_before_sending(local_service, monkeypatch, tmp_path):
    from config import conf
    from core.security.connections import ConnectionControl, service_id
    from voice.openai.openai_voice import OpenaiVoice
    from bridge.reply import ReplyType
    url, received, _, _ = local_service
    monkeypatch.setitem(conf(), 'open_ai_api_base', url)
    monkeypatch.setitem(conf(), 'open_ai_api_key', 'synthetic-test-key')
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'tmp').mkdir()
    identifier, _ = service_id(url)
    ConnectionControl().save(identifier, enabled=False, expected_revision=0)
    result = OpenaiVoice().textToVoice('Synthetic private text')
    assert result.type == ReplyType.ERROR
    assert received == [], 'Disabled voice service received private text'


@pytest.mark.parametrize('provider', ['google', 'azure', 'elevenlabs', 'edge', 'xunfei', 'tencent', 'dashscope', 'baidu'])
def test_voice_sdk_without_controlled_adapter_is_blocked_before_loading(provider):
    from voice.factory import create_voice
    with pytest.raises(PermissionError, match='controlled|adapter|受控'):
        create_voice(provider)


def test_vision_download_obeys_live_connection_intent(local_service):
    from core.security.connections import ConnectionControl, service_id
    from agent.tools.vision.vision import Vision
    url, received, _, _ = local_service
    identifier, _ = service_id(url)
    ConnectionControl().save(identifier, enabled=False, expected_revision=0)
    with pytest.raises(PermissionError):
        Vision._download_to_data_url(url + '/image')
    assert received == []


def test_aliyun_asr_does_not_open_an_uncontrolled_https_connection(monkeypatch):
    from voice.ali import ali_api
    from core.security.connections import ConnectionStore, service_id
    url = 'https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr'
    identifier, _ = service_id(url)
    ConnectionStore().save('local-default', identifier, False, 0)
    def forbidden(*args, **kwargs):
        raise AssertionError('Disabled voice service opened a raw TLS connection')
    monkeypatch.setattr('http.client.HTTPSConnection', forbidden)
    with pytest.raises(PermissionError):
        ali_api.speech_to_text_aliyun(url, b'SYNTHETIC_PCM', 'synthetic-app', 'synthetic-token')

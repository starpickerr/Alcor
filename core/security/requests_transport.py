"""Requests-shaped Core transport with live egress and socket checks.

Encoding/Response compatibility comes from requests; its network session,
proxy handling, cookies, redirects and adapter escape hatches are not exposed.
"""

import http.client
import socket
import ssl

import requests as _requests
from requests import Response
from requests import exceptions
from requests.structures import CaseInsensitiveDict
from requests.utils import get_encoding_from_headers

from core.security.connections import ConnectionControl, service_id
from core.security.http_transport import open_request
from core.security.service import SecurityService

RequestException = _requests.RequestException
HTTPError = _requests.HTTPError
Timeout = _requests.Timeout
ConnectionError = _requests.ConnectionError
compat = _requests.compat



class _Raw:
    def __init__(self, response):
        self.response = response

    def read(self, amount=None, decode_content=False):
        return self.response.read(amount)

    def stream(self, amount=65536, decode_content=False):
        while True:
            chunk = self.response.read1(amount or 65536)
            if not chunk:
                break
            yield chunk

    def close(self):
        self.response.close()

    def release_conn(self):
        self.close()


def request(method, url, *, _category=None, _source="private:model-context", _cancel_event=None, **kwargs):
    # Model callers may specify an endpoint; they cannot introduce an arbitrary
    # proxy/tunnel, client certificate, cookie jar or unverified TLS session.
    accepted = {
        "params",
        "data",
        "json",
        "headers",
        "files",
        "timeout",
        "stream",
        "proxies",
        "allow_redirects",
        "verify",
    }
    if set(kwargs) - accepted or kwargs.get("proxies") or kwargs.get("verify") is False:
        raise PermissionError("This controlled transport option is not safely supported")
    headers = dict(kwargs.get("headers") or {})
    if any(key.lower() == "cookie" for key in headers):
        raise PermissionError("Model transport does not use a private browser login")
    prepared = _requests.Request(
        method=method,
        url=url,
        params=kwargs.get("params"),
        data=kwargs.get("data"),
        json=kwargs.get("json"),
        files=kwargs.get("files"),
        headers=headers,
    ).prepare()
    body = prepared.body
    if isinstance(body, str):
        body = body.encode("utf-8")
    if body is not None and not isinstance(body, bytes):
        raise PermissionError(
            "Streaming uploads require a dedicated provenance-aware connector"
        )
    headers = {
        key: value
        for key, value in prepared.headers.items()
        if key.lower() != "content-length"
    }
    headers["Accept-Encoding"] = "identity"
    identifier, _ = service_id(prepared.url)
    # Check intent before presenting an approval; a disabled service cannot
    # queue prompts or contact DNS merely because a model request was made.
    control = ConnectionControl()
    with control.begin(identifier):
        pass
    if _category:
        with control.begin(_category):
            pass
    # These labels describe the Core request assembler's inputs, never a field
    # in a model response, tool argument or extension-supplied annotation.
    sources = (_source,)
    permit = SecurityService.authorize_egress(
        prepared.url, body or b"", sources=sources, cancel_event=_cancel_event,
        method=method, headers=headers, category=_category
    )
    timeout = kwargs.get("timeout", 60)
    if isinstance(timeout, tuple):
        timeout = min(value for value in timeout if value is not None)
    if timeout is None:
        timeout = 60
    try:
        raw = open_request(
            method.upper(),
            prepared.url,
            body=body,
            headers=headers,
            timeout=timeout,
            permit=permit,
            category=_category,
            cancel_event=_cancel_event,
        )
        if raw.headers.get("Content-Encoding", "identity").lower() not in {
            "identity",
            "",
        }:
            raw.close()
            raise PermissionError(
                "Model service did not honor the required response encoding"
            )
        response = Response()
        response.status_code, response.reason, response.url = (
            raw.status,
            raw.reason,
            prepared.url,
        )
        response.request = prepared
        response.headers = CaseInsensitiveDict(raw.headers)
        response.encoding = get_encoding_from_headers(response.headers)
        response.raw = _Raw(raw)
        if not kwargs.get("stream"):
            try:
                response._content = response.content
            finally:
                response.close()
        return response
    except PermissionError:
        raise
    except (socket.timeout, TimeoutError) as error:
        raise exceptions.Timeout("Controlled model request timed out") from error
    except ssl.SSLError as error:
        raise exceptions.SSLError("Model service TLS verification failed") from error
    except (OSError, http.client.HTTPException) as error:
        raise exceptions.ConnectionError(
            "Controlled model connection failed"
        ) from error


def get(url, **kwargs):
    return request("GET", url, **kwargs)


def post(url, **kwargs):
    return request("POST", url, **kwargs)


class _RequestsAdapter:
    Response = _requests.Response
    exceptions = _requests.exceptions
    compat = _requests.compat
    Timeout = _requests.Timeout
    ConnectionError = _requests.ConnectionError
    HTTPError = _requests.HTTPError

    def __init__(self, source, category=None):
        self.source, self.category = source, category

    def get(self, url, **kwargs):
        return request('GET', url, _category=self.category, _source=self.source, **kwargs)

    def post(self, url, **kwargs):
        return request('POST', url, _category=self.category, _source=self.source, **kwargs)


web_requests = _RequestsAdapter('private:web-input', 'public-browser')
voice_requests = _RequestsAdapter('private:voice-input')

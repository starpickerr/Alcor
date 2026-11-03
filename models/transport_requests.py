"""Compatibility import for Core's single controlled HTTP transport."""
from core.security.requests_transport import (
    request as request, get as get, post as post, Response as Response,
    exceptions as exceptions, RequestException as RequestException, HTTPError as HTTPError,
    Timeout as Timeout, ConnectionError as ConnectionError,
)

"""Explicit synthetic-owner approval for local protocol/transport tests only."""

from contextlib import contextmanager
import threading
from urllib.parse import urlsplit


@contextmanager
def approve_synthetic_egress(url):
    from core.security.connections import service_id
    from agent.permission.gateway import SecurityGateway

    if urlsplit(url).hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("Synthetic approval is limited to the local test server")
    origin = service_id(url)[1]
    stop = threading.Event()

    def confirm():
        while not stop.is_set():
            for row in SecurityGateway.pending():
                if row["tool"] == "model_egress" and row["resource"] == origin:
                    SecurityGateway.approve(row["id"])
            stop.wait(0.005)

    worker = threading.Thread(target=confirm, daemon=True)
    worker.start()
    try:
        from core.plugins.caller import caller_origin_scope
        with caller_origin_scope('desktop'):
            yield
    finally:
        stop.set()
        worker.join(2)

"""One polling and change-watch lifecycle shared by EventKit sync adapters."""

from __future__ import annotations

import threading

import logging
logger = logging.getLogger('stella.apple')


def report_sync_error(kind, phase, error):
    """Keep a caught EventKit failure visible in both the UI and desktop log."""
    logger.exception("[AppleSync] %s failed during %s", kind, phase)
    return f"{phase}失败：{error or type(error).__name__}"


class EventKitAutoRunner:
    def __init__(self, bridge, connected, sync, *, kind="calendar", interval=300, watch_retry_interval=5):
        self.bridge = bridge
        self.connected = connected
        self.sync = sync
        self.kind = kind
        self.interval = interval
        self.watch_retry_interval = watch_retry_interval
        self.stop_event = threading.Event()
        self.watch_start_lock = threading.Lock()
        self.started = False

    def start(self):
        if self.started:
            return
        self.started = True

        def poll():
            while not self.stop_event.is_set():
                if self.connected():
                    try:
                        self.sync()
                    except Exception:
                        logger.exception("EventKit %s poll failed", self.kind)
                self.stop_event.wait(self.interval)

        def watch():
            while not self.stop_event.is_set():
                if self.connected():
                    try:
                        if self.kind == "reminder":
                            result = self.bridge.watch(self.sync, self.stop_event, kind="reminder", start_lock=self.watch_start_lock)
                        else:
                            result = self.bridge.watch(self.sync, self.stop_event, start_lock=self.watch_start_lock)
                    except Exception:
                        result = None
                        logger.exception("EventKit %s watch failed", self.kind)
                    if result == 'unsupported':
                        self.stop_event.wait(self.interval)
                        continue
                    if not self.stop_event.is_set() and self.connected():
                        try:
                            self.sync()  # Reconcile any notifications missed during the outage.
                        except Exception:
                            logger.exception("EventKit %s watch recovery failed", self.kind)
                    self.stop_event.wait(self.watch_retry_interval)
                else:
                    self.stop_event.wait(self.interval)

        threading.Thread(target=poll, name=f"apple-{self.kind}-poll", daemon=True).start()
        threading.Thread(target=watch, name=f"apple-{self.kind}-watch", daemon=True).start()

    def stop(self):
        with self.watch_start_lock:
            self.stop_event.set()
        self.started = False

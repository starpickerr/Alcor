"""Small, fail-closed JSON process boundary for the macOS EventKit helper."""

from __future__ import annotations

import json
import os
import signal
import tempfile
import time
import select
import subprocess
import sys
import threading
from pathlib import Path


class BridgeError(RuntimeError):
    pass


def _default_helper():
    import os
    return Path(os.environ.get('STELLA_EVENTKIT_HELPER') or Path(__file__).with_name('Stella Apple Sync.app'))

class AppleCalendarBridge:
    def __init__(self, helper_path=None, platform=None, timeout=30):
        self.helper_path = Path(helper_path) if helper_path is not None else _default_helper()
        self.platform = platform or sys.platform
        self.timeout = timeout
        self._lock = threading.Lock()
        self._processes = set()
        self._closed = False

    def _start(self, command=None, app_files=None, **kwargs):
        with self._lock:
            if self._closed:
                raise BridgeError('Apple sync is stopped')
            process = subprocess.Popen(command or [str(self.helper_path)], **kwargs)
            process.stella_app_files = app_files
            self._processes.add(process)
            return process

    def _start_app(self, request):
        temporary = tempfile.TemporaryDirectory(prefix='stella-eventkit-')
        root = Path(temporary.name)
        incoming, outgoing, errors, pid = [root / name for name in ('request.json', 'response.json', 'stderr', 'pid')]
        incoming.write_text(json.dumps(request, ensure_ascii=False))
        outgoing.touch(mode=0o600)
        errors.touch(mode=0o600)
        command = ['/usr/bin/open', '-W', '-n', '-g', '--stdin', str(incoming),
                   '--stdout', str(outgoing), '--stderr', str(errors), str(self.helper_path),
                   '--args', '--pid-file', str(pid)]
        try:
            return self._start(command, app_files=(temporary, outgoing, pid),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except Exception:
            temporary.cleanup()
            raise

    def _finish(self, process):
        app_files = getattr(process, 'stella_app_files', None)
        if process.poll() is None:
            if app_files:
                # Launch Services owns the app process; terminating `open` alone leaves it running.
                deadline = time.monotonic() + 2
                while not app_files[2].exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                try:
                    pid = int(app_files[2].read_text())
                    if pid > 1:
                        os.kill(pid, signal.SIGTERM)
                except (OSError, ValueError):
                    pass
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        with self._lock:
            self._processes.discard(process)
        if app_files:
            app_files[0].cleanup()

    def close(self):
        with self._lock:
            self._closed = True
            processes = list(self._processes)
        for process in processes:
            self._finish(process)

    def call(self, op, **payload):
        if self.platform != "darwin":
            if op in ("status", "reminder_status"):
                return {"supported": False, "authorization": "unsupported"}
            raise BridgeError("EventKit is not supported on this platform")
        bundled = self.helper_path.suffix == '.app'
        executable = self.helper_path / 'Contents/MacOS/StellaCalendarBridge' if bundled else self.helper_path
        if not executable.is_file():
            if op in ("status", "reminder_status"):
                return {"supported": False, "authorization": "helper_missing"}
            raise BridgeError("EventKit helper is not installed")
        if op not in {"status", "request_access", "calendars", "events", "lookup", "upsert", "delete", "rename", "delete_calendar", "create_calendar",
                      "reminder_status", "reminder_request_access", "reminder_lists", "reminders",
                      "reminder_lookup", "reminder_upsert", "reminder_delete", "reminder_rename", "reminder_list_create", "reminder_list_delete"}:
            raise BridgeError("unknown EventKit operation")
        request = json.dumps({"op": op, **payload}, ensure_ascii=False) + "\n"
        try:
            process = (self._start_app({"op": op, **payload}) if bundled else
                       self._start(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            try:
                timeout = max(self.timeout, 150) if op in ("request_access", "reminder_request_access") else self.timeout
                if bundled:
                    process.communicate(timeout=timeout)
                    stdout = process.stella_app_files[1].read_text()
                else:
                    stdout, _ = process.communicate(request, timeout=timeout)
            finally:
                self._finish(process)
        except subprocess.TimeoutExpired as exc:
            raise BridgeError("EventKit helper timed out") from exc
        except OSError as exc:
            raise BridgeError("EventKit helper could not start") from exc
        try:
            response = json.loads(stdout.strip())
        except (json.JSONDecodeError, ValueError) as exc:
            raise BridgeError("EventKit helper returned an invalid response") from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            if isinstance(response, dict) and response.get("ok") is False:
                raise BridgeError(str(response.get("error") or "EventKit operation failed"))
            raise BridgeError("EventKit helper returned an invalid response")
        return {key: value for key, value in response.items() if key != "ok"}

    def watch(self, on_change, stop_event=None, kind="calendar"):
        """Block until stopped, forwarding EKEventStoreChanged notifications."""
        if self.platform != "darwin":
            return
        if self.helper_path.suffix == '.app':
            process = self._start_app({"op": "reminder_watch" if kind == "reminder" else "watch"})
            try:
                with process.stella_app_files[1].open() as output:
                    while process.poll() is None and (stop_event is None or not stop_event.is_set()):
                        line = output.readline()
                        if not line:
                            if stop_event is not None:
                                stop_event.wait(0.2)
                            else:
                                time.sleep(0.2)
                            continue
                        try:
                            if json.loads(line).get("changed") is True:
                                on_change()
                        except (ValueError, AttributeError):
                            continue
            finally:
                self._finish(process)
            return
        if not self.helper_path.is_file():
            return
        process = self._start(stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                   bufsize=1)
        try:
            op = "reminder_watch" if kind == "reminder" else "watch"
            process.stdin.write(json.dumps({"op": op}) + "\n")
            process.stdin.flush()
            while process.poll() is None and (stop_event is None or not stop_event.is_set()):
                ready, _, _ = select.select([process.stdout], [], [], 1)
                if ready:
                    line = process.stdout.readline()
                    if not line:
                        break
                    try:
                        if json.loads(line).get("changed") is True:
                            on_change()
                    except (ValueError, AttributeError):
                        continue
        finally:
            self._finish(process)

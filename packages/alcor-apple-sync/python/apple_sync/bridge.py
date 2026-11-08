"""Small, fail-closed JSON process boundary for the macOS EventKit helper."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import select
import subprocess
import sys
import threading
from pathlib import Path


class BridgeError(RuntimeError):
    def __init__(self, message, *, native_quiesced=False, native_no_side_effects=False):
        super().__init__(message)
        self.native_quiesced = native_quiesced is True
        self.native_no_side_effects = native_no_side_effects is True


def _default_helper():
    current = os.environ.get('ALCOR_EVENTKIT_HELPER')
    legacy = os.environ.get('STELLA_EVENTKIT_HELPER')
    if current and legacy and current != legacy:
        raise BridgeError('Conflicting EventKit helper settings')
    return Path(current or legacy or Path(__file__).with_name('Alcor Apple Sync.app'))

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
            process.alcor_app_files = app_files
            process.alcor_finish_lock = threading.Lock()
            process.alcor_finished = False
            process.alcor_quiesced = False
            process.alcor_watch = bool(app_files and len(app_files) > 3 and app_files[3] is not None)
            process.alcor_control_lock = threading.Lock()
            process.alcor_stopping = False
            self._processes.add(process)
            return process

    def _start_app(self, request):
        temporary = tempfile.TemporaryDirectory(prefix='alcor-eventkit-')
        root = Path(temporary.name)
        incoming, outgoing, errors, pid = [root / name for name in ('request.json', 'response.json', 'stderr', 'pid')]
        control = root / 'watch-control' if request.get('op') in ('watch', 'reminder_watch') else None
        if control is not None:
            control.write_text('0')
            request = {**request, 'watch_control': str(control)}
        incoming.write_text(json.dumps(request, ensure_ascii=False))
        outgoing.touch(mode=0o600)
        errors.touch(mode=0o600)
        command = ['/usr/bin/open', '-W', '-n', '-g', '--stdin', str(incoming),
                   '--stdout', str(outgoing), '--stderr', str(errors), str(self.helper_path),
                   '--args', '--pid-file', str(pid)]
        try:
            process = self._start(command, app_files=(temporary, outgoing, pid, control),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            return process
        except Exception:
            temporary.cleanup()
            raise

    def _finish(self, process):
        with process.alcor_finish_lock:
            if process.alcor_finished:
                return process.alcor_quiesced
            result = self._finish_once(process)
            process.alcor_quiesced = result
            process.alcor_finished = True
            return result

    def _finish_once(self, process):
        app_files = getattr(process, 'alcor_app_files', None)
        quiesced = False
        helper_pid = None
        if app_files:
            try:
                helper_pid = int(app_files[2].read_text())
                if helper_pid <= 1:
                    helper_pid = None
            except (OSError, ValueError):
                pass
        if process.poll() is None:
            if app_files:
                # Launch Services owns the app process; terminating `open` alone leaves it running.
                deadline = time.monotonic() + 2
                while not app_files[2].exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                if getattr(process, 'alcor_watch', False):
                    with process.alcor_control_lock:
                        process.alcor_stopping = True
                        try:
                            app_files[3].write_text('stop')
                        except OSError:
                            pass
                try:
                    helper_pid = int(app_files[2].read_text())
                    if helper_pid <= 1:
                        helper_pid = None
                except (OSError, ValueError):
                    pass
                if helper_pid:
                    try:
                        # Only a natural open -W return proves the launched app exited.
                        quiesced = process.wait(timeout=2) == 0 and process.poll() == 0
                    except subprocess.TimeoutExpired:
                        pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if not app_files:
                    quiesced = process.poll() is not None
        elif app_files:
            quiesced = bool(helper_pid and process.poll() == 0)
        else:
            quiesced = True  # The directly spawned helper was reaped.
        with self._lock:
            self._processes.discard(process)
        if app_files:
            app_files[0].cleanup()
        return quiesced

    def close(self):
        with self._lock:
            self._closed = True
            processes = list(self._processes)
        for process in processes:
            self._finish(process)

    def call(self, op, **payload):
        if op in ("delete", "delete_calendar", "reminder_delete", "reminder_list_delete") and (type(payload.get("delete_write_version")) is not int or payload["delete_write_version"] != 1):
            raise BridgeError('缺少可验证的本次人工确认通道，不可恢复删除暂不可用。', native_no_side_effects=True)
        if self.platform != "darwin":
            if op in ("status", "reminder_status"):
                return {"supported": False, "authorization": "unsupported"}
            raise BridgeError("EventKit is not supported on this platform")
        bundled = self.helper_path.suffix == '.app'
        executable = self.helper_path / 'Contents/MacOS/AlcorCalendarBridge' if bundled else self.helper_path
        if bundled and not executable.is_file():
            # An explicitly selected older helper remains usable during rollback.
            executable = self.helper_path / 'Contents/MacOS/StellaCalendarBridge'
        if not executable.is_file():
            if op in ("status", "reminder_status"):
                return {"supported": False, "authorization": "helper_missing"}
            raise BridgeError("EventKit helper is not installed")
        if op not in {"status", "request_access", "calendars", "events", "lookup", "lookup_identity", "upsert", "delete", "rename", "delete_calendar", "create_calendar",
                      "reminder_status", "reminder_request_access", "reminder_lists", "reminders",
                      "reminder_lookup", "reminder_upsert", "reminder_complete", "reminder_delete", "reminder_rename", "reminder_list_create", "reminder_list_delete"}:
            raise BridgeError("unknown EventKit operation")
        request = json.dumps({"op": op, **payload}, ensure_ascii=False) + "\n"
        quiesced = False
        try:
            process = (self._start_app({"op": op, **payload}) if bundled else
                       self._start(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
            try:
                timeout = max(self.timeout, 150) if op in ("request_access", "reminder_request_access") else self.timeout
                if bundled:
                    process.communicate(timeout=timeout)
                    stdout = process.alcor_app_files[1].read_text()
                else:
                    stdout, _ = process.communicate(request, timeout=timeout)
            finally:
                quiesced = self._finish(process)
        except subprocess.TimeoutExpired as exc:
            raise BridgeError("EventKit helper timed out", native_quiesced=quiesced) from exc
        except OSError as exc:
            raise BridgeError("EventKit helper could not start", native_quiesced=quiesced) from exc
        try:
            response = json.loads(stdout.strip())
        except (json.JSONDecodeError, ValueError) as exc:
            raise BridgeError("EventKit helper returned an invalid response", native_quiesced=quiesced) from exc
        if not isinstance(response, dict) or response.get("ok") is not True:
            if isinstance(response, dict) and response.get("ok") is False:
                binding = payload.get("receipt_binding")
                category_proof = (
                    op in ("rename", "reminder_rename")
                    and type(payload.get("category_write_version")) is int and payload["category_write_version"] == 1
                    and isinstance(binding, str) and re.fullmatch(r"[0-9a-f]{64}", binding) is not None
                    and type(response.get("category_write_version")) is int and response["category_write_version"] == 1
                    and response.get("receipt_binding") == binding and response.get("op") == op
                    and response.get("no_side_effects") is True
                )
                create_proof = (
                    op in ("create_calendar", "reminder_list_create")
                    and type(payload.get("category_create_version")) is int and payload["category_create_version"] == 1
                    and isinstance(payload.get("source_id"), str) and bool(payload["source_id"])
                    and isinstance(payload.get("receipt_file"), str) and bool(payload["receipt_file"])
                    and Path(payload["receipt_file"]).parent.name == "native-receipts"
                    and isinstance(binding, str) and re.fullmatch(r"[0-9a-f]{64}", binding) is not None
                    and type(response.get("category_create_version")) is int and response["category_create_version"] == 1
                    and response.get("receipt_binding") == binding and response.get("op") == op
                    and response.get("no_side_effects") is True
                )
                delete_proof = (
                    quiesced and op in ("delete", "delete_calendar", "reminder_delete", "reminder_list_delete")
                    and type(payload.get("delete_write_version")) is int and payload["delete_write_version"] == 1
                    and isinstance(binding, str) and re.fullmatch(r"[0-9a-f]{64}", binding) is not None
                    and type(response.get("delete_write_version")) is int and response["delete_write_version"] == 1
                    and response.get("receipt_binding") == binding and response.get("op") == op
                    and response.get("no_side_effects") is True
                )
                raise BridgeError(str(response.get("error") or "EventKit operation failed"), native_quiesced=quiesced,
                                  native_no_side_effects=delete_proof or category_proof or create_proof or (op == "reminder_complete" and response.get("no_side_effects") is True))
            raise BridgeError("EventKit helper returned an invalid response", native_quiesced=quiesced)
        return {key: value for key, value in response.items() if key != "ok"}

    def watch(self, on_change, stop_event=None, kind="calendar", start_lock=None):
        """Block until stopped, forwarding EKEventStoreChanged notifications."""
        if self.platform != "darwin" or (stop_event is not None and stop_event.is_set()):
            return
        if self.helper_path.suffix == '.app':
            capabilities = self.call('reminder_status' if kind == 'reminder' else 'status')
            if stop_event is not None and stop_event.is_set():
                return
            if capabilities.get('watch_control_version') != 1:
                return 'unsupported'
            def start_app():
                if stop_event is not None and stop_event.is_set():
                    return None
                return self._start_app({"op": "reminder_watch" if kind == "reminder" else "watch"})
            if start_lock is not None:
                with start_lock:
                    process = start_app()
            else:
                process = start_app()
            if process is None:
                return
            heartbeat_stop = None
            def heartbeat_loop():
                heartbeat = 0
                while not heartbeat_stop.is_set():
                    with process.alcor_control_lock:
                        if process.alcor_stopping:
                            return
                        try:
                            heartbeat += 1
                            process.alcor_app_files[3].write_text(str(heartbeat))
                        except OSError:
                            return
                    heartbeat_stop.wait(1)
            heartbeat_thread = None
            heartbeat_started = False
            try:
                heartbeat_stop = threading.Event()
                heartbeat_thread = threading.Thread(target=heartbeat_loop, name='apple-watch-heartbeat', daemon=True)
                heartbeat_thread.start()
                heartbeat_started = True
                with process.alcor_app_files[1].open() as output:
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
                if heartbeat_stop is not None:
                    heartbeat_stop.set()
                self._finish(process)
                if heartbeat_started:
                    heartbeat_thread.join(timeout=2)
            return
        if not self.helper_path.is_file():
            return
        def start_direct():
            if stop_event is not None and stop_event.is_set():
                return None
            return self._start(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        if start_lock is not None:
            with start_lock:
                process = start_direct()
        else:
            process = start_direct()
        if process is None:
            return
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

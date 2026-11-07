import json
import sys
import threading
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/stella-apple-sync/python'))
from apple_sync.bridge import AppleCalendarBridge, BridgeError

class AppBridge(unittest.TestCase):
    def test_app_bundle_launches_through_launch_services_and_returns_native_response(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Stella Apple Sync.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/StellaCalendarBridge').write_text('fixture')
            seen = []
            class Launcher:
                returncode = 0
                def __init__(self, command, **kwargs):
                    seen.append(command)
                    request = json.loads(Path(command[command.index('--stdin') + 1]).read_text())
                    assert request['op'] == 'request_access'
                    Path(command[command.index('--stdout') + 1]).write_text(json.dumps({'ok': True, 'authorization': 'denied', 'granted': False}))
                def communicate(self, timeout=None):
                    return ('', '')
                def poll(self):
                    return 0
            with patch('apple_sync.bridge.subprocess.Popen', Launcher):
                bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                self.assertEqual(bridge.call('request_access')['authorization'], 'denied')
                bridge.close()
            self.assertEqual(seen[0][0], '/usr/bin/open')
            self.assertIn('-W', seen[0])
            self.assertIn('-n', seen[0])
            self.assertIn(str(app), seen[0])
            self.assertFalse(Path(seen[0][seen[0].index('--stdin') + 1]).exists())

    def test_calendar_deletion_passes_exact_identity_and_surfaces_native_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Delete.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/StellaCalendarBridge').touch()
            response = {'ok': True, 'deleted': True}
            class Launcher:
                def __init__(self, command, **kwargs):
                    request = json.loads(Path(command[command.index('--stdin') + 1]).read_text())
                    assert request == {'op': 'delete_calendar', 'calendar_id': 'synthetic-id'}
                    Path(command[command.index('--stdout') + 1]).write_text(json.dumps(response))
                def communicate(self, timeout=None):
                    return ('', '')
                def poll(self):
                    return 0
            with patch('apple_sync.bridge.subprocess.Popen', Launcher):
                bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                self.assertEqual(bridge.call('delete_calendar', calendar_id='synthetic-id'), {'deleted': True})
                response = {'ok': False, 'error': 'Calendar is read only'}
                with self.assertRaisesRegex(BridgeError, 'read only'):
                    bridge.call('delete_calendar', calendar_id='synthetic-id')
                bridge.close()

    def test_app_watcher_stops_the_actual_launched_app_and_cleans_its_files(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Watch.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/StellaCalendarBridge').touch()
            commands = []
            class Launcher:
                def __init__(self, command, **kwargs):
                    commands.append(command)
                    self.returncode = None
                    Path(command[command.index('--stdout') + 1]).write_text('{"changed":true}\n')
                    Path(command[command.index('--pid-file') + 1]).write_text('424242')
                def poll(self):
                    return self.returncode
                def terminate(self):
                    self.returncode = 0
                def wait(self, timeout=None):
                    return self.returncode
            stopped = threading.Event()
            with patch('apple_sync.bridge.subprocess.Popen', Launcher), patch('apple_sync.bridge.os.kill') as kill:
                bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                bridge.watch(stopped.set, stopped)
                kill.assert_called_once_with(424242, signal.SIGTERM)
                self.assertFalse(bridge._processes)
                self.assertFalse(Path(commands[0][commands[0].index('--stdin') + 1]).exists())
                bridge.close()

    def test_native_permission_error_is_visible_instead_of_becoming_not_determined(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Failure.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/StellaCalendarBridge').touch()
            class Launcher:
                def __init__(self, command, **kwargs):
                    Path(command[command.index('--stdout') + 1]).write_text('{"ok":false,"error":"System permission request failed"}')
                def communicate(self, timeout=None):
                    return ('', '')
                def poll(self):
                    return 0
            with patch('apple_sync.bridge.subprocess.Popen', Launcher):
                bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                with self.assertRaisesRegex(BridgeError, 'System permission request failed'):
                    bridge.call('reminder_request_access')
                self.assertFalse(bridge._processes)
                bridge.close()

    def test_unreadable_bundle_is_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Missing.app'
            app.mkdir()
            bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
            self.assertEqual(bridge.call('status')['authorization'], 'helper_missing')

if __name__ == '__main__':
    unittest.main()

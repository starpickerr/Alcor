import json
import os
import sys
import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'packages/alcor-apple-sync/python'))
from apple_sync.bridge import AppleCalendarBridge, BridgeError, _default_helper

class AppBridge(unittest.TestCase):
    def test_new_helper_identity_and_legacy_override(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_default_helper().name, 'Alcor Apple Sync.app')
        with patch.dict(os.environ, {'STELLA_EVENTKIT_HELPER': '/legacy/helper'}, clear=True):
            self.assertEqual(_default_helper(), Path('/legacy/helper'))
        with patch.dict(os.environ, {'ALCOR_EVENTKIT_HELPER': '/new/helper', 'STELLA_EVENTKIT_HELPER': '/other/helper'}, clear=True):
            with self.assertRaisesRegex(BridgeError, 'Conflicting'):
                _default_helper()

    def test_app_bundle_launches_through_launch_services_and_returns_native_response(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Alcor Apple Sync.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/AlcorCalendarBridge').write_text('fixture')
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
                    assert request == {'op': 'delete_calendar', 'calendar_id': 'synthetic-id', 'delete_write_version': 1}
                    Path(command[command.index('--stdout') + 1]).write_text(json.dumps(response))
                def communicate(self, timeout=None):
                    return ('', '')
                def poll(self):
                    return 0
            with patch('apple_sync.bridge.subprocess.Popen', Launcher):
                bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                self.assertEqual(bridge.call('delete_calendar', calendar_id='synthetic-id', delete_write_version=1), {'deleted': True})
                response = {'ok': False, 'error': 'Calendar is read only'}
                with self.assertRaisesRegex(BridgeError, 'read only'):
                    bridge.call('delete_calendar', calendar_id='synthetic-id', delete_write_version=1)
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
                    request = json.loads(Path(command[command.index('--stdin') + 1]).read_text())
                    self.control = Path(request['watch_control'])
                    Path(command[command.index('--stdout') + 1]).write_text('{"changed":true}\n')
                    Path(command[command.index('--pid-file') + 1]).write_text('424242')
                def poll(self):
                    return self.returncode
                def terminate(self):
                    self.returncode = 0
                def wait(self, timeout=None):
                    if timeout == 2:
                        assert self.control.read_text() == 'stop'
                        self.returncode = 0
                    return self.returncode
            stopped = threading.Event()
            with patch('apple_sync.bridge.subprocess.Popen', Launcher), patch('apple_sync.bridge.os.kill') as kill:
                bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                with patch.object(bridge, 'call', return_value={'watch_control_version':1}):
                    bridge.watch(stopped.set, stopped)
                kill.assert_not_called()
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

    def test_timeout_quiescence_requires_observed_open_wait_exit_and_exact_helper_pid(self):
        import itertools
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Timeout.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/AlcorCalendarBridge').touch()
            for mode in ('confirmed', 'unconfirmed', 'missing_pid', 'inconsistent_wait'):
                with self.subTest(mode=mode):
                    class Launcher:
                        def __init__(self, command, **kwargs):
                            self.returncode = None
                            if mode != 'missing_pid':
                                Path(command[command.index('--pid-file') + 1]).write_text('424242')
                        def communicate(self, timeout=None): raise subprocess.TimeoutExpired('synthetic', timeout)
                        def poll(self): return self.returncode
                        def wait(self, timeout=None):
                            if timeout == 2 and mode == 'confirmed':
                                self.returncode = 0
                                return 0
                            if timeout == 2 and mode == 'inconsistent_wait':
                                return 0
                            if timeout == 2:
                                raise subprocess.TimeoutExpired('synthetic', timeout)
                            return self.returncode
                        def terminate(self): self.returncode = -15
                        def kill(self): self.returncode = -9
                    with patch('apple_sync.bridge.subprocess.Popen', Launcher), patch('apple_sync.bridge.os.kill') as kill, patch('apple_sync.bridge.time.monotonic', side_effect=itertools.count(step=3)), patch('apple_sync.bridge.time.sleep'):
                        bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                        with self.assertRaises(BridgeError) as error:
                            bridge.call('delete_calendar', calendar_id='synthetic', delete_write_version=1)
                        self.assertEqual(error.exception.native_quiesced, mode == 'confirmed')
                        kill.assert_not_called()
                        self.assertFalse(bridge._processes)

    def test_watcher_timeout_never_signals_a_reused_pid_and_removes_control_file(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'WatchTimeout.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/AlcorCalendarBridge').touch()
            controls = []
            class Launcher:
                def __init__(self, command, **kwargs):
                    request = json.loads(Path(command[command.index('--stdin') + 1]).read_text())
                    controls.append(Path(request['watch_control']))
                    Path(command[command.index('--pid-file') + 1]).write_text('424242')
                    self.returncode = None
                def poll(self): return self.returncode
                def wait(self, timeout=None):
                    if timeout == 2:
                        assert controls[0].read_text() == 'stop'
                        raise subprocess.TimeoutExpired('synthetic', timeout)
                    return self.returncode
                def terminate(self): self.returncode = -15
                def kill(self): self.returncode = -9
            with patch('apple_sync.bridge.subprocess.Popen', Launcher), patch('apple_sync.bridge.os.kill') as kill:
                bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
                process = bridge._start_app({'op':'watch'})
                self.assertFalse(bridge._finish(process))
                self.assertFalse(bridge._finish(process))
                kill.assert_not_called()
                self.assertFalse(controls[0].exists())

    def test_watcher_heartbeat_continues_during_slow_sync_callback(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'SlowWatch.app'
            (app / 'Contents/MacOS').mkdir(parents=True)
            (app / 'Contents/MacOS/AlcorCalendarBridge').touch()
            controls=[]
            callback_started=threading.Event()
            release=threading.Event()
            stopped=threading.Event()
            class Launcher:
                def __init__(self, command, **kwargs):
                    request=json.loads(Path(command[command.index('--stdin')+1]).read_text())
                    controls.append(Path(request['watch_control']))
                    self.returncode=None
                    Path(command[command.index('--stdout')+1]).write_text('{"changed":true}\n')
                    Path(command[command.index('--pid-file')+1]).write_text('424242')
                def poll(self): return self.returncode
                def wait(self,timeout=None):
                    if timeout==2 and controls[0].read_text()=='stop':
                        self.returncode=0
                    return self.returncode
                def terminate(self):self.returncode=-15
                def kill(self):self.returncode=-9
            def slow_callback():
                callback_started.set()
                release.wait(4)
                stopped.set()
            with patch('apple_sync.bridge.subprocess.Popen',Launcher),patch('apple_sync.bridge.os.kill') as kill:
                bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
                with patch.object(bridge, 'call', return_value={'watch_control_version':1}):
                    thread=threading.Thread(target=bridge.watch,args=(slow_callback,stopped))
                    thread.start()
                    self.assertTrue(callback_started.wait(2))
                    before=controls[0].read_text()
                    time.sleep(1.3)
                    after=controls[0].read_text()
                    self.assertNotEqual(before,after)
                    release.set()
                    thread.join(3)
                    self.assertFalse(thread.is_alive())
                    kill.assert_not_called()
                    bridge.close()

    def test_legacy_helper_without_cooperative_watch_is_not_launched(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/'Legacy.app'
            (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/MacOS/StellaCalendarBridge').touch()
            bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
            with patch.object(bridge,'call',return_value={'supported':True,'authorization':'full_access'}), patch('apple_sync.bridge.subprocess.Popen') as launch:
                self.assertEqual(bridge.watch(lambda:None,threading.Event()),'unsupported')
                launch.assert_not_called()

    def test_stop_during_capability_query_never_launches_watcher(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/'Stopped.app'
            (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/MacOS/AlcorCalendarBridge').touch()
            stop=threading.Event()
            bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
            def capability(_):
                stop.set()
                return {'watch_control_version':1}
            with patch.object(bridge,'call',side_effect=capability), patch('apple_sync.bridge.subprocess.Popen') as launch:
                bridge.watch(lambda:None,stop)
                launch.assert_not_called()

    def test_stop_and_watch_launch_are_serialized(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/'Guarded.app'
            (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/MacOS/AlcorCalendarBridge').touch()
            stop=threading.Event()
            checked=threading.Event()
            guard=threading.Lock()
            bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
            def capability(_):
                checked.set()
                return {'watch_control_version':1}
            with patch.object(bridge,'call',side_effect=capability),patch('apple_sync.bridge.subprocess.Popen') as launch:
                guard.acquire()
                try:
                    thread=threading.Thread(target=bridge.watch,args=(lambda:None,stop),kwargs={'start_lock':guard})
                    thread.start()
                    self.assertTrue(checked.wait(2))
                    stop.set()
                finally:
                    guard.release()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                launch.assert_not_called()

    def test_close_cannot_observe_partially_initialized_watch_process(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/'Concurrent.app'
            (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/MacOS/AlcorCalendarBridge').touch()
            started=threading.Event()
            release=threading.Event()
            closed=threading.Event()
            errors=[]
            class Launcher:
                def __init__(self,command,**kwargs):
                    request=json.loads(Path(command[command.index('--stdin')+1]).read_text())
                    self.control=Path(request['watch_control'])
                    self.returncode=None
                    Path(command[command.index('--pid-file')+1]).write_text('424242')
                def __setattr__(self,name,value):
                    if name=='alcor_control_lock':
                        started.set()
                        release.wait(2)
                    object.__setattr__(self,name,value)
                def poll(self):return self.returncode
                def wait(self,timeout=None):
                    if timeout==2 and self.control.read_text()=='stop':
                        self.returncode=0
                    return self.returncode
                def terminate(self):self.returncode=-15
                def kill(self):self.returncode=-9
            with patch('apple_sync.bridge.subprocess.Popen',Launcher),patch('apple_sync.bridge.os.kill') as kill:
                bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
                def start():
                    try:
                        bridge._start_app({'op':'watch'})
                    except Exception as error:
                        errors.append(error)
                def close():
                    try:
                        bridge.close()
                    except Exception as error:
                        errors.append(error)
                    finally:
                        closed.set()
                starter=threading.Thread(target=start)
                starter.start()
                self.assertTrue(started.wait(2))
                closer=threading.Thread(target=close)
                closer.start()
                self.assertFalse(closed.wait(0.05))
                release.set()
                starter.join(2)
                closer.join(2)
                self.assertTrue(closed.is_set())
                self.assertFalse(errors)
                self.assertFalse(bridge._processes)
                kill.assert_not_called()

    def test_heartbeat_thread_start_failure_cleans_launched_watcher(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/'ThreadFailure.app'
            (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/MacOS/AlcorCalendarBridge').touch()
            controls=[]
            class Launcher:
                def __init__(self,command,**kwargs):
                    request=json.loads(Path(command[command.index('--stdin')+1]).read_text())
                    controls.append(Path(request['watch_control']))
                    self.returncode=None
                    Path(command[command.index('--pid-file')+1]).write_text('424242')
                def poll(self):return self.returncode
                def wait(self,timeout=None):
                    if timeout==2 and controls[0].read_text()=='stop':
                        self.returncode=0
                    return self.returncode
                def terminate(self):self.returncode=-15
                def kill(self):self.returncode=-9
            with patch('apple_sync.bridge.subprocess.Popen',Launcher),patch('apple_sync.bridge.os.kill') as kill:
                bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
                with patch.object(bridge,'call',return_value={'watch_control_version':1}),patch('apple_sync.bridge.threading.Thread.start',side_effect=RuntimeError('synthetic thread failure')):
                    with self.assertRaisesRegex(RuntimeError,'synthetic thread failure'):
                        bridge.watch(lambda:None,threading.Event())
                self.assertFalse(bridge._processes)
                self.assertFalse(controls[0].exists())
                kill.assert_not_called()
                controls.clear()
                bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
                with patch.object(bridge,'call',return_value={'watch_control_version':1}),patch('apple_sync.bridge.threading.Thread',side_effect=RuntimeError('synthetic constructor failure')):
                    with self.assertRaisesRegex(RuntimeError,'synthetic constructor failure'):
                        bridge.watch(lambda:None,threading.Event())
                self.assertFalse(bridge._processes)
                self.assertFalse(controls[0].exists())
                kill.assert_not_called()
                controls.clear()
                bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
                stop=threading.Event()
                with patch.object(bridge,'call',return_value={'watch_control_version':1}),patch('apple_sync.bridge.threading.Event',side_effect=RuntimeError('synthetic event failure')):
                    with self.assertRaisesRegex(RuntimeError,'synthetic event failure'):
                        bridge.watch(lambda:None,stop)
                self.assertFalse(bridge._processes)
                self.assertFalse(controls[0].exists())
                kill.assert_not_called()

    def test_unreadable_bundle_is_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'Missing.app'
            app.mkdir()
            bridge = AppleCalendarBridge(helper_path=app, platform='darwin')
            self.assertEqual(bridge.call('status')['authorization'], 'helper_missing')

if __name__ == '__main__':
    unittest.main()

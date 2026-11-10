"""Link replacement captures its snapshot only after an active sync completes."""
import runpy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

M = runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))


class LinkReplacement(unittest.TestCase):
    def setUp(self):
        self.case = M['Contracts']()
        self.case.setUp()

    def tearDown(self):
        self.case.tearDown()

    def test_link_replacement_waits_for_active_sync_before_capturing_snapshot(self):
        for kind in ('calendar', 'reminders'):
            with self.subTest(kind=kind):
                sync = (self.case.calendar if kind == 'calendar' else self.case.reminder)(M['NativeBridge'](kind))
                sync.clock = lambda: '2026-11-07T00:00:00Z'
                sync.start_sync()
                before = sync._stored_links()
                links = [{**row, 'enabled': bool(row['enabled']), 'direction': 'import'} for row in before]
                lock = sync._lock
                waiting = threading.Event()
                captured = threading.Event()
                resume = threading.Event()
                caller = threading.get_ident()

                class ObservedLock:
                    def __enter__(self):
                        if threading.get_ident() != caller:
                            waiting.set()
                        return lock.__enter__()

                    def __exit__(self, *args):
                        return lock.__exit__(*args)

                stored_links = sync._stored_links

                def capture_links():
                    rows = stored_links()
                    if threading.get_ident() != caller and not captured.is_set():
                        captured.set()
                        waiting.set()
                        if not resume.wait(5):
                            raise TimeoutError('replacement snapshot was not released')
                    return rows

                sync._lock = ObservedLock()
                sync._stored_links = capture_links
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with lock:
                        future = pool.submit(sync.replace_links, links)
                        self.assertTrue(waiting.wait(5), 'replacement did not reach the manager lock')
                        sync.clock = lambda: '2026-11-07T00:01:00Z'
                        self.assertEqual(sync.sync()['status'], 'success')
                        self.assertNotEqual(sync._stored_links(), before)
                        resume.set()
                    result = future.result(timeout=5)
                self.assertEqual(result['links'][0]['direction'], 'import')


if __name__ == '__main__':
    unittest.main()

"""Unchanged link selection reconnects without rebinding or touching claims."""
import copy
import runpy
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

M=runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))
COMMON=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class Reconnect(unittest.TestCase):
    def setUp(self):self.case=M['Contracts']();self.case.setUp()
    def tearDown(self):self.case.tearDown()
    def setup_domain(self,kind):
        bridge=M['NativeBridge'](kind);sync=(self.case.calendar if kind=='calendar' else self.case.reminder)(bridge)
        sync.start_sync();owner=self.case.business.calendar if kind=='calendar' else self.case.business.tasks
        return sync,bridge,owner
    def test_start_sync_reconnects_identical_selected_links(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);before=sync._stored_links()
                sync.disconnect()
                result=sync.start_sync([{'apple_id':r['apple_id'],'alcor_id':r['alcor_id']} for r in before])
                self.assertTrue(result['state']['connected'])
                self.assertEqual(result['result']['status'],'success')
                fields=lambda rows:[{k:v for k,v in row.items() if k!='updated_at'} for row in rows]
                self.assertEqual(fields(sync._stored_links()),fields(before))
                self.assertEqual(sync._get('calendar_ids' if kind=='calendar' else 'list_ids'),['a'])
    def test_identical_replace_reconnects_without_touching_held_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);before=sync._stored_links();maps=sync._maps();sync.disconnect()
                with COMMON['other_process_holds'](sync):
                    held=copy.deepcopy(sync.status()['scan_claims'])
                    with patch.object(owner,'apple_bind',side_effect=AssertionError('unchanged link rebound')):
                        result=sync.replace_links([{**r,'enabled':bool(r['enabled'])} for r in before])
                    self.assertTrue(result['connected']);self.assertEqual(result['scan_claims'],held)
                    self.assertEqual(sync._stored_links(),before);self.assertEqual(sync._maps(),maps)
    def test_real_inventory_error_still_stops_reconnection(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.setup_domain(kind);links=sync._stored_links();sync.disconnect();original=bridge.call
                def failing(op,**kw):
                    if op in ('calendars','reminder_lists'):raise RuntimeError('synthetic inventory failure')
                    return original(op,**kw)
                with patch.object(bridge,'call',side_effect=failing):
                    with self.assertRaisesRegex(RuntimeError,'inventory failure'):
                        sync.replace_links([{**r,'enabled':bool(r['enabled'])} for r in links])
                self.assertFalse(sync._get('connected'))

if __name__=='__main__':unittest.main(verbosity=2)

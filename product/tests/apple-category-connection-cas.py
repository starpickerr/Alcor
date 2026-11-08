"""Existing selection entry, real owner and manager SQLite, no EventKit."""
import contextlib
import copy
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

@contextlib.contextmanager
def case(kind):
    c=M['CommonClaims']();c.setUp()
    try:
        b=M['M']['NativeBridge'](kind)
        s=(c.case.calendar if kind=='calendar' else c.case.reminder)(b)
        o=c.case.business.calendar if kind=='calendar' else c.case.business.tasks
        yield s,o,b
    finally:c.tearDown()

def select(identity='',name='Selected'):
    return [{'apple_id':'a','alcor_id':identity,'create_name':name,'direction':'bidirectional','enabled':True}]

class Connections(unittest.TestCase):
    def test_first_binding_creates_category_once_without_native_write(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                state=s.replace_links(select());ident=state['links'][0]['alcor_id']
                self.assertEqual(o.get_category(ident)['apple']['apple_id'],'a')
                s.replace_links(select(ident));self.assertEqual(len(o.categories()),1)
                self.assertFalse(any(op in ('create_calendar','reminder_list_create','upsert','reminder_upsert') for op,_ in b.calls))
    def test_owner_edit_after_capture_rejects_without_binding_and_retries(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                local=o.create_category({'name':'Local'});original=o.apple_connect_category
                def edited(*args):
                    o.update_category(local['id'],{'name':'New choice'})
                    return original(*args)
                with patch.object(o,'apple_connect_category',side_effect=edited):
                    with self.assertRaisesRegex(ValueError,'CONNECTION_CHANGED'):s.replace_links(select(local['id']))
                self.assertEqual(o.get_category(local['id'])['name'],'New choice')
                self.assertFalse(o.get_category(local['id']).get('apple'));self.assertFalse(s._stored_links())
                self.assertFalse(s.status()['scan_claims'])
                s.replace_links(select(local['id']));self.assertEqual(len(s._stored_links()),1)
    def test_owner_commit_response_lost_keeps_evidence_and_no_repeat(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                original=o.apple_connect_category;calls=[]
                def lost(*args):calls.append(args);original(*args);raise RuntimeError('reply lost')
                with patch.object(o,'apple_connect_category',side_effect=lost):
                    with self.assertRaisesRegex(RuntimeError,'reply lost'):s.replace_links(select())
                    with self.assertRaises(ValueError):s.replace_links(select())
                self.assertEqual(len(calls),1);self.assertEqual(len(o.categories()),1)
                self.assertFalse(s._stored_links());self.assertEqual(s.status()['scan_claims'][0]['state'],'uncertain')
                self.assertEqual(o.apple_connect_category(*calls[0])['id'],o.categories()[0]['id'])
    def test_manager_evidence_change_after_owner_commit_never_inserts_link(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                original=o.apple_connect_category
                def changed(*args):
                    result=original(*args)
                    with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    return result
                with patch.object(o,'apple_connect_category',side_effect=changed):
                    with self.assertRaises(ValueError):s.replace_links(select())
                self.assertFalse(s._stored_links());self.assertEqual(len(o.categories()),1)
                self.assertTrue(s.status()['scan_claims'])
    def test_same_local_category_claim_excludes_other_native_group_process(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                local=o.create_category({'name':'Local'})
                with M['other_process_holds'](s,group='other',item='category:'+local['id']):
                    with self.assertRaises(ValueError):s.replace_links(select(local['id']))
                self.assertFalse(s._stored_links());self.assertFalse(o.get_category(local['id']).get('apple'))
    def test_receipt_cannot_bind_recreated_category_with_same_id(self):
        from apple_sync.contracts import category_binding
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                local=o.create_category({'name':'Local'});expected=o.apple_capture_category_absence(local['id'])
                op='a'*64;binding=category_binding(b.sources[0])
                o.apple_connect_category(op,local['id'],expected,{},binding)
                with o._connect() as db:
                    row=tuple(db.execute(f'SELECT * FROM {o.APPLE_CATEGORIES} WHERE id=?',(local['id'],)).fetchone())
                    db.execute(f'DELETE FROM {o.APPLE_CATEGORIES} WHERE id=?',(local['id'],))
                    db.execute(f'INSERT INTO {o.APPLE_CATEGORIES} VALUES (?,?,?,?)',row)
                with self.assertRaisesRegex(ValueError,'incarnation'):o.apple_connect_category(op,local['id'],expected,{},binding)
    def test_pending_delete_keeps_evidence_for_direction_only_not_reassignment(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                s.start_sync();link=s._stored_links()[0]
                # Queue through the private conditional contract; raw delete no longer grants approval.
                from business.apple_delete_approval import prepare,commit
                from apple_sync.contracts import native_delete_preview
                domain='calendar' if kind=='calendar' else 'tasks';args={'id' if kind=='calendar' else 'category_id':link['alcor_id']}
                operation='d'*64;plan=prepare(o,domain,'delete_category',args,operation)
                native=native_delete_preview(s,o,kind,'category',link['alcor_id'],plan['apple']['scope'])
                commit(o,domain,'delete_category',args,operation,plan['snapshot'],native)
                before=copy.deepcopy(o.get_category(link['alcor_id']));operations=copy.deepcopy(o.apple_operations())
                s.replace_links([{**link,'direction':'import','enabled':True}])
                self.assertEqual(o.get_category(link['alcor_id']),before)
                self.assertEqual(o.apple_operations(),operations)
                self.assertEqual(s._stored_links()[0]['direction'],'import')
                b.sources.append(dict(b.sources[0],id='b',title='Other'))
                with self.assertRaisesRegex(ValueError,'operation is pending'):
                    s.replace_links([{**link,'apple_id':'b','direction':'import','enabled':True}])
                self.assertEqual(s._stored_links()[0]['apple_id'],'a')
                self.assertEqual(o.get_category(link['alcor_id']),before)

    def test_inventory_change_does_not_create_owner_category(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),case(kind) as (s,o,b):
                original=b.call;count=0
                def changed(op,**kw):
                    nonlocal count
                    if op in ('calendars','reminder_lists'):
                        count+=1
                        if count==2:b.sources[0]['title']='Changed'
                    return original(op,**kw)
                with patch.object(b,'call',side_effect=changed):
                    with self.assertRaisesRegex(ValueError,'inventory changed'):s.replace_links(select())
                self.assertFalse(o.categories());self.assertFalse(s._stored_links());self.assertFalse(s.status()['scan_claims'])

if __name__=='__main__':unittest.main()

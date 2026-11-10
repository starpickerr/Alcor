"""A reconnect must not turn a persistent category into a different account."""
import copy
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

M = runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))

READS = {'status','reminder_status','calendars','reminder_lists','events','reminders','lookup','reminder_lookup','lookup_identity'}

class CategorySourceReconnect(unittest.TestCase):
    def fixture(self, kind, s, o, b, *, empty=True, writable=True):
        fresh=copy.deepcopy(b.rows[0]);b.sources[0]['writable']=writable
        fresh['writable']=writable
        b.sources.append({**b.sources[0],'id':'healthy','title':'Healthy','writable':True})
        healthy={**fresh,'calendar_id' if kind=='calendar' else 'list_id':'healthy',
                 'event_id' if kind=='calendar' else 'reminder_id':'healthy-item','external_id':'healthy-external','writable':True}
        b.rows=([fresh] if not empty else [])+[healthy]
        s.start_sync()
        link=next(row for row in s._stored_links() if row['apple_id']=='a')
        return fresh,healthy,link

    def local(self, kind, o, category):
        payload={'title':'Private local item','category_id':category}
        return o.create_event({**payload,'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'}) if kind=='calendar' else o.create_todo(payload)

    def test_empty_category_changed_source_neither_imports_nor_exports(self):
        for kind in ('calendar','reminders'):
            for automatic in (False,True):
                with self.subTest(kind=kind,automatic=automatic),M['case'](kind) as (s,o,b):
                    fresh,healthy,link=self.fixture(kind,s,o,b)
                    category=copy.deepcopy(o.get_category(link['alcor_id']))
                    s.disconnect();b.sources[0]['source_id']='account2'
                    fresh.update(source_id='account2',title='Foreign source item');b.rows=[fresh,healthy]
                    healthy['title']='Healthy incoming edit'
                    selected=[{key:row[key] for key in ('apple_id','alcor_id')} for row in s._stored_links()]
                    b.calls.clear()
                    result=s.start_sync(None if automatic else selected)['result']
                    local=self.local(kind,o,link['alcor_id']);before=copy.deepcopy(s._local(local['id']))
                    result2=s.sync();result3=s.sync()
                    observed={'initial':result,'next':result2,'third':result3,'calls':[op for op,_ in b.calls]}
                    print(kind,automatic,json.dumps(observed,sort_keys=True))
                    self.assertEqual(result['imported'],0)
                    self.assertEqual(result2['exported'],0)
                    self.assertEqual(result3['exported'],0)
                    self.assertEqual(o.get_category(link['alcor_id']),category)
                    self.assertEqual(s._local(local['id']),before)
                    self.assertFalse(any(row['title']=='Foreign source item' for row in (o.list_events() if kind=='calendar' else o.list_todos())))
                    self.assertTrue(any(row['title']=='Healthy incoming edit' for row in (o.list_events() if kind=='calendar' else o.list_todos())))
                    self.assertFalse([(op,p) for op,p in b.calls if op not in READS])
                    self.assertIn('a',result['failed_calendar_ids' if kind=='calendar' else 'failed_list_ids'])
                    self.assertEqual(result['status'],'partial')
                    self.assertFalse(s.status()['scan_claims'])
                    self.assertFalse(o.list_apple_inbound_recoveries())

    def test_unverifiable_source_waits_and_exact_source_return_resumes(self):
        for kind in ('calendar','reminders'):
            for fault in ('native_source_missing','owner_source_missing','owner_id_changed'):
                with self.subTest(kind=kind,fault=fault),M['case'](kind) as (s,o,b):
                    fresh,healthy,link=self.fixture(kind,s,o,b)
                    saved=copy.deepcopy(o.get_category(link['alcor_id'])['apple'])
                    if fault=='native_source_missing':b.sources[0]['source_id']=''
                    else:o.apple_bind('category',link['alcor_id'],{**saved, 'source_id' if fault=='owner_source_missing' else 'apple_id':'' if fault=='owner_source_missing' else 'different'})
                    b.rows=[fresh,healthy];b.calls.clear();r=s.sync()
                    self.assertEqual(r['imported'],0);self.assertNotEqual(r['status'],'success')
                    self.assertFalse([(op,p) for op,p in b.calls if op not in READS])
                    self.assertFalse(s.status()['scan_claims'])
                    b.sources[0]['source_id']='account1';o.apple_bind('category',link['alcor_id'],saved)
                    r=s.sync();self.assertEqual(r['status'],'success');self.assertEqual(r['imported'],1)

    def test_same_source_reconnect_preserves_direction_and_normal_sync(self):
        for kind in ('calendar','reminders'):
            for direction in ('bidirectional','import','export'):
                with self.subTest(kind=kind,direction=direction),M['case'](kind) as (s,o,b):
                    fresh,healthy,link=self.fixture(kind,s,o,b,writable=direction!='import')
                    s.replace_links([{**row,'direction':direction if row['apple_id']=='a' else row['direction'],'enabled':True} for row in s._stored_links()])
                    s.disconnect();b.rows=[fresh,healthy]
                    selected=[{key:row[key] for key in ('apple_id','alcor_id')} for row in s._stored_links()]
                    r=s.start_sync(selected)['result']
                    self.assertEqual(r['status'],'success');self.assertEqual(r['imported'],0 if direction=='export' else 1)
                    self.assertEqual(next(row for row in s._stored_links() if row['apple_id']=='a')['direction'],direction)
                    if direction!='import':
                        local=self.local(kind,o,link['alcor_id']);r=s.sync()
                        self.assertEqual(r['exported'],1);self.assertTrue(any(row['title']==local['title'] for row in b.rows))

    def test_legacy_item_source_is_verified_from_exact_runtime_identity(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                fresh,healthy,link=self.fixture(kind,s,o,b,empty=False)
                key='alcor_id' if kind=='calendar' else 'todo_id';group='calendar_id' if kind=='calendar' else 'list_id'
                item=next(row for row in s._maps() if row[group]=='a')[key]
                binding=copy.deepcopy(s._local(item)['apple']);binding.pop('source_id');o.apple_bind('item',item,binding)
                b.rows[0]['title']='Verified legacy item';r=s.sync()
                self.assertEqual(s._local(item)['title'],'Verified legacy item')
                self.assertEqual(s._local(item)['apple']['source_id'],'account1')
                b.sources[0]['source_id']='account2';b.rows[0].update(source_id='account2',title='Foreign replacement')
                before=copy.deepcopy(s._local(item));b.calls.clear();r=s.sync()
                self.assertNotEqual(r['status'],'success');self.assertEqual(s._local(item),before)
                self.assertFalse([(op,p) for op,p in b.calls if op not in READS])

    def test_category_change_after_claim_is_not_permanently_held(self):
        from apple_sync import group_scan_claims as claims
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                fresh,healthy,link=self.fixture(kind,s,o,b)
                saved=copy.deepcopy(o.get_category(link['alcor_id'])['apple'])
                b.rows=[fresh,healthy];original=claims.acquire;changed=[]
                def acquire(*args,**kwargs):
                    token=original(*args,**kwargs)
                    if args[2]=='a' and not changed:
                        changed.append(True);o.apple_bind('category',link['alcor_id'],{**saved,'source_id':'account2'})
                    return token
                b.calls.clear()
                with patch.object(claims,'acquire',side_effect=acquire):r=s.sync()
                self.assertEqual(r['status'],'partial');self.assertEqual(r['imported'],0)
                self.assertFalse(s.status()['scan_claims'])
                self.assertFalse([(op,p) for op,p in b.calls if op not in READS])
                o.apple_bind('category',link['alcor_id'],saved)
                self.assertEqual(s.sync()['imported'],1)

    def test_changed_scan_evidence_is_not_released_by_source_rejection(self):
        from apple_sync import group_scan_claims as claims
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                fresh,healthy,link=self.fixture(kind,s,o,b)
                saved=copy.deepcopy(o.get_category(link['alcor_id'])['apple'])
                b.rows=[fresh,healthy];original=claims.acquire
                def acquire(*args,**kwargs):
                    token=original(*args,**kwargs)
                    if args[2]=='a':
                        o.apple_bind('category',link['alcor_id'],{**saved,'source_id':'account2'})
                        with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?",('{"stage":"sending"}',token))
                    return token
                b.calls.clear()
                with patch.object(claims,'acquire',side_effect=acquire):r=s.sync()
                self.assertEqual(r['imported'],0)
                self.assertTrue(any(row['group_id']=='a' for row in s.status()['scan_claims']))
                self.assertFalse([(op,p) for op,p in b.calls if op not in READS])

    def test_changed_source_does_not_rename_or_move_into_wrong_category(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                fresh,healthy,link=self.fixture(kind,s,o,b,empty=False)
                # A local rename and a move target must not bypass the group scan guard.
                o.update_category(link['alcor_id'],{'name':'Private category name'})
                key='alcor_id' if kind=='calendar' else 'todo_id';group='calendar_id' if kind=='calendar' else 'list_id'
                item=next(row for row in s._maps() if row[group]=='healthy')[key]
                (o.update_event if kind=='calendar' else o.update_todo)(item,{'category_id':link['alcor_id']})
                before=copy.deepcopy(s._local(item));b.sources[0]['source_id']='account2';b.rows[0]['source_id']='account2'
                b.calls.clear();r=s.sync()
                self.assertEqual(r['exported'],0);self.assertEqual(s._local(item),before)
                self.assertFalse([(op,p) for op,p in b.calls if op not in READS])
                self.assertEqual(b.sources[0]['title'],'Synthetic')

if __name__=='__main__':unittest.main(verbosity=2)

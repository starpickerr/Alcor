"""Production category synchronization with real temporary owner/manager stores."""
import copy
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))
from apple_sync.bridge import BridgeError
from apple_sync.category_create import create_category,CreatePending
from apple_sync.categories import synchronize_categories
from apple_sync.group_scan_claims import WorkClaim

class CreateEntry(unittest.TestCase):
    def setup_case(self,kind,s,o,b):
        s.start_sync();local=o.create_category({'name':'New local','color':'#112233'});b.calls.clear()
        return local
    def test_normal_create_once_both_domains_and_explicit_source(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=self.setup_case(kind,s,o,b)
                o.apple_bind('category',local['id'],{'source_id':'account2'})
                for _ in range(2):synchronize_categories(s,o,kind)
                link=next(row for row in s._stored_links() if row['alcor_id']==local['id'])
                self.assertEqual(o.get_category(local['id'])['apple']['source_id'],'account2')
                self.assertEqual(len([x for x in b.calls if x[0] in ('create_calendar','reminder_list_create')]),1)
                self.assertEqual(link['apple_id'],o.get_category(local['id'])['apple']['apple_id'])
                self.assertFalse(s.status()['category_creates'])
    def test_lost_native_reply_never_duplicates_or_guesses_same_name_and_healthy_updates(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=self.setup_case(kind,s,o,b);original=b.call;writes=[]
                def lost(op,**kw):
                    result=original(op,**kw)
                    if op in ('create_calendar','reminder_list_create'):writes.append(kw);raise BridgeError('lost native reply')
                    return result
                with patch.object(b,'call',side_effect=lost):
                    for i in range(3):
                        b.rows[0]['title']='Healthy '+str(i)
                        s.sync()
                self.assertEqual(len(writes),1)
                self.assertEqual(len(o.categories()),2);self.assertFalse(o.get_category(local['id']).get('apple'))
                self.assertEqual(len(s._stored_links()),1)
                ident=s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id']
                self.assertEqual((o.get_event if kind=='calendar' else o.get_todo)(ident)['title'],'Healthy 2')
                pending=s.status()['category_creates'];self.assertEqual(pending[0]['state'],'native_unknown')
                self.assertIn('不会',pending[0]['reason'])
                # Another source remains discoverable despite matching the pending local title.
                b.sources.append(dict(b.sources[0],id='other-account',source_id='account2',title='New local'))
                s.sync();self.assertEqual(len(s._stored_links()),2)
                self.assertNotEqual(s._stored_links()[-1]['alcor_id'],local['id'])
    def test_verified_presend_failure_releases_and_next_cycle_succeeds(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=self.setup_case(kind,s,o,b);original=b.call;attempts=[]
                def rejected(op,**kw):
                    if op in ('create_calendar','reminder_list_create'):
                        attempts.append(kw);raise BridgeError('verified before save',native_no_side_effects=True)
                    return original(op,**kw)
                with patch.object(b,'call',side_effect=rejected):synchronize_categories(s,o,kind)
                self.assertEqual(len(attempts),1);self.assertEqual(s.status()['category_creates'][0]['state'],'waiting')
                synchronize_categories(s,o,kind)
                self.assertEqual(len(s._stored_links()),2);self.assertFalse(s.status()['category_creates'])
    def test_old_helper_prevents_only_new_create_and_retries_after_update(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=self.setup_case(kind,s,o,b);original=b.call
                def old(op,**kw):
                    result=original(op,**kw)
                    if op in ('status','reminder_status'):result.pop('category_create_version',None)
                    return result
                with patch.object(b,'call',side_effect=old):synchronize_categories(s,o,kind)
                self.assertFalse(any(op in ('create_calendar','reminder_list_create') for op,_ in b.calls))
                self.assertTrue(s.status()['connected']);self.assertEqual(s.status()['category_creates'][0]['state'],'waiting')
                synchronize_categories(s,o,kind);self.assertEqual(len(s._stored_links()),2)
    def test_owner_commit_unknown_keeps_intent_and_real_group_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=self.setup_case(kind,s,o,b);original=o.apple_apply_category_if_unchanged
                def lost(expected,*args):
                    result=original(expected,*args)
                    if expected['local_id']==local['id']:raise RuntimeError('owner reply lost')
                    return result
                with patch.object(o,'apple_apply_category_if_unchanged',side_effect=lost):synchronize_categories(s,o,kind)
                before=copy.deepcopy(o.get_category(local['id']))
                synchronize_categories(s,o,kind)
                self.assertEqual(o.get_category(local['id']),before)
                self.assertEqual(len([x for x in b.calls if x[0] in ('create_calendar','reminder_list_create')]),1)
                self.assertEqual(s.status()['category_creates'][0]['state'],'owner_claiming')
                self.assertTrue(s.status()['scan_claims']);self.assertEqual(len(s._stored_links()),1)
    def test_old_intent_does_not_transfer_to_recreated_id(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=self.setup_case(kind,s,o,b);old=o.apple_category_incarnation(o.apple_capture_category_absence(local['id']))
                original=b.call
                def lost(op,**kw):
                    if op in ('create_calendar','reminder_list_create'):raise BridgeError('unknown')
                    return original(op,**kw)
                with patch.object(b,'call',side_effect=lost):synchronize_categories(s,o,kind)
                with o._connect() as db:
                    row=tuple(db.execute(f'SELECT * FROM {o.APPLE_CATEGORIES} WHERE id=?',(local['id'],)).fetchone())
                    db.execute(f'DELETE FROM {o.APPLE_CATEGORIES} WHERE id=?',(local['id'],))
                    db.execute(f'INSERT INTO {o.APPLE_CATEGORIES} VALUES (?,?,?,?)',row)
                self.assertNotEqual(o.apple_category_incarnation(o.apple_capture_category_absence(local['id'])),old)
                b.calls.clear();synchronize_categories(s,o,kind)
                self.assertFalse(any(op in ('create_calendar','reminder_list_create') for op,_ in b.calls))
                self.assertFalse(o.get_category(local['id']).get('apple'));self.assertEqual(len(s._stored_links()),1)
    def test_create_and_first_bind_share_category_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                local=self.setup_case(kind,s,o,b)
                with WorkClaim(s,{'other':'account1'},[],{'test':'binding'},category_ids=[local['id']],category_kind=kind):
                    synchronize_categories(s,o,kind)
                self.assertFalse(any(op in ('create_calendar','reminder_list_create') for op,_ in b.calls))
                synchronize_categories(s,o,kind);self.assertEqual(len(s._stored_links()),2)

if __name__=='__main__':unittest.main()

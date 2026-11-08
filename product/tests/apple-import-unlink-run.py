"""Stop one import and delete its local category, never modify its Apple source."""
import copy,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))
from apple_sync.approved_delete import prepare,run
from business.import_category_delete import prepare as owner_prepare,commit as owner_commit

class ImportUnlink(unittest.TestCase):
    def setup(self,s,o,b,kind,immutable=False):
        b.sources[0].update(writable=False,immutable=immutable);b.rows[0]['writable']=False
        b.sources.append({**b.sources[0],'id':'other','title':'Healthy source'})
        remote=copy.deepcopy(b.rows[0]);remote['calendar_id' if kind=='calendar' else 'list_id']='other'
        remote['event_id' if kind=='calendar' else 'reminder_id']='healthy';remote['external_id']='healthy-external';b.rows.append(remote)
        s.start_sync();link=next(r for r in s._stored_links() if r['apple_id']=='a');domain='tasks' if kind=='reminders' else 'calendar'
        def prep(action,**kw):
            self.assertEqual(action,'prepare_import_category_delete');self.assertEqual(kw.pop('domain'),domain)
            return owner_prepare(o,**kw)
        plan=prepare(s,domain,'b'*64,category_id=link['alcor_id'],prepare_owner=prep)
        def commit(action,**kw):
            self.assertEqual(action,'commit_import_category_delete')
            return owner_commit(o,domain,**kw)
        b.calls.clear();return plan,commit,link
    def writes(self,b):return [op for op,_ in b.calls if op in ('upsert','reminder_upsert','delete','reminder_delete','delete_calendar','reminder_list_delete','create_calendar','reminder_list_create','rename','reminder_rename')]
    def test_readonly_container_remains_and_reconnect_does_not_recreate_local(self):
        for kind in ('calendar','reminders'):
            for immutable in (False,True):
                with self.subTest(kind=kind,immutable=immutable),M['case'](kind) as (s,o,b):
                    plan,commit,link=self.setup(s,o,b,kind,immutable);native=copy.deepcopy(b.rows)
                    result=run(s,plan,lambda *a:True,commit)
                    self.assertTrue(result['apple_unchanged']);self.assertTrue(result['import_stopped'])
                    self.assertEqual(b.rows,native);self.assertFalse(self.writes(b))
                    for _ in range(3):s.disconnect();s.start_sync();s.sync()
                    self.assertFalse(any(c['id']==link['alcor_id'] for c in o.categories()))
                    self.assertEqual(len(o.categories()),1);self.assertFalse(next(r for r in s._stored_links() if r['apple_id']=='a')['enabled'])
                    b.rows[1]['title']='Healthy change';s.sync()
                    active=o.list_events() if kind=='calendar' else o.list_todos()
                    self.assertEqual([r['title'] for r in active],['Healthy change']);self.assertFalse(self.writes(b))
                    self.assertEqual(run(s,plan,lambda *a:False,commit)['recovery_id'],result['recovery_id'])
    def test_explicit_reselection_imports_fresh_local_copy(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,commit,link=self.setup(s,o,b,kind);run(s,plan,lambda *a:True,commit)
                links=[{**r,'enabled':bool(r['enabled'])} for r in s._stored_links() if r['apple_id']!='a']
                links.append({'apple_id':'a','alcor_id':'','create_name':'Import again','direction':'import','enabled':True})
                s.replace_links(links);s.sync();s.sync()
                new=next(r for r in s._stored_links() if r['apple_id']=='a')
                self.assertNotEqual(new['alcor_id'],link['alcor_id']);self.assertTrue(new['enabled']);self.assertEqual(new['direction'],'import')
                active=o.list_events() if kind=='calendar' else o.list_todos()
                self.assertEqual(len(active),2);self.assertEqual(len(s._maps()),2);self.assertFalse(self.writes(b))
    def test_cancel_and_owner_change_preserve_local_and_enabled_link(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,commit,link=self.setup(s,o,b,kind)
                with self.assertRaises(PermissionError):run(s,plan,lambda *a:False,commit)
                self.assertEqual(s._stored_links()[0]['enabled'],1)
                def changed(action,**kw):
                    with o._connect() as db:db.execute('UPDATE '+o.APPLE_CATEGORIES+' SET name=? WHERE id=?',('New local choice',link['alcor_id']))
                    return commit(action,**kw)
                with self.assertRaises(ValueError):run(s,plan,lambda *a:True,changed)
                self.assertEqual(o.get_category(link['alcor_id'])['name'],'New local choice')
                self.assertTrue(next(r for r in s._stored_links() if r['apple_id']=='a')['enabled'])
                self.assertFalse(s.status()['scan_claims']);self.assertFalse(self.writes(b))
    def test_unknown_owner_reply_keeps_import_stopped_and_receipt_can_finish_once(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                plan,commit,link=self.setup(s,o,b,kind)
                def lost(action,**kw):commit(action,**kw);raise RuntimeError('owner committed, reply lost')
                with self.assertRaises(RuntimeError):run(s,plan,lambda *a:True,lost)
                self.assertFalse(next(r for r in s._stored_links() if r['apple_id']=='a')['enabled'])
                self.assertEqual(s.status()['scan_claims'][0]['state'],'uncertain')
                self.assertTrue(run(s,plan,lambda *a:True,commit)['apple_unchanged']);s.sync()
                self.assertFalse(any(c['id']==link['alcor_id'] for c in o.categories()));self.assertFalse(self.writes(b))

if __name__=='__main__':unittest.main(verbosity=2)

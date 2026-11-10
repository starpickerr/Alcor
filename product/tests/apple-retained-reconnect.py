"""Retained rebuild members must not become automatic exports on reconnect/restart."""
import copy,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-category-manual-recovery.py')))
class RetainedReconnect(unittest.TestCase):
    def prepare(self,s,o,b):
        t=M['ManualCategoryRecovery']();link,item=t.setup_missing(s,o,b)
        payload={'title':'Retained unmapped','category_id':link['alcor_id']}
        if b.kind=='calendar':old=o.create_event({**payload,'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'})
        else:old=o.create_todo({**payload,'due_on':'2026-11-09'})
        t.perform(s,o,b);return link,item,old
    def test_start_sync_keeps_retained_unmapped_members_local(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                link,item,old=self.prepare(s,o,b);before=copy.deepcopy(s._local(old['id']))
                s.start_sync();s.start_sync();s.sync()
                self.assertEqual(b.rows,[]);self.assertEqual(s._local(old['id']),before)
                self.assertEqual(s.status()['retained_categories'][0]['member_count'],2)

    def test_restart_direction_and_reconnect_keep_hold_but_new_normal_item_exports(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                link,item,old=self.prepare(s,o,b);before=copy.deepcopy(s._local(old['id']))
                s.disconnect()
                s=type(s)(bridge=b,db_path=s.db_path,**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}),clock=s.clock)
                s.start_auto=lambda:None
                s.start_sync([{key:row[key] for key in ('apple_id','alcor_id')} for row in s._stored_links()])
                for direction in ('import','export','bidirectional'):
                    selected=[{**row,'direction':direction,'enabled':True} for row in s._stored_links()]
                    s.replace_links(selected);s.sync()
                    self.assertEqual(s._stored_links()[0]['direction'],direction)
                    s.start_sync([{key:row[key] for key in ('apple_id','alcor_id')} for row in selected]);s.sync()
                with s._connect() as db:db.execute('DELETE FROM baseline_local')
                s.sync();self.assertFalse(b.rows);self.assertEqual(s._local(old['id']),before)
                data={'title':'New normal','category_id':link['alcor_id']}
                if kind=='calendar':new=o.create_event({**data,'start_at':'2026-11-10T01:00:00Z','end_at':'2026-11-10T02:00:00Z'})
                else:new=o.create_todo({**data,'due_on':'2026-11-10'})
                s.sync();s.sync()
                self.assertEqual([row['title'] for row in b.rows],['New normal'])
                self.assertEqual(s._local(old['id']),before);self.assertEqual(len(s._maps()),2)
    def test_direct_write_payload_and_metadata_binding_cannot_bypass_retained_hold(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                link,item,old=self.prepare(s,o,b);before=copy.deepcopy(s._local(old['id']))
                group=s._stored_links()[0]['apple_id'];native=copy.deepcopy(s._local(item)['apple']['native'])
                native['calendar_id' if kind=='calendar' else 'list_id']=group
                with self.assertRaisesRegex(ValueError,'原成员'):
                    s._write_payload(before,**({'calendar_id':group} if kind=='calendar' else {'list_id':group}))
                with self.assertRaisesRegex(ValueError,'原成员'):s._new_map(before,native,'alcor')
                self.assertEqual(s._local(old['id']),before);self.assertFalse(b.rows)
    def test_native_marker_cannot_silently_reassociate_retained_unmapped_member(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                link,item,old=self.prepare(s,o,b);before=copy.deepcopy(s._local(old['id']))
                native=copy.deepcopy(s._local(item)['apple']['native'])
                native.update(alcor_id=old['id'],title='Remote marker candidate',external_id='new-external')
                native['calendar_id' if kind=='calendar' else 'list_id']=s._stored_links()[0]['apple_id']
                native['event_id' if kind=='calendar' else 'reminder_id']='new-native'
                b.rows=[native];b.calls.clear()
                for _ in range(3):s.sync()
                self.assertEqual(s._local(old['id']),before);self.assertEqual(len(s._maps()),1)
                self.assertEqual(len(o.list_events() if kind=='calendar' else o.list_todos()),2)
                self.assertFalse(any(op in ('upsert','reminder_upsert','delete','reminder_delete') for op,_ in b.calls))

if __name__=='__main__':unittest.main(verbosity=2)

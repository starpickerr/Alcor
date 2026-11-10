"""Reviewer's reachable retained-member manual and recurring-reminder regressions."""
import copy,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-category-manual-recovery.py')))

class RetainedConflictSuccessor(unittest.TestCase):
    def test_manual_conflict_does_not_release_retained_members(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                t=M['ManualCategoryRecovery']();link,item=t.setup_missing(s,o,b)
                old=copy.deepcopy(s._local(item));s._conflict(s._maps()[0],'original conflict');t.perform(s,o,b)
                remote=copy.deepcopy(old['apple']['native']);remote['list_id' if kind=='reminders' else 'calendar_id']=s._stored_links()[0]['apple_id'];remote['title']='Native later';b.rows=[remote]
                b.sources.append({**b.sources[0],'id':'a','title':'Old source returns'})
                before=copy.deepcopy(s._local(item));maps=copy.deepcopy(s._maps());retained=copy.deepcopy(s.status()['retained_categories']);b.calls.clear()
                for choice in ('keep_apple','keep_alcor'):
                    with self.assertRaises(ValueError):s.resolve_conflict(item,choice)
                    self.assertEqual(s._local(item),before);self.assertEqual(s._maps(),maps)
                    self.assertEqual(s.status()['retained_categories'],retained)
                self.assertFalse(any(op in ('upsert','reminder_upsert','delete','reminder_delete') for op,_ in b.calls))
    def successor(self,b,old):
        completed=copy.deepcopy(old['apple']['native']);completed.update(completed=True,completed_at='2026-11-07T01:00:00Z')
        following=copy.deepcopy(completed);following.update(reminder_id='next',completed=False,completed_at=None,due_on='2026-11-08')
        b.rows=[completed,following]
    def test_reselect_old_group_cannot_advance_retained_recurring_member(self):
        with M['M']['case']('reminders') as (s,o,b):
            b.rows[0].update(due_on='2026-11-07',recurrence={'frequency':'daily'})
            t=M['ManualCategoryRecovery']();link,item=t.setup_missing(s,o,b);old=copy.deepcopy(s._local(item));t.perform(s,o,b)
            b.sources.append({**b.sources[0],'id':'a','title':'Old returned'});self.successor(b,old)
            before=copy.deepcopy(s._local(item));maps=copy.deepcopy(s._maps());b.calls.clear()
            for _ in range(3):
                s.start_sync([{'apple_id':'a','alcor_id':link['alcor_id']}]);s.sync()
                self.assertEqual(s._local(item),before);self.assertEqual(s._maps(),maps)
                self.assertEqual(len(o.list_todos()),1);self.assertEqual(s.status()['retained_categories'][0]['member_count'],1)
            self.assertFalse(any(op in ('reminder_upsert','reminder_complete','reminder_delete') for op,_ in b.calls))
    def test_normal_unretained_native_successor_still_advances_once(self):
        with M['M']['case']('reminders') as (s,o,b):
            b.rows[0].update(due_on='2026-11-07',recurrence={'frequency':'daily'})
            s.start_sync();item=s._maps()[0]['todo_id'];old=copy.deepcopy(s._local(item));self.successor(b,old);b.calls.clear()
            s.sync();s.sync()
            self.assertEqual(s._maps()[0]['reminder_id'],'next');self.assertEqual(s._local(item)['due_on'],'2026-11-08')
            self.assertEqual(len(o.list_todos()),1)
            self.assertFalse(any(op in ('reminder_upsert','reminder_complete','reminder_delete') for op,_ in b.calls))

if __name__=='__main__':unittest.main(verbosity=2)

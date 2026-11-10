"""Actual owner pending/claim errors and cancellation without reopening delete sinks."""
import runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))

class DeleteStateMessages(unittest.TestCase):
    def test_historical_pending_cancel_and_started_unknown_never_offer_retry(self):
        for kind in ('calendar','reminders'):
            for entity in ('item','category'):
                for state,text in (('pending','等待本次确认'),('sending','正在提交'),('uncertain','结果未知')):
                    with self.subTest(kind=kind,entity=entity,state=state),M['case'](kind) as (s,o,b):
                        s.start_sync();item=s._maps()[0]['alcor_id' if kind=='calendar' else 'todo_id'];identity=item if entity=='item' else s._stored_links()[0]['alcor_id']
                        with o._connect() as db:db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)',('historical','item' if entity=='item' else 'category',identity,'delete','{}',state,'','2026-11-07'))
                        with o._connect() as db:
                            with self.assertRaisesRegex(ValueError,text):o._apple_guard(db,'item',item,'can_modify')
                        if state=='pending':
                            self.assertTrue(o.cancel_apple_operation('historical')['cancelled'])
                            self.assertEqual(o.apple_operations(),[])
                        else:
                            with self.assertRaises(ValueError):o.cancel_apple_operation('historical')
                            self.assertEqual(o.apple_operations()[0]['state'],state)
                        self.assertIsNotNone(s._local(item))
                        self.assertFalse(any(op in ('delete','delete_calendar','reminder_delete','reminder_list_delete') for op,_ in b.calls))

if __name__=='__main__':unittest.main(verbosity=2)

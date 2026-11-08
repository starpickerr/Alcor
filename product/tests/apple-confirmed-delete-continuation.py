"""One stored user intent across manager restart; synthetic Host, owner and EventKit."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-approved-delete-run.py')))
W=runpy.run_path(str(Path(__file__).resolve().parents[2]/'packages/alcor-apple-sync/python/worker.py'))
from business.apple_delete_approval import prepare_confirmed
from apple_sync.bridge import BridgeError

class Continuation(unittest.TestCase):
    def host(self,s,o,b,kind):
        active={};calls=[]
        def peer(args):
            calls.append(copy.deepcopy(args));route=args['route']
            if route=='approved_delete_continue':
                if args['phase']=='begin':
                    self.assertFalse(active)
                    plan=prepare_confirmed(o,'tasks' if kind=='reminders' else 'calendar',args['operation_id'])
                    if plan:active.update(plan)
                    return plan
                self.assertEqual(args['plan_fingerprint'],active['plan_fingerprint']);active.clear();return True
            self.assertTrue(active);self.assertEqual(args['operation_id'],active['operation_id'])
            self.assertEqual(args['plan_fingerprint'],active['plan_fingerprint']);self.assertEqual(args['local_id'],active['local_plan']['id'])
            if route=='approved_delete':return True
            self.assertEqual(route,'approved_delete_owner')
            return getattr(o,args['action'])(**args['args'])
        worker=W['AppleWorker'].__new__(W['AppleWorker']);worker.verification_no_apple=False;worker.managers={kind:s};worker.peer=peer
        s.continue_approved_delete=lambda identity:worker._continue_approved_delete(kind,identity)
        return active,calls
    def restart(self,s,o,b,kind):
        result=type(s)(bridge=b,db_path=s.db_path,**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}),clock=s.clock)
        result.start_auto=lambda:None
        return result
    def test_confirmed_item_and_category_resume_after_restart_once(self):
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            for entity in ('item','category'):
                with self.subTest(kind=kind,entity=entity),M['M']['case'](kind) as (s,o,b):
                    plan,_=helper.setup(s,o,b,kind,entity,confirmed=True);s=self.restart(s,o,b,kind)
                    active,calls=self.host(s,o,b,kind);s.sync();s.sync()
                    self.assertEqual(helper.writes(b),['delete_calendar' if kind=='calendar' else 'reminder_list_delete'] if entity=='category' else ['delete' if kind=='calendar' else 'reminder_delete'])
                    self.assertFalse(active);self.assertFalse(o.apple_operations());self.assertTrue(o.list_apple_inbound_recoveries())
                    self.assertEqual(sum(c.get('phase')=='begin' for c in calls),1)
    def test_proven_unsent_retry_after_restart_ignores_mapping_read_timestamp(self):
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                plan,_=helper.setup(s,o,b,kind,confirmed=True);self.host(s,o,b,kind);original=b.call
                def refuse(op,**kw):
                    if op in ('delete','reminder_delete'):raise BridgeError('offline before send',native_quiesced=True,native_no_side_effects=True)
                    return original(op,**kw)
                with patch.object(b,'call',side_effect=refuse):s.sync()
                self.assertEqual(o.apple_operations()[0]['state'],'pending');self.assertEqual(helper.writes(b),[])
                self.assertEqual(s.status()['operations'][0]['deletion_phase'],'not_sent')
                with s._connect() as db:db.execute("UPDATE mappings SET updated_at='2026-11-08T00:00:00Z'")
                s=self.restart(s,o,b,kind);active,_=self.host(s,o,b,kind);s.sync();s.sync()
                self.assertEqual(len(helper.writes(b)),1);self.assertFalse(active)
    def test_pause_cancel_legacy_changed_scope_and_unknown_do_not_dispatch(self):
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            for cause in ('paused','cancelled','legacy','local_changed','native_changed','import','permission','unknown'):
                with self.subTest(kind=kind,cause=cause),M['M']['case'](kind) as (s,o,b):
                    plan,_=helper.setup(s,o,b,kind,confirmed=cause!='legacy');self.host(s,o,b,kind)
                    if cause=='paused':s.disconnect()
                    if cause=='cancelled':s.cancel_operation(plan['operation_id'])
                    if cause=='local_changed':
                        with self.assertRaisesRegex(ValueError,'删除等待'):
                            (o.update_event if kind=='calendar' else o.update_todo)(plan['local_plan']['id'],{'title':'Local choice'})
                        # Synthetic legacy writer bypasses the public guard to exercise the dispatch CAS.
                        with o._connect() as db:db.execute('UPDATE '+o.APPLE_ITEMS+' SET title=? WHERE id=?',('Local choice',plan['local_plan']['id']))
                    if cause=='native_changed':b.rows[0]['title']='Native choice'
                    if cause=='import':
                        with s._connect() as db:db.execute("UPDATE category_links SET direction='import'")
                    original=b.call;attempts=[]
                    def unavailable(op,**kw):
                        if cause=='permission' and op in ('status','reminder_status'):return {'authorization':'denied'}
                        if cause=='unknown' and op in ('delete','reminder_delete'):
                            attempts.append(op);raise BridgeError('native result unknown',native_quiesced=False)
                        return original(op,**kw)
                    with patch.object(b,'call',side_effect=unavailable):s.sync();s=self.restart(s,o,b,kind);self.host(s,o,b,kind);s.sync()
                    self.assertEqual(helper.writes(b),[])
                    if cause=='unknown':self.assertEqual(len(attempts),1);self.assertEqual(o.apple_operations()[0]['state'],'uncertain')
                    self.assertIsNotNone(s._local(plan['local_plan']['id']))

if __name__=='__main__':unittest.main(verbosity=2)

"""Abrupt exit of synthetic deletion worker; no real EventKit or personal database."""
import copy,json,os,runpy,unittest,subprocess,sys
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-approved-delete-run.py')))
from apple_sync.approved_delete import run
from apple_sync.group_scan_claims import WorkClaim

class CrashRecovery(unittest.TestCase):
    def crash(self,s,o,b,plan,owner,point):
        native=s.db_path.parent/'fake-native-after-crash.json';input_file=s.db_path.parent/'crash-input.json'
        input_file.write_text(json.dumps({'manager':str(s.db_path),'owner':str(o.db_path),'kind':plan['kind'],'plan':plan,'point':point,'rows':b.rows,'sources':b.sources,'native':str(native)}))
        child=subprocess.run([sys.executable,'-B',__file__,'--child',str(input_file)],capture_output=True,text=True)
        self.assertEqual(child.returncode,23,child.stderr)
        if native.exists():
            data=json.loads(native.read_text());b.rows=data['rows'];b.sources=data['sources']
        return native.exists()
    def test_success_receipt_finishes_after_owner_or_manager_crash_without_resend(self):
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            for point in ('before_owner','after_owner','after_manager'):
                with self.subTest(kind=kind,point=point),M['M']['case'](kind) as (s,o,b):
                    plan,owner=helper.setup(s,o,b,kind,confirmed=True)
                    self.assertTrue(self.crash(s,o,b,plan,owner,point));self.assertTrue(s.status()['scan_claims'])
                    b.calls.clear()
                    self.assertEqual(run(s,plan,lambda *a:False,owner)['state'],'done')
                    self.assertFalse(helper.writes(b));self.assertFalse(s.status()['scan_claims'])
                    self.assertIsNone(s._local(plan['local_plan']['id']));self.assertTrue(o.list_apple_inbound_recoveries())
                    self.assertEqual(run(s,plan,lambda *a:False,owner)['state'],'done');self.assertFalse(helper.writes(b))
    def test_receipt_takeover_fences_late_original_writer_without_reopening_done(self):
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                plan,owner=helper.setup(s,o,b,kind,confirmed=True)
                native=s.db_path.parent/'fake-native-after-crash.json';input_file=s.db_path.parent/'crash-input.json'
                input_file.write_text(json.dumps({'manager':str(s.db_path),'owner':str(o.db_path),'kind':kind,'plan':plan,'point':'pause_after_receipt','rows':b.rows,'sources':b.sources,'native':str(native)}))
                child=subprocess.Popen([sys.executable,'-B',__file__,'--child',str(input_file)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                self.assertEqual(child.stdout.readline().strip(),'receipt-written')
                try:
                    data=json.loads(native.read_text());b.rows=data['rows'];b.sources=data['sources'];b.calls.clear()
                    self.assertEqual(run(s,plan,lambda *a:False,owner)['state'],'done')
                finally:out,err=child.communicate('\n',timeout=10)
                self.assertEqual(child.returncode,1,err);self.assertIn('manager operation claim or evidence changed',err)
                self.assertEqual(s._get('approved-delete:'+plan['operation_id'])['state'],'done')
                self.assertFalse(o.apple_operations());self.assertFalse(s.status()['scan_claims']);self.assertFalse(helper.writes(b))

    def test_altered_claim_or_receipt_cannot_take_over(self):
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            for changed in ('claim','receipt'):
                with self.subTest(kind=kind,changed=changed),M['M']['case'](kind) as (s,o,b):
                    plan,owner=helper.setup(s,o,b,kind,confirmed=True);self.crash(s,o,b,plan,owner,'before_owner')
                    before=copy.deepcopy(s._maps());local=copy.deepcopy(s._local(plan['local_plan']['id']))
                    if changed=='claim':
                        with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                    else:
                        path=s.db_path.parent/'native-receipts'/('delete-'+plan['operation_id']+'.json')
                        receipt=json.loads(path.read_text());receipt['delete_receipt']['source_id']='different-account';path.write_text(json.dumps(receipt))
                    b.calls.clear()
                    with self.assertRaises(ValueError):run(s,plan,lambda *a:False,owner)
                    self.assertEqual(s._maps(),before);self.assertEqual(s._local(plan['local_plan']['id']),local)
                    self.assertFalse(helper.writes(b));self.assertTrue(s.status()['scan_claims'])

    def test_restart_sync_uses_finish_only_scope_and_respects_pause(self):
        H=runpy.run_path(str(Path(__file__).with_name('apple-confirmed-delete-continuation.py')))
        from business.apple_delete_approval import verify_confirmed_finish
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            for point in ('before_owner','after_owner','after_manager'):
                with self.subTest(kind=kind,point=point),M['M']['case'](kind) as (s,o,b):
                    plan,owner=helper.setup(s,o,b,kind,confirmed=True);self.crash(s,o,b,plan,owner,point)
                    active={};calls=[]
                    def peer(args):
                        calls.append(copy.deepcopy(args))
                        if args['route']=='approved_delete_continue':
                            if args['phase']=='finish_begin':
                                self.assertFalse(active)
                                self.assertIs(verify_confirmed_finish(o,plan['domain'],plan['operation_id'],args['plan']),True)
                                active.update(args['plan']);return args['plan']
                            self.assertEqual(args['phase'],'end');active.clear();return True
                        self.assertTrue(active);self.assertEqual(args['operation_id'],plan['operation_id'])
                        self.assertEqual(args['plan_fingerprint'],plan['plan_fingerprint'])
                        if args['route']=='approved_delete':return False
                        self.assertEqual(args['action'],'apple_finish_approved_delete')
                        return owner(args['action'],**args['args'])
                    worker=H['W']['AppleWorker'].__new__(H['W']['AppleWorker']);worker.verification_no_apple=False;worker.peer=peer;worker.managers={kind:s}
                    s.finish_approved_delete=lambda frozen:worker._continue_approved_delete(kind,frozen['operation_id'],finish_plan=frozen)
                    s.disconnect();s.sync();self.assertEqual(calls,[])
                    s._set('connected',True);b.calls.clear();s.sync();s.sync()
                    self.assertIsNone(s._local(plan['local_plan']['id']));self.assertFalse(s.status()['scan_claims'])
                    self.assertFalse(active);self.assertFalse(helper.writes(b))
                    self.assertEqual(sum(row.get('phase')=='finish_begin' for row in calls),1)

    def test_recurring_receipt_finishes_local_exclusion_and_anchor_after_restart(self):
        contracts=M['M']['M']['M']
        for day in ('2026-11-07','2026-11-08'):
            for point in ('before_owner','after_owner','after_inbound'):
                with self.subTest(day=day,point=point):
                    case=contracts['Contracts']();case.setUp()
                    try:
                        b,s,item=case.local_series();o=case.business.calendar;original=b.call
                        b.call=lambda op,**kw:{**original(op,**kw),'delete_write_version':1} if op=='status' else original(op,**kw)
                        args={'id':item['id'],'scope':'this','occurrence_on':day};operation='c'*64
                        local=M['owner_prepare'](o,'calendar','delete_event',args,operation)
                        plan=M['prepare'](s,'calendar',operation,local)
                        M['owner_commit'](o,'calendar','delete_event',args,operation,local['snapshot'],plan['native'],confirmed_intent={'version':1,'plan_fingerprint':plan['plan_fingerprint']})
                        owner=lambda name,**kw:getattr(o,name)(**kw)
                        self.crash(s,o,b,plan,owner,point);b.calls.clear()
                        self.assertEqual(run(s,plan,lambda *a:False,owner)['state'],'done')
                        self.assertFalse(s.status()['scan_claims']);self.assertFalse(any(op=='delete' for op,_ in b.calls))
                        self.assertEqual(o.list_events(day+'T00:00:00Z',day+'T23:59:59Z'),[])
                        self.assertEqual(s.sync()['status'],'success');self.assertEqual(s.sync()['status'],'success')
                    finally:case.tearDown()

    def test_crash_without_success_receipt_keeps_unknown_and_no_retry(self):
        helper=M['ApprovedDelete']()
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                plan,owner=helper.setup(s,o,b,kind,confirmed=True)
                self.assertFalse(self.crash(s,o,b,plan,owner,'before_native'));before=copy.deepcopy(s._maps());b.calls.clear()
                with self.assertRaises(ValueError):run(s,plan,lambda *a:True,owner)
                self.assertFalse(helper.writes(b));self.assertEqual(s._maps(),before);self.assertIsNotNone(s._local(plan['local_plan']['id']))
                self.assertTrue(s.status()['scan_claims'])

def child(path):
    data=json.loads(Path(path).read_text());kind=data['kind'];point=data['point'];plan=data['plan']
    from business.calendar.domain.service import CalendarService
    from business.tasks.domain.service import TodoService
    from apple_sync.calendar import AppleCalendarSync
    from apple_sync.reminders import AppleReminderSync
    b=M['M']['M']['M']['NativeBridge'](kind)
    b.rows=data['rows'];b.sources=data['sources']
    o=(CalendarService if kind=='calendar' else TodoService)(data['owner'])
    s=(AppleCalendarSync if kind=='calendar' else AppleReminderSync)(bridge=b,db_path=data['manager'],**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}))
    original=b.call;exit_claim=WorkClaim.__exit__
    if point=='after_inbound':
        apply=o.apple_apply_calendar_inbound
        def applied(*args,**kwargs):
            apply(*args,**kwargs);os._exit(23)
        o.apple_apply_calendar_inbound=applied
    def bridge(op,**kw):
        if op in ('delete','reminder_delete') and point=='before_native':os._exit(23)
        value=original(op,**kw)
        if op in ('status','reminder_status'):return {**value,'delete_write_version':1}
        if op in ('delete','reminder_delete'):
            proof={'version':1,'binding':kw['receipt_binding'],'op':op,'source_id':kw['expected_source_id'],'expected_snapshot':kw['expected_snapshot'],'series':kw.get('series',False)}
            value={'deleted':True,'delete_receipt':proof}
            Path(kw['receipt_file']).write_text(json.dumps({'binding':kw['receipt_binding'],**value}))
            Path(data['native']).write_text(json.dumps({'rows':b.rows,'sources':b.sources}))
        return value
    def action(name,**kw):
        if name=='apple_finish_approved_delete' and point=='before_owner':os._exit(23)
        if name=='apple_finish_approved_delete' and point=='pause_after_receipt':
            print('receipt-written',flush=True);sys.stdin.readline()
        value=getattr(o,name)(**kw)
        if name=='apple_finish_approved_delete' and point=='after_owner':os._exit(23)
        return value
    def leave(claim,error_type,*args):
        if point=='after_manager' and error_type is None:os._exit(23)
        return exit_claim(claim,error_type,*args)
    with patch.object(b,'call',side_effect=bridge),patch.object(WorkClaim,'__exit__',leave):run(s,plan,lambda *a:True,action)
    if point!='pause_after_receipt':raise AssertionError('crash point was not reached')

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':child(sys.argv[2])
    else:unittest.main(verbosity=2)

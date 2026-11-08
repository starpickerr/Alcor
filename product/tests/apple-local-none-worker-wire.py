"""Actual Python worker entry points and SQLite with a synthetic Host peer, no Apple."""
import copy,json,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-local-none-manager-cas.py')))
W=runpy.run_path(str(Path(__file__).resolve().parents[2]/'packages/alcor-apple-sync/python/worker.py'))['AppleWorker']
from business.worker import Business
from apple_sync.metadata import conflict_summaries

class WorkerWire(unittest.TestCase):
    def worker(self,s,o,kind,identity,*,allowed=True):
        business=object.__new__(Business);setattr(business,'tasks' if kind=='reminders' else 'calendar',o)
        worker=object.__new__(W);worker.verification_no_apple=False;worker.managers={kind:s}
        state={'phase':'prepare','op':'b'*64,'capture':None,'messages':[]}
        def peer(request):
            request=json.loads(json.dumps(request));state['messages'].append(request)
            self.assertEqual(request['kind'],kind);self.assertEqual(request['local_id'],identity)
            self.assertEqual(request['operation_id'],state['op'])
            if request['route']=='item_restore':
                self.assertEqual(state['phase'],'run');self.assertEqual(request['plan_fingerprint'],state['plan']['plan_fingerprint'])
                return allowed
            self.assertEqual(request['kwargs'],{})
            method,args=request['method'],request['args']
            if state['phase']=='prepare':
                self.assertEqual(request['route'],'item_restore_prepare');self.assertEqual(method,'apple_capture_item_restore')
                self.assertEqual(len(args),5);state['capture']=copy.deepcopy(args)
            else:
                self.assertEqual(request['route'],'item_restore_owner');self.assertEqual(request['plan_fingerprint'],state['plan']['plan_fingerprint'])
                if state['phase']=='receipt_finish':self.assertEqual(method,'apple_verify_item_restore')
                if method=='apple_capture_item_restore':self.assertEqual(args,state['capture'])
                else:self.assertEqual(args,[state['op'],state['plan']['owner_expected']])
            return business.item_restore_peer('tasks' if kind=='reminders' else 'calendar',method,args,{})
        worker.peer=peer
        return worker,state
    def prepare(self,worker,state,kind,identity):
        prepared=worker.prepare_item_restore(kind,identity,state['op'])
        if prepared.get('mode')=='receipt_finish':
            state.update(phase='receipt_finish',plan=prepared['plan'],op=prepared['plan']['operation_id'])
        else:state.update(phase='run',plan=prepared)
        return prepared
    def test_private_route_roundtrip_status_and_normal_scan(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                identity,_=M['MissingLocalManager']().setup(s,o,b,kind)
                self.assertIs(s.status()['conflicts'][0]['local_missing'],True)
                worker,state=self.worker(s,o,kind,identity);plan=self.prepare(worker,state,kind,identity)
                result=worker.run_item_restore(kind,json.loads(json.dumps(plan)))
                self.assertEqual(result['status'],'success');self.assertEqual(result['local_id'],identity)
                s.sync();s.sync();self.assertEqual(s._local(identity)['title'],'Chosen Apple content');M['MissingLocalManager']().no_writes(b)
                s._conflict(s._maps()[0],'new unrelated conflict');self.assertIs(s.status()['conflicts'][0]['local_missing'],False)
    def test_lost_owner_response_only_verifies_on_next_explicit_choice(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
                identity,_=M['MissingLocalManager']().setup(s,o,b,kind);worker,state=self.worker(s,o,kind,identity)
                plan=self.prepare(worker,state,kind,identity);original=o.apple_restore_item
                def lost(*args):original(*args);raise RuntimeError('lost committed response')
                with patch.object(o,'apple_restore_item',side_effect=lost),self.assertRaises(RuntimeError):worker.run_item_restore(kind,plan)
                summary=s.status()['conflicts'][0]
                self.assertIs(summary['local_missing'],False);self.assertIs(summary['local_restore_pending'],True)
                before=copy.deepcopy(s._local(identity));state.update(phase='prepare',op='c'*64,messages=[])
                envelope=self.prepare(worker,state,kind,identity);self.assertEqual(envelope,{'mode':'receipt_finish','plan':plan})
                self.assertFalse(state['messages'])
                with patch.object(o,'apple_restore_item',side_effect=AssertionError('no second restore')):
                    result=worker.run_item_restore(kind,envelope)
                self.assertEqual(result['status'],'success');self.assertEqual(s._local(identity),before)
                self.assertFalse(s.status()['scan_claims']);self.assertTrue(state['messages'])
                self.assertFalse(s.status()['conflicts'])
                self.assertEqual({m['method'] for m in state['messages']},{'apple_verify_item_restore'});M['MissingLocalManager']().no_writes(b)
    def test_unknown_receipt_never_reissues_and_denied_context_keeps_missing(self):
        for kind in ('calendar','reminders'):
            for unknown in (True,False):
                with self.subTest(kind=kind,unknown=unknown),M['M']['case'](kind) as (s,o,b):
                    identity,_=M['MissingLocalManager']().setup(s,o,b,kind);worker,state=self.worker(s,o,kind,identity,allowed=unknown)
                    plan=self.prepare(worker,state,kind,identity)
                    if unknown:
                        with patch.object(o,'apple_restore_item',side_effect=RuntimeError('unproven response')),self.assertRaises(RuntimeError):worker.run_item_restore(kind,plan)
                        state.update(phase='prepare',op='c'*64,messages=[]);envelope=self.prepare(worker,state,kind,identity)
                        with patch.object(o,'apple_restore_item',side_effect=AssertionError('no retry')),self.assertRaises(ValueError):worker.run_item_restore(kind,envelope)
                        self.assertTrue(s.status()['scan_claims']);self.assertEqual({m['method'] for m in state['messages']},{'apple_verify_item_restore'})
                    else:
                        with self.assertRaises(PermissionError):worker.run_item_restore(kind,plan)
                        self.assertFalse(s.status()['scan_claims'])
                    self.assertIsNone(s._local(identity));M['MissingLocalManager']().no_writes(b)
    def test_missing_flag_does_not_hide_database_errors(self):
        def broken(_):raise RuntimeError('database unavailable')
        with self.assertRaisesRegex(RuntimeError,'database unavailable'):
            conflict_summaries([{'id':'one','conflict':'review'}],broken,'id')

if __name__=='__main__':unittest.main(verbosity=2)

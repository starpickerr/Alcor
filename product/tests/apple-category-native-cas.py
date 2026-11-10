"""Category writes use a bound native contract, with no real EventKit calls."""
import copy,json,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))
from apple_sync.bridge import BridgeError

class NativeCategoryCAS(unittest.TestCase):
    def setUp(self):self.base=M['CommonClaims']();self.base.setUp()
    def tearDown(self):self.base.tearDown()
    def scenario(self,kind):
        sync,bridge,owner=self.base.setup_domain(kind)
        local=sync._stored_links()[0]['alcor_id'];owner.apple_apply_category(local,{'name':'Chosen'})
        return sync,bridge,owner,local
    def test_conditions_and_saved_receipt_are_bound_to_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,local=self.scenario(kind);call=bridge.call;seen=[]
                def capture(op,**payload):
                    if op in ('rename','reminder_rename'):
                        self.assertEqual(payload.get('category_write_version'),1)
                        self.assertEqual(payload.get('expected_snapshot'),bridge.sources[0])
                        self.assertEqual(payload.get('expected_source_id'),'account1')
                        self.assertEqual(len(payload.get('receipt_binding','')),64)
                        seen.append(copy.deepcopy(payload))
                    return call(op,**payload)
                with patch.object(bridge,'call',side_effect=capture):sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertEqual(len(seen),1);self.assertEqual(bridge.sources[0]['title'],'Chosen')
                receipt=sync._get('category-write-receipt:'+seen[0]['receipt_binding'])
                self.assertEqual(receipt['result']['title'],'Chosen');self.assertEqual(sync.status()['scan_claims'],[])
    def test_old_helper_blocks_only_category_outbound_without_hold(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,local=self.scenario(kind);call=bridge.call;writes=[]
                def old(op,**payload):
                    value=call(op,**payload)
                    if op in ('status','reminder_status'):value.pop('category_write_version',None)
                    if op in ('rename','reminder_rename'):writes.append(op)
                    return value
                with patch.object(bridge,'call',side_effect=old):
                    with self.assertRaisesRegex(ValueError,'条件修改'):sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertEqual(writes,[]);self.assertEqual(sync.status()['scan_claims'],[])
                self.assertEqual(owner.get_category(local)['name'],'Chosen')
    def test_native_snapshot_change_rejects_before_save_and_can_retry(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,local=self.scenario(kind);call=bridge.call;once=[]
                def race(op,**payload):
                    if op in ('rename','reminder_rename') and not once:
                        once.append(True);bridge.sources[0]['title']='Concurrent Apple edit'
                    return call(op,**payload)
                with patch.object(bridge,'call',side_effect=race):
                    with self.assertRaises(BridgeError):sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertEqual(bridge.sources[0]['title'],'Concurrent Apple edit');self.assertEqual(sync.status()['scan_claims'],[])
                sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertEqual(bridge.sources[0]['title'],'Chosen')
    def test_receipt_mismatch_is_unknown_and_not_resent(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,local=self.scenario(kind);call=bridge.call;sent=[]
                def wrong(op,**payload):
                    value=call(op,**payload)
                    if op in ('rename','reminder_rename'):
                        sent.append(op);value['category_receipt']={'version':1,'binding':'wrong'}
                    return value
                with patch.object(bridge,'call',side_effect=wrong):
                    with self.assertRaisesRegex(ValueError,'回执'):sync.resolve_category_conflict('a','keep_alcor',field='name')
                    with self.assertRaises(ValueError):sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertEqual(len(sent),1);self.assertEqual(sync.status()['scan_claims'][0]['state'],'uncertain')
    def test_local_edit_during_capability_read_is_not_sent(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,local=self.scenario(kind);call=bridge.call;changed=[];sent=[]
                def race(op,**payload):
                    if op in ('status','reminder_status') and not changed:
                        changed.append(True);owner.apple_apply_category(local,{'name':'Later local edit'})
                    if op in ('rename','reminder_rename'):sent.append(op)
                    return call(op,**payload)
                with patch.object(bridge,'call',side_effect=race):
                    with self.assertRaisesRegex(ValueError,'changed'):sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertEqual(sent,[]);self.assertEqual(owner.get_category(local)['name'],'Later local edit')
                self.assertEqual(sync.status()['scan_claims'],[])

    def test_no_effect_proof_cannot_release_changed_claim(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner,local=self.scenario(kind);call=bridge.call
                def race(op,**payload):
                    if op in ('rename','reminder_rename'):
                        with sync._connect() as db:
                            db.execute("UPDATE calendar_scan_attempts SET state='sending' WHERE attempt_id IN (SELECT attempt_id FROM calendar_scan_groups)")
                        raise BridgeError('pre-save rejection with changed claim',native_no_side_effects=True)
                    return call(op,**payload)
                with patch.object(bridge,'call',side_effect=race):
                    with self.assertRaises(ValueError):sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertTrue(sync.status()['scan_claims']);self.assertEqual(bridge.sources[0]['title'],'Synthetic')

    def test_auto_color_owner_edit_after_send_retains_newer_local_choice(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);local=sync._stored_links()[0]['alcor_id']
                bridge.sources[0]['color']='#6B8AF7';sync.sync()
                owner.apple_apply_category(local,{'color':'#112233'});call=bridge.call;sent=[]
                def race(op,**payload):
                    result=call(op,**payload)
                    if op in ('rename','reminder_rename'):
                        sent.append(op);owner.apple_apply_category(local,{'color':'#445566'})
                    return result
                with patch.object(bridge,'call',side_effect=race):sync.sync();sync.sync()
                self.assertEqual(len(sent),1);self.assertEqual(owner.get_category(local)['color'],'#445566')
                self.assertEqual(sync._get('category-colors:a'),['#6B8AF7','#6B8AF7'])
                self.assertEqual(sync.status()['scan_claims'][0]['state'],'uncertain')

if __name__=='__main__':unittest.main(verbosity=2)

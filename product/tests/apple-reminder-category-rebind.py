"""Existing reminder category ID resets require item identity, never just a name."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class ReminderRebind(unittest.TestCase):
    def setUp(self):self.cases=[]
    def tearDown(self):
        for case in self.cases:case.tearDown()
    def setup_reset(self):
        self.base=M['CommonClaims']();self.base.setUp();self.cases.append(self.base)
        sync,bridge,owner=self.base.setup_domain('reminders');mapping=sync._maps()[0]
        bridge.sources[0]['id']='new-group';bridge.rows[0]['list_id']='new-group'
        bridge.calls.clear();return sync,bridge,owner,mapping
    def test_verified_reset_keeps_local_ids_and_content_without_native_write(self):
        sync,bridge,owner,mapping=self.setup_reset();owner.update_todo(mapping['todo_id'],{'notes':'Private notes'})
        # A native read-only value prevents this test's independent local edit being exported.
        bridge.rows[0]['writable']=False
        sync.sync();self.assertEqual(sync._stored_links()[0]['apple_id'],'new-group')
        self.assertEqual(owner.get_todo(mapping['todo_id'])['notes'],'Private notes')
        self.assertEqual(owner.get_todo(mapping['todo_id'])['apple']['group_id'],'new-group')
        self.assertEqual(len(sync._maps()),1);self.assertEqual(len(owner.categories()),1)
        self.assertFalse(any(op in ('reminder_upsert','reminder_list_create','reminder_delete') for op,_ in bridge.calls))
    def test_same_name_empty_category_is_not_identity_and_other_list_continues(self):
        sync,bridge,owner,mapping=self.setup_reset();category=sync._stored_links()[0]['alcor_id'];owner.apple_remove_item(mapping['todo_id'])
        with sync._connect() as db:db.execute('DELETE FROM mappings')
        bridge.rows=[];bridge.sources.append({**bridge.sources[0],'id':'other','title':'Healthy'})
        bridge.rows.append({**M['M']['NativeBridge']('reminders').rows[0],'list_id':'other','reminder_id':'other-item','external_id':'other-external'})
        for _ in range(2):sync.sync()
        self.assertEqual(next(row for row in sync._stored_links() if row['alcor_id']==category)['apple_id'],'a')
        self.assertFalse(any(row['apple_id']=='new-group' for row in sync._stored_links()))
        self.assertTrue(any(row['reminder_id']=='other-item' for row in sync._maps()))
    def test_same_name_unrelated_items_cannot_rebind_or_duplicate_import(self):
        sync,bridge,owner,mapping=self.setup_reset();before=copy.deepcopy(sync._stored_links())
        bridge.rows[0].update(reminder_id='unrelated',external_id='different')
        for _ in range(2):sync.sync()
        self.assertEqual(sync._stored_links(),before);self.assertEqual(len(sync._maps()),1)
        self.assertEqual(owner.get_todo(mapping['todo_id'])['apple']['group_id'],'a')
    def test_other_process_claim_prevents_rebind(self):
        sync,bridge,owner,mapping=self.setup_reset();before=copy.deepcopy(sync._stored_links())
        with M['other_process_holds'](sync,'new-group'):sync.sync()
        self.assertEqual(sync._stored_links(),before);self.assertEqual(owner.get_todo(mapping['todo_id'])['apple']['group_id'],'a')

    def test_complete_same_source_external_id_can_confirm_changed_item_id(self):
        sync,bridge,owner,mapping=self.setup_reset();bridge.rows[0]['reminder_id']='new-item'
        result=sync.sync();self.assertEqual(result['status'],'success',result)
        self.assertEqual(sync._maps()[0]['reminder_id'],'new-item');self.assertEqual(len(sync._maps()),1)
        self.assertEqual(sync._local(mapping['todo_id'])['apple']['apple_id'],'new-item')

    def test_empty_external_id_requires_the_exact_native_id(self):
        for changed in (False,True):
            with self.subTest(changed=changed):
                sync,bridge,owner,mapping=self.setup_reset()
                with sync._connect() as db:db.execute("UPDATE mappings SET external_id=''")
                bridge.rows[0]['external_id']=''
                if changed:bridge.rows[0]['reminder_id']='unknown-id'
                sync.sync()
                self.assertEqual(sync._stored_links()[0]['apple_id'],'a' if changed else 'new-group')
                self.assertEqual(len(sync._maps()),1)

    def test_duplicate_or_incomplete_native_inventory_keeps_original_identity(self):
        for failure in ('duplicate','incomplete','wrong_source'):
            with self.subTest(failure=failure):
                sync,bridge,owner,mapping=self.setup_reset();before=copy.deepcopy(sync._stored_links());call=bridge.call
                def inventory(op,**payload):
                    result=call(op,**payload)
                    if op=='reminders':
                        if failure=='duplicate':result['reminders'].append(copy.deepcopy(result['reminders'][0]))
                        if failure=='incomplete':result['successful_list_ids']=[]
                        if failure=='wrong_source':result['reminders'][0]['source_id']='account2'
                    return result
                with patch.object(bridge,'call',side_effect=inventory):sync.sync()
                self.assertEqual(sync._stored_links(),before);self.assertEqual(sync._local(mapping['todo_id'])['apple']['group_id'],'a')
                self.assertEqual(sync.status()['scan_claims'],[])

    def test_owner_edit_rejects_without_overwriting_and_can_retry_rebind(self):
        sync,bridge,owner,mapping=self.setup_reset();call=owner.apple_commit_reminder_category_rebind
        def commit(*args):
            owner.update_todo(mapping['todo_id'],{'notes':'Later local notes'})
            return call(*args)
        with patch.object(owner,'apple_commit_reminder_category_rebind',side_effect=commit):sync.sync()
        self.assertEqual(sync._stored_links()[0]['apple_id'],'a');self.assertEqual(sync._local(mapping['todo_id'])['notes'],'Later local notes')
        self.assertEqual(sync.status()['scan_claims'],[])
        bridge.rows[0]['writable']=False;sync.sync()
        self.assertEqual(sync._stored_links()[0]['apple_id'],'new-group');self.assertEqual(sync._local(mapping['todo_id'])['notes'],'Later local notes')

    def test_unknown_owner_commit_retains_claim_and_does_not_repeat(self):
        sync,bridge,owner,mapping=self.setup_reset();call=owner.apple_commit_reminder_category_rebind;seen=[]
        def commit(*args):
            result=call(*args);seen.append(result);raise RuntimeError('owner rebind response lost')
        with patch.object(owner,'apple_commit_reminder_category_rebind',side_effect=commit):sync.sync();sync.sync()
        self.assertEqual(len(seen),1);self.assertEqual(sync._stored_links()[0]['apple_id'],'a')
        self.assertEqual(sync._local(mapping['todo_id'])['apple']['group_id'],'new-group')
        self.assertEqual({row['group_id'] for row in sync.status()['scan_claims']},{'a','new-group'})
        self.assertTrue(all(row['state']=='uncertain' for row in sync.status()['scan_claims']))

    def test_manager_claim_change_after_owner_commit_keeps_original_mapping(self):
        sync,bridge,owner,mapping=self.setup_reset();call=owner.apple_commit_reminder_category_rebind
        def commit(*args):
            result=call(*args)
            with sync._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
            return result
        with patch.object(owner,'apple_commit_reminder_category_rebind',side_effect=commit):sync.sync()
        self.assertEqual(sync._maps()[0],mapping);self.assertEqual(sync._stored_links()[0]['apple_id'],'a')
        self.assertEqual(sync._local(mapping['todo_id'])['apple']['group_id'],'new-group')
        self.assertTrue(sync.status()['scan_claims'])

    def test_changed_external_identity_not_backed_by_owner_is_rejected(self):
        sync,bridge,owner,mapping=self.setup_reset()
        with sync._connect() as db:db.execute("UPDATE mappings SET external_id='different'")
        bridge.rows[0].update(reminder_id='new-unverified',external_id='different')
        sync.sync()
        self.assertEqual(sync._stored_links()[0]['apple_id'],'a')
        self.assertEqual(sync._local(mapping['todo_id'])['apple']['apple_id'],mapping['reminder_id'])
        self.assertEqual(sync.status()['scan_claims'],[])

if __name__=='__main__':unittest.main(verbosity=2)

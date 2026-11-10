"""Bounded existing category property paths with synthetic native data/SQLite only."""
import copy,runpy,unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

class CategoryClaims(unittest.TestCase):
    def setUp(self):self.base=M['CommonClaims']();self.base.setUp()
    def tearDown(self):self.base.tearDown()
    def test_normal_inbound_name_and_color_remain_enabled(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id']
                # The base synthetic source omits color; establish a real shared baseline first.
                bridge.sources[0]['color']='#6B8AF7';sync.sync()
                bridge.sources[0].update(title='Native renamed',color='#102030')
                result=sync.sync();self.assertEqual(result['status'],'success')
                local=owner.get_category(identity);self.assertEqual(local['name'],'Native renamed');self.assertEqual(local['color'],'#102030')
                self.assertEqual(sync.status()['scan_claims'],[])
    def test_manual_apple_choice_remains_enabled(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id']
                bridge.sources[0]['title']='Native choice'
                sync.resolve_category_conflict('a','keep_apple',field='name')
                self.assertEqual(owner.get_category(identity)['name'],'Native choice');self.assertEqual(sync.status()['scan_claims'],[])
    def test_both_domains_hold_group_and_members_during_native_rename(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id']
                owner.apple_apply_category(identity,{'name':'Local choice'});original=bridge.call;seen=[]
                def call(op,**payload):
                    if op in ('rename','reminder_rename'):
                        seen.append(True);self.assertEqual(self.base.competing_scan(sync,'a'),'blocked')
                    return original(op,**payload)
                with patch.object(bridge,'call',side_effect=call):sync.resolve_category_conflict('a','keep_alcor',field='name')
                self.assertEqual(len(seen),1);self.assertEqual(bridge.sources[0]['title'],'Local choice')
    def test_lost_native_rename_response_is_held_and_not_resent(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id']
                owner.apple_apply_category(identity,{'name':'Local choice'});original=bridge.call;sent=[]
                def lost(op,**payload):
                    result=original(op,**payload)
                    if op in ('rename','reminder_rename'):sent.append(True);raise RuntimeError('synthetic rename response lost')
                    return result
                with patch.object(bridge,'call',side_effect=lost):
                    with self.assertRaises(RuntimeError):sync.resolve_category_conflict('a','keep_alcor',field='name')
                    with self.assertRaises(ValueError):sync.resolve_category_conflict('a','keep_alcor',field='name')
                    sync.sync()
                self.assertEqual(len(sent),1);self.assertTrue(any(row['group_id']=='a' and row['state']=='uncertain' for row in sync.status()['scan_claims']))
    def test_lost_owner_apply_response_is_held_and_not_reapplied(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);bridge.sources[0]['title']='Native choice';original=owner.apple_apply_category_if_unchanged;applied=[]
                def lost(*args,**kwargs):
                    result=original(*args,**kwargs);applied.append(True);raise RuntimeError('synthetic owner response lost')
                with patch.object(owner,'apple_apply_category_if_unchanged',side_effect=lost):
                    with self.assertRaises(RuntimeError):sync.resolve_category_conflict('a','keep_apple',field='name')
                    sync.sync()
                self.assertEqual(len(applied),1);self.assertTrue(any(row['group_id']=='a' and row['state']=='uncertain' for row in sync.status()['scan_claims']))
    def test_unrelated_group_still_imports_when_category_is_held(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);bridge.sources.append({**bridge.sources[0],'id':'b','title':'Unrelated'})
                sync.sync();bridge.sources[0]['title']='Held rename';bridge.sources[1]['title']='Unrelated rename'
                with M['other_process_holds'](sync):sync.sync()
                by_apple={link['apple_id']:owner.get_category(link['alcor_id']) for link in sync._stored_links()}
                self.assertEqual(by_apple['a']['name'],'Synthetic');self.assertEqual(by_apple['b']['name'],'Unrelated rename')

    def test_owner_edit_after_observation_is_preserved_and_known_rejection_releases(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id']
                bridge.sources[0]['title']='Native chosen';original=owner.apple_apply_category_if_unchanged;changed=[]
                def race(expected,payload,binding=None):
                    if payload.get('name') and not changed:
                        owner.apple_apply_category(identity,{'name':'Later local edit'});changed.append(True)
                    return original(expected,payload,binding)
                with patch.object(owner,'apple_apply_category_if_unchanged',side_effect=race):
                    with self.assertRaisesRegex(ValueError,'CATEGORY_CHANGED:'):sync.resolve_category_conflict('a','keep_apple',field='name')
                self.assertEqual(owner.get_category(identity)['name'],'Later local edit');self.assertEqual(sync.status()['scan_claims'],[])
    def test_owner_edit_after_native_send_is_preserved_and_claim_stays_held(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind):
                sync,bridge,owner=self.base.setup_domain(kind);identity=sync._stored_links()[0]['alcor_id']
                owner.apple_apply_category(identity,{'name':'First local choice'});original=bridge.call;sent=[]
                def race(op,**payload):
                    result=original(op,**payload)
                    if op in ('rename','reminder_rename'):
                        sent.append(True);owner.apple_apply_category(identity,{'name':'Later local choice'})
                    return result
                with patch.object(bridge,'call',side_effect=race):
                    with self.assertRaisesRegex(ValueError,'CATEGORY_CHANGED:'):sync.resolve_category_conflict('a','keep_alcor',field='name')
                    sync.sync()
                self.assertEqual(owner.get_category(identity)['name'],'Later local choice');self.assertEqual(len(sent),1)
                self.assertTrue(any(row['group_id']=='a' and row['state']=='uncertain' for row in sync.status()['scan_claims']))

    def test_automatic_local_name_and_color_converge_after_restart_with_live_or_copied_inventory(self):
        R=M['M']['M']
        for kind in ('calendar','reminders'):
            for bridge_type in (R['Bridge'],M['M']['NativeBridge']):
                with self.subTest(kind=kind,bridge=bridge_type.__name__):
                    case=R['Recovery']();case.setUp()
                    try:
                        bridge=bridge_type(kind);owner=case.business.calendar if kind=='calendar' else case.business.tasks
                        cls=R['AppleCalendarSync'] if kind=='calendar' else R['AppleReminderSync']
                        port='calendar_port' if kind=='calendar' else 'todo_port';path=case.root/'category-claim-parity.sqlite3'
                        sync=cls(bridge=bridge,db_path=path,**{port:owner});sync.start_auto=lambda:None;sync.start_sync()
                        local=owner.create_category({'name':'Together','color':'#112233'});self.assertEqual(sync.sync()['status'],'success')
                        linked=next(row for row in sync._stored_links() if row['alcor_id']==local['id'])
                        sync=cls(bridge=bridge,db_path=path,**{port:owner});sync.start_auto=lambda:None
                        owner.update_category(local['id'],{'name':'Renamed together','color':'#334455'})
                        self.assertEqual(sync.sync()['status'],'success')
                        native=next(row for row in bridge.sources if row['id']==linked['apple_id'])
                        self.assertEqual(native['title'],'Renamed together');self.assertEqual(native['color'],'#334455')
                        self.assertEqual(owner.get_category(local['id'])['name'],'Renamed together')
                        self.assertEqual(owner.get_category(local['id'])['color'],'#334455')
                        self.assertEqual(sync.status()['scan_claims'],[])
                    finally:case.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)

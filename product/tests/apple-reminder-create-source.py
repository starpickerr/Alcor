"""Ordinary reminder creation: synthetic SQLite and conditional fake native only."""
import copy
import json
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))

class CreateSource(unittest.TestCase):
    def setup(self,s,o,b):
        s.start_sync();link=s._stored_links()[0]
        local=o.create_todo({'title':'Sensitive new local','category_id':link['alcor_id']})
        return local,link

    def bridge(self,b,*,before=None,after=None):
        original=b.call;calls=[]
        def call(op,**payload):
            if op!='reminder_upsert':return original(op,**payload)
            calls.append(copy.deepcopy(payload))
            if before:before(payload)
            result=original(op,**payload)
            return after(result) if after else result
        return call,calls

    def test_changed_source_at_dispatch_cannot_write_into_other_account(self):
        with M['case']('reminders') as (s,o,b):
            local,link=self.setup(s,o,b);before=copy.deepcopy(s._local(local['id']))
            call,sent=self.bridge(b,before=lambda p:b.sources[0].update(source_id='account2'))
            with patch.object(b,'call',side_effect=call):r=s.sync()
            self.assertEqual(sent[0].get('expected_source_id'),'account1')
            self.assertEqual(r['exported'],0);self.assertNotEqual(r['status'],'success')
            self.assertFalse(any(row['title']==local['title'] for row in b.rows));self.assertEqual(s._local(local['id']),before)

    def test_same_source_creates_once_with_bound_receipt_and_owner_baseline(self):
        with M['case']('reminders') as (s,o,b):
            local,link=self.setup(s,o,b);call,sent=self.bridge(b)
            with patch.object(b,'call',side_effect=call):
                self.assertEqual(s.sync()['exported'],1);s.sync();s.sync()
            self.assertEqual(len(sent),1);payload=sent[0]
            self.assertEqual(payload.get('expected_source_id'),'account1')
            self.assertEqual(payload.get('expected_destination_snapshot'),b.sources[0])
            self.assertTrue(payload.get('expected_absent'))
            self.assertEqual(json.loads(Path(payload['receipt_file']).read_text()),{'binding':payload['receipt_binding'],'saved':next(row for row in b.rows if row['title']==local['title'])})
            self.assertEqual(s._local(local['id'])['apple']['source_id'],'account1')
            self.assertEqual(len([row for row in s._maps() if row['todo_id']==local['id']]),1)
            self.assertFalse(s.status()['scan_claims'])

    def test_owner_or_category_edit_during_native_call_is_not_blessed(self):
        for changed in ('item','category','recreated'):
            with self.subTest(changed=changed),M['case']('reminders') as (s,o,b):
                local,link=self.setup(s,o,b)
                def edit(result):
                    if changed=='item':o.update_todo(local['id'],{'title':'Later user edit'})
                    elif changed=='category':o.update_category(link['alcor_id'],{'name':'Later category'})
                    else:
                        with o._connect() as db:
                            row=dict(db.execute('SELECT * FROM todos WHERE id=?',(local['id'],)).fetchone())
                            db.execute('DELETE FROM todos WHERE id=?',(local['id'],))
                            db.execute('INSERT INTO todos ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
                    return result
                call,sent=self.bridge(b,after=edit)
                with patch.object(b,'call',side_effect=call):r=s.sync();s.sync()
                self.assertEqual(r['exported'],0);self.assertEqual(len(sent),1)
                self.assertFalse(s._local(local['id']).get('apple'))
                self.assertFalse(any(row['todo_id']==local['id'] for row in s._maps()))
                self.assertTrue(s.status()['scan_claims'])
                if changed=='item':self.assertEqual(s._local(local['id'])['title'],'Later user edit')

    def test_untrusted_result_or_receipt_never_binds_or_retries(self):
        for fault in ('source','container','receipt','lost_reply'):
            with self.subTest(fault=fault),M['case']('reminders') as (s,o,b):
                local,link=self.setup(s,o,b)
                def alter(result):
                    if fault=='lost_reply':raise RuntimeError('Native reply lost')
                    if fault=='source':result['reminder']['source_id']='account2'
                    elif fault=='container':result['reminder']['list_id']='other'
                    elif fault=='receipt':
                        for p in (s.db_path.parent/'native-receipts').glob('*.json'):p.write_text('{"intent":true}')
                    return result
                call,sent=self.bridge(b,after=alter)
                with patch.object(b,'call',side_effect=call):s.sync();s.sync()
                self.assertFalse(s._local(local['id']).get('apple'))
                self.assertFalse(any(row['todo_id']==local['id'] for row in s._maps()))
                self.assertTrue(s.status()['scan_claims']);self.assertEqual(len(sent),1)
                restarted=type(s)(bridge=b,db_path=s.db_path,todo_port=o,clock=s.clock);restarted.start_auto=lambda:None
                with patch.object(b,'call',side_effect=call):restarted.sync()
                self.assertEqual(len(sent),1)

    def test_owner_change_before_dispatch_finishes_unsent_and_next_scan_uses_new_content(self):
        with M['case']('reminders') as (s,o,b):
            local,link=self.setup(s,o,b);original=o.apple_capture_approved_export;calls=[]
            def changed(identity):
                calls.append(identity)
                if len(calls)==2:o.update_todo(identity,{'title':'New user choice'})
                return original(identity)
            call,sent=self.bridge(b)
            with patch.object(o,'apple_capture_approved_export',side_effect=changed),patch.object(b,'call',side_effect=call):r=s.sync()
            self.assertEqual(r['exported'],0);self.assertFalse(sent);self.assertFalse(s.status()['scan_claims'])
            with patch.object(b,'call',side_effect=call):self.assertEqual(s.sync()['exported'],1)
            self.assertEqual(sent[0]['title'],'New user choice')

    def test_other_process_claim_prevents_ordinary_create(self):
        with M['case']('reminders') as (s,o,b):
            local,link=self.setup(s,o,b);before=copy.deepcopy(s._local(local['id']));call,sent=self.bridge(b)
            with M['M']['other_process_holds'](s,group='a',item=local['id']),patch.object(b,'call',side_effect=call):r=s.sync()
            self.assertEqual(r['exported'],0);self.assertFalse(sent);self.assertEqual(s._local(local['id']),before)

    def test_lost_owner_reply_keeps_native_receipt_and_never_sends_again(self):
        with M['case']('reminders') as (s,o,b):
            local,link=self.setup(s,o,b);original=o.apple_finalize_approved_export
            def lost(*args):
                original(*args);raise RuntimeError('Owner commit response lost')
            call,sent=self.bridge(b)
            with patch.object(b,'call',side_effect=call),patch.object(o,'apple_finalize_approved_export',side_effect=lost):r=s.sync()
            self.assertEqual(r['exported'],0);self.assertTrue(s._local(local['id']).get('apple'))
            self.assertFalse(any(row['todo_id']==local['id'] for row in s._maps()));self.assertTrue(s.status()['scan_claims'])
            with patch.object(b,'call',side_effect=call):s.sync();s.sync()
            self.assertEqual(len(sent),1);self.assertTrue(Path(sent[0]['receipt_file']).is_file())

    def test_manager_claim_change_during_owner_recheck_prevents_dispatch(self):
        with M['case']('reminders') as (s,o,b):
            local,link=self.setup(s,o,b);original=o.apple_capture_approved_export;captures=[]
            def changed(*args):
                captures.append(args);result=original(*args)
                if len(captures)==2:
                    with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                return result
            call,sent=self.bridge(b)
            with patch.object(o,'apple_capture_approved_export',side_effect=changed),patch.object(b,'call',side_effect=call):r=s.sync()
            self.assertEqual(r['exported'],0);self.assertFalse(sent);self.assertFalse(s._local(local['id']).get('apple'))
            self.assertTrue(s.status()['scan_claims'])

    def test_manager_claim_evidence_change_does_not_commit_mapping(self):
        with M['case']('reminders') as (s,o,b):
            local,link=self.setup(s,o,b);original=o.apple_finalize_approved_export
            def changed(*args):
                result=original(*args)
                with s._connect() as db:db.execute("UPDATE calendar_scan_attempts SET evidence='{}' WHERE state='scanning'")
                return result
            call,sent=self.bridge(b)
            with patch.object(b,'call',side_effect=call),patch.object(o,'apple_finalize_approved_export',side_effect=changed):r=s.sync()
            self.assertEqual(r['exported'],0);self.assertTrue(s._local(local['id']).get('apple'))
            self.assertFalse(any(row['todo_id']==local['id'] for row in s._maps()));self.assertTrue(s.status()['scan_claims'])

    def test_calendar_transient_destination_change_releases_only_unstarted_exact_claim(self):
        from apple_sync import group_scan_claims as claims
        for changed_evidence in (False,True):
            with self.subTest(changed_evidence=changed_evidence),M['case']('calendar') as (s,o,b):
                s.start_sync();identity=s._maps()[0]['alcor_id'];acquire=claims.acquire;call=b.call;armed=[]
                def held(*args,**kwargs):
                    token=acquire(*args,**kwargs);armed.append(token)
                    if changed_evidence:
                        with s._connect() as db:db.execute('UPDATE calendar_scan_attempts SET evidence=? WHERE attempt_id=?',('{"stage":"sending"}',token))
                    return token
                def inventory(op,**kw):
                    result=call(op,**kw)
                    if op=='calendars' and armed:
                        armed.clear();result=copy.deepcopy(result);result['calendars'][0]['source_id']='account2'
                    return result
                with patch.object(claims,'acquire',side_effect=held),patch.object(b,'call',side_effect=inventory):r=s.sync()
                self.assertEqual(bool(s.status()['scan_claims']),changed_evidence)
                b.rows[0]['title']='Healthy next round';s.sync()
                self.assertEqual(s._local(identity)['title']=='Healthy next round',not changed_evidence)

if __name__=='__main__':unittest.main(verbosity=2)

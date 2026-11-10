"""Native-origin occurrence range deletion over approved owner/manager actions."""
import copy,json,runpy,unittest
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
M=runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))
from business.apple_delete_approval import prepare as owner_prepare,commit as owner_commit
from apple_sync.approved_delete import prepare,run
CLAIMS=runpy.run_path(str(Path(__file__).with_name('apple-common-claims.py')))

@contextmanager
def case(all_sources=False,detached=False):
    c=M['Contracts']();c.setUp()
    try:
        b=M['NativeBridge']('calendar');template=copy.deepcopy(b.rows[0]);template['recurrence']={'frequency':'daily','interval':1}
        b.rows=[{**copy.deepcopy(template),'event_id':'series-'+str(day),'occurrence_date':f'2026-11-{day:02}T01:00:00Z','start_at':f'2026-11-{day:02}T01:00:00Z','end_at':f'2026-11-{day:02}T02:00:00Z'} for day in (7,8,9)]
        if detached:b.rows[1]['is_detached']=True
        b.sources.append({**b.sources[0],'id':'other','source_id':'account2','title':'Other source'})
        b.rows.append({**copy.deepcopy(b.rows[2]),'event_id':'foreign','calendar_id':'other','source_id':'account2'})
        s=c.calendar(b);s.clock=lambda:'2026-11-07T00:00:00Z';s.start_sync(None if all_sources else [{'apple_id':'a','alcor_id':''}]);s.sync();o=c.business.calendar
        original=b.call
        def native(op,**kw):
            if op=='status':return {**original(op,**kw),'delete_write_version':1}
            if op=='delete':
                expected=kw['expected_snapshot'];selected=[r for r in b.rows if r['event_id']==kw['event_id'] and r['calendar_id']==kw['calendar_id']]
                if selected!=[expected] or expected['source_id']!=kw['expected_source_id']:raise ValueError('native snapshot changed')
                if kw.get('series'):
                    b.calls.append((op,copy.deepcopy(kw)));target=datetime.fromisoformat(expected['occurrence_date'].replace('Z','+00:00'))
                    b.rows=[r for r in b.rows if not(r['source_id']==expected['source_id'] and r['calendar_id']==expected['calendar_id'] and r.get('external_id')==expected['external_id'] and datetime.fromisoformat(r['occurrence_date'].replace('Z','+00:00'))>=target)]
                    for r in b.rows:
                        if r['source_id']==expected['source_id'] and r['calendar_id']==expected['calendar_id'] and r.get('external_id')==expected['external_id']:r['recurrence']={**r['recurrence'],'until':'2026-11-07T15:59:59Z'}
                else:original(op,**kw)
                proof={'version':1,'binding':kw['receipt_binding'],'op':op,'source_id':kw['expected_source_id'],'expected_snapshot':expected,'series':kw.get('series',False)}
                result={'deleted':True,'delete_receipt':proof};Path(kw['receipt_file']).write_text(json.dumps({'binding':kw['receipt_binding'],**result}));return result
            return original(op,**kw)
        b.call=native;b.calls.clear();yield s,o,b
    finally:c.tearDown()

def target(o,day=8):return next(row for row in o.list_events() if row['start_at'].startswith(f'2026-11-{day:02}') and row['apple']['group_id']=='a')

def preview(s,o,scope,day=8):
    item=target(o,day);op='e'*64;args={'id':item['id'],'scope':scope,'occurrence_on':f'2026-11-{day:02}'}
    local=owner_prepare(o,'calendar','delete_event',args,op);plan=prepare(s,'calendar',op,local)
    return item,op,args,local,plan

class NativeRanges(unittest.TestCase):
    def test_this_and_future_remove_only_selected_local_counterparts(self):
        for scope,day in (('this',7),('this',8),('future',7),('future',8)):
            with self.subTest(scope=scope,day=day),case() as (s,o,b):
                item,op,args,local,plan=preview(s,o,scope,day);before=copy.deepcopy(o.list_events())
                selected=[r for r in before if r['apple']['group_id']=='a' and (r['id']==item['id'] if scope=='this' else r['start_at']>=item['start_at'])]
                self.assertEqual({r['id'] for r in local['targets']},{r['id'] for r in selected})
                owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
                result=run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw));self.assertEqual(result['state'],'done')
                self.assertEqual({r['id'] for r in o.list_events()},{r['id'] for r in before}-{r['id'] for r in selected})
                self.assertEqual(sum(op=='delete' for op,_ in b.calls),1)
                s.sync();s.sync();self.assertEqual(sum(op=='delete' for op,_ in b.calls),1)
                self.assertFalse({r['id'] for r in selected}&{r['id'] for r in o.list_events()})
                self.assertTrue(any(r['source_id']=='account2' for r in b.rows));self.assertFalse(s.status()['scan_claims'])
                self.assertEqual(run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw)),result)
    def test_changed_future_member_before_commit_or_send_preserves_all(self):
        for phase in ('preview','queued'):
            with self.subTest(phase=phase),case() as (s,o,b):
                item,op,args,local,plan=preview(s,o,'future');later=next(r for r in o.list_events() if r['start_at'].startswith('2026-11-09'))
                if phase=='queued':owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
                o.update_event(later['id'],{'title':'New local choice'})
                with self.assertRaises(ValueError):
                    if phase=='preview':owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
                    else:run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
                self.assertEqual(len(o.list_events()),3);self.assertEqual(o.get_event(later['id'])['title'],'New local choice')
                self.assertFalse(any(op=='delete' for op,_ in b.calls));self.assertFalse(s.status()['scan_claims'])
    def test_new_same_series_member_changes_frozen_range(self):
        with case() as (s,o,b):
            item,op,args,local,plan=preview(s,o,'future');new=copy.deepcopy(b.rows[2])
            new.update(event_id='series-10',occurrence_date='2026-11-10T01:00:00Z',start_at='2026-11-10T01:00:00Z',end_at='2026-11-10T02:00:00Z')
            b.rows.append(new);s.sync()
            with self.assertRaises(ValueError):owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
            self.assertEqual(len(o.list_events()),4);self.assertFalse(any(op=='delete' for op,_ in b.calls))
    def test_post_native_local_edit_keeps_range_and_never_resends(self):
        with case() as (s,o,b):
            item,op,args,local,plan=preview(s,o,'future');later=next(r for r in o.list_events() if r['start_at'].startswith('2026-11-09'))
            owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native']);original=b.call
            def changed(action,**kw):
                result=original(action,**kw)
                if action=='delete':o.update_event(later['id'],{'description':'Edit while Apple submits'})
                return result
            b.call=changed
            with self.assertRaises(ValueError):run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
            self.assertEqual(len(o.list_events()),3);self.assertEqual(o.get_event(later['id'])['description'],'Edit while Apple submits')
            self.assertTrue(s.status()['scan_claims'])
            with self.assertRaises(ValueError):run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
            self.assertEqual(sum(op=='delete' for op,_ in b.calls),1)
    def test_other_process_later_item_claim_prevents_dispatch(self):
        with case() as (s,o,b):
            item,op,args,local,plan=preview(s,o,'future');later=next(r for r in o.list_events() if r['start_at'].startswith('2026-11-09'))
            owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
            with CLAIMS['other_process_holds'](s,group='unrelated',item=later['id']):
                with self.assertRaises(ValueError):run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
            self.assertEqual(len(o.list_events()),3);self.assertFalse(any(op=='delete' for op,_ in b.calls))
    def test_exact_receipt_after_lost_response_finishes_all_once(self):
        with case() as (s,o,b):
            item,op,args,local,plan=preview(s,o,'future');owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native']);original=b.call
            def lost(action,**kw):
                result=original(action,**kw)
                if action=='delete':raise RuntimeError('response lost after persisted native receipt')
                return result
            b.call=lost
            with self.assertRaises(RuntimeError):run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
            self.assertEqual(len(o.list_events()),3);self.assertTrue(s.status()['scan_claims'])
            result=run(s,plan,lambda *a:False,lambda action,**kw:getattr(o,action)(**kw))
            self.assertEqual(result['state'],'done');self.assertEqual(len(o.list_events()),1)
            self.assertFalse(s.status()['scan_claims']);self.assertEqual(sum(op=='delete' for op,_ in b.calls),1)
    def test_archives_cover_each_deleted_member_and_restore_stays_local(self):
        with case() as (s,o,b):
            item,op,args,local,plan=preview(s,o,'future');owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
            run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
            recoveries=o.list_apple_inbound_recoveries();self.assertEqual({r['target_id'] for r in recoveries},{r['id'] for r in local['targets']})
            restored=[o.restore_apple_inbound_recovery(r['id'])['ids'][0] for r in recoveries];b.calls.clear();s.sync();s.sync()
            self.assertTrue(all(o.get_event(identity) for identity in restored));self.assertFalse(any(op in ('upsert','delete') for op,_ in b.calls))
    def test_ambiguous_native_range_is_not_guessed(self):
        for change in ('external','detached','duplicate'):
            with self.subTest(change=change),case() as (s,o,b):
                item=target(o)
                with o._connect() as db:
                    binding=o._apple_binding(db,'item',item['id'])
                    if change=='external':binding['native']['external_id']=''
                    elif change=='detached':binding['native']['is_detached']=True
                    else:
                        later=next(r for r in o.list_events() if r['start_at'].startswith('2026-11-09'))
                        binding2=o._apple_binding(db,'item',later['id']);binding2['native']['occurrence_date']=binding['native']['occurrence_date']
                        db.execute("UPDATE apple_bindings SET document=? WHERE entity='item' AND local_id=?",(json.dumps(binding2),later['id']))
                    db.execute("UPDATE apple_bindings SET document=? WHERE entity='item' AND local_id=?",(json.dumps(binding),item['id']))
                with self.assertRaises(ValueError):preview(s,o,'future')
                self.assertEqual(len(o.list_events()),3);self.assertFalse(any(op=='delete' for op,_ in b.calls))
    def test_unfinished_other_member_operation_blocks_the_range(self):
        with case() as (s,o,b):
            later=next(r for r in o.list_events() if r['start_at'].startswith('2026-11-09'))
            with o._connect() as db:
                db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)',('unknown-other','item',later['id'],'edit','{}','uncertain','synthetic unknown','2026-11-07'))
            with self.assertRaisesRegex(ValueError,'未知'):preview(s,o,'future')
            self.assertFalse(any(op=='delete' for op,_ in b.calls));self.assertEqual(len(o.list_events()),3)
    def test_recreated_member_with_identical_fields_cannot_inherit_approval(self):
        with case() as (s,o,b):
            item,op,args,local,plan=preview(s,o,'future');later=next(r for r in o.list_events() if r['start_at'].startswith('2026-11-09'))
            owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
            with o._connect() as db:
                old=dict(db.execute('SELECT * FROM calendar_events WHERE id=?',(later['id'],)).fetchone());binding=o._apple_binding(db,'item',later['id'])
            o.apple_remove_item(later['id'])
            with o._connect() as db:
                db.execute('INSERT INTO calendar_events ('+','.join(old)+') VALUES ('+','.join('?' for _ in old)+')',list(old.values()))
                db.execute('INSERT INTO apple_bindings VALUES (?,?,?)',('item',later['id'],json.dumps(binding)))
            with self.assertRaises(ValueError):run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))
            self.assertEqual(o.get_event(later['id'])['title'],later['title']);self.assertEqual(len(o.list_events()),3)
            self.assertFalse(any(op=='delete' for op,_ in b.calls))
    def test_range_receipt_does_not_bless_concurrent_earlier_content_edits(self):
        with case() as (s,o,b):
            item,op,args,local,plan=preview(s,o,'future');earlier=target(o,7)
            owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native']);original=b.call
            def changed(action,**kw):
                result=original(action,**kw)
                if action=='delete':
                    o.update_event(earlier['id'],{'title':'Earlier local choice'})
                    next(r for r in b.rows if r['event_id']=='series-7')['description']='Earlier Apple edit'
                return result
            b.call=changed
            self.assertEqual(run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))['state'],'done')
            s.sync();s.sync()
            self.assertEqual(o.get_event(earlier['id'])['title'],'Earlier local choice')
            self.assertEqual(next(r for r in b.rows if r['event_id']=='series-7')['description'],'Earlier Apple edit')
            self.assertEqual(next(r for r in s._maps() if r['alcor_id']==earlier['id'])['state'],'conflict')
            self.assertEqual(sum(op=='delete' for op,_ in b.calls),1);self.assertFalse(any(op=='upsert' for op,_ in b.calls))
    def test_exact_detached_instance_this_scope_still_works(self):
        with case(detached=True) as (s,o,b):
            item,op,args,local,plan=preview(s,o,'this')
            owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
            self.assertEqual(run(s,plan,lambda *a:True,lambda action,**kw:getattr(o,action)(**kw))['state'],'done')
            self.assertEqual(len(o.list_events()),2);self.assertNotIn(item['id'],{r['id'] for r in o.list_events()})
            s.sync();s.sync();self.assertEqual(sum(op=='delete' for op,_ in b.calls),1)

if __name__=='__main__':unittest.main(verbosity=2)

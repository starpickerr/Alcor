"""Approved exact recurring calendar scopes with fake EventKit, no real native store."""
import copy,json,runpy,unittest
from pathlib import Path
from datetime import datetime
M=runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))
from business.apple_delete_approval import prepare as owner_prepare,commit as owner_commit
from apple_sync.approved_delete import prepare,run

class RecurringDelete(unittest.TestCase):
    def test_approved_this_future_and_first_scope_keep_earlier_and_other_calendar(self):
        for scope,day,change in [('this','2026-11-08',None),('future','2026-11-08',None),('this','2026-11-07',None),('future','2026-11-07',None),('future','2026-11-08','local'),('future','2026-11-08','remote'),('this','2026-11-07','both')]:
            with self.subTest(scope=scope,day=day,change=change):
                c=M['Contracts']();c.setUp()
                try:
                    b,s,item=c.local_series();o=c.business.calendar;original=b.call
                    foreign=copy.deepcopy(b.rows[-1]);foreign['calendar_id']='other';foreign['event_id']='foreign';b.rows.append(foreign)
                    def native(op,**kw):
                        if op=='status':return {**original(op,**kw),'delete_write_version':1}
                        if op=='delete':
                            target=datetime.fromisoformat(kw['occurrence_date'].replace('Z','+00:00'));expected=kw['expected_snapshot']
                            if kw.get('series'):
                                b.calls.append((op,copy.deepcopy(kw)))
                                b.rows=[r for r in b.rows if not(r['calendar_id']==kw['calendar_id'] and r.get('external_id')==expected.get('external_id') and datetime.fromisoformat(r['occurrence_date'].replace('Z','+00:00'))>=target)]
                                for r in b.rows:
                                    if r['calendar_id']==kw['calendar_id'] and r.get('external_id')==expected.get('external_id'):
                                        r['recurrence']={**r['recurrence'],'until':'2026-11-07T15:59:59Z'}
                            else:original(op,**kw)
                            if change in ('remote','both'):
                                b.rows[0]['description']='Apple changed during delete'
                            proof={'version':1,'binding':kw['receipt_binding'],'op':op,'source_id':kw['expected_source_id'],'expected_snapshot':expected,'series':kw.get('series',False)}
                            result={'deleted':True,'delete_receipt':proof}
                            Path(kw['receipt_file']).write_text(json.dumps({'binding':kw['receipt_binding'],**result}));return result
                        return original(op,**kw)
                    b.call=native;op='c'*64;args={'id':item['id'],'scope':scope,'occurrence_on':day}
                    local=owner_prepare(o,'calendar','delete_event',args,op);plan=prepare(s,'calendar',op,local)
                    owner_commit(o,'calendar','delete_event',args,op,local['snapshot'],plan['native'])
                    def call(action,**kw):
                        value=getattr(o,action)(**kw)
                        if action=='apple_finish_approved_delete' and change in ('local','both'):
                            o.update_event(item['id'],{'title':'Local changed after delete'})
                        return value
                    result=run(s,plan,lambda *a:True,call)
                    self.assertEqual(result['state'],'done')
                    if change:
                        self.assertIn('reconciliation_required',result)
                        if change in ('local','both'):self.assertEqual(o.get_event(item['id'])['title'],'Local changed after delete')
                        if change in ('remote','both'):self.assertEqual(b.rows[0]['description'],'Apple changed during delete')
                        deletes=sum(action=='delete' for action,_ in b.calls)
                        self.assertEqual(run(s,plan,lambda *a:True,call),result)
                        s.sync();s.sync()
                        self.assertEqual(sum(action=='delete' for action,_ in b.calls),deletes)
                        self.assertEqual(s.status()['scan_claims'],[])
                        self.assertEqual(s._maps()[0]['state'],'conflict')
                        continue
                    self.assertTrue(any(r==foreign for r in b.rows))
                    self.assertEqual(o.list_events(day+'T00:00:00Z',day+'T23:59:59Z'),[])
                    if day=='2026-11-08':self.assertTrue(o.list_events('2026-11-07T00:00:00Z','2026-11-07T23:59:59Z'))
                    deletes=sum(action=='delete' for action,_ in b.calls)
                    first=s.sync();second=s.sync()
                    self.assertEqual(sum(action=='delete' for action,_ in b.calls),deletes)
                    self.assertEqual(first['status'],'success',(first,s.status()['conflicts']));self.assertEqual(second['status'],'success',second)
                finally:c.tearDown()

if __name__=='__main__':unittest.main(verbosity=2)

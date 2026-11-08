"""Explicit existing selections retain missing containers without guessing identity."""
import copy,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-import-unlink-run.py')))

class MissingSelection(unittest.TestCase):
 def test_existing_missing_id_keeps_local_changes_and_healthy_group_syncs(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   for deleted in (True,False):
    for local_change in ('edit','new'):
     with self.subTest(kind=kind,deleted=deleted,change=local_change),M['M']['case'](kind) as (s,o,b):
      b.sources.append({**b.sources[0],'id':'other','title':'Healthy'})
      remote=copy.deepcopy(b.rows[0]);remote['calendar_id' if kind=='calendar' else 'list_id']='other';remote['event_id' if kind=='calendar' else 'reminder_id']='healthy';remote['external_id']='healthy-external';b.rows.append(remote)
      s.start_sync();link=next(r for r in s._stored_links() if r['apple_id']=='a');s._set('sync_deletions',deleted)
      selected=[{k:r[k] for k in ('apple_id','alcor_id')} for r in s._stored_links()];links=copy.deepcopy(s._stored_links());s.disconnect()
      item=next(r for r in s._maps() if r['calendar_id' if kind=='calendar' else 'list_id']=='a')['alcor_id' if kind=='calendar' else 'todo_id']
      if local_change=='edit':(o.update_event if kind=='calendar' else o.update_todo)(item,{'title':'Local offline choice'})
      else:
       data={'title':'New unsynced offline','category_id':link['alcor_id']}
       row=o.create_event({**data,'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'}) if kind=='calendar' else o.create_todo(data);item=row['id']
      local=copy.deepcopy(s._local(item));maps=copy.deepcopy(s._maps());b.sources=[r for r in b.sources if r['id']!='a'];b.rows=[b.rows[1]];b.rows[0]['title']='Healthy after offline';b.calls.clear()
      s=type(s)(bridge=b,db_path=s.db_path,**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}),clock=s.clock);s.start_auto=lambda:None
      result=s.start_sync(selected);s.sync()
      self.assertTrue(result['state']['connected']);self.assertEqual(result['result']['status'],'partial');self.assertEqual(s._get('sync_deletions'),deleted)
      self.assertEqual(s._local(item),local);self.assertEqual(len(s._maps()),len(maps));self.assertFalse(s.status()['scan_claims'])
      self.assertEqual([{k:r[k] for k in ('apple_id','alcor_id','direction','enabled')} for r in s._stored_links()],[{k:r[k] for k in ('apple_id','alcor_id','direction','enabled')} for r in links])
      self.assertIn('a',result['result']['failed_calendar_ids' if kind=='calendar' else 'failed_list_ids'])
      self.assertTrue(any(r['title']=='Healthy after offline' for r in (o.list_events() if kind=='calendar' else o.list_todos())))
      self.assertFalse(helper.writes(b));self.assertFalse(o.list_apple_inbound_recoveries())
 def test_unavailable_new_or_reassigned_identity_is_not_treated_as_old_selection(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   for change in ('unknown_id','different_local','disabled'):
    with self.subTest(kind=kind,change=change),M['M']['case'](kind) as (s,o,b):
     _,_,link=helper.setup(s,o,b,kind);other=o.create_category({'name':'Other local'})
     if change=='disabled':
      with s._connect() as db:db.execute("UPDATE category_links SET enabled=0 WHERE apple_id='a'")
     s.disconnect();b.sources=[r for r in b.sources if r['id']!='a'];before=copy.deepcopy(s._stored_links());maps=copy.deepcopy(s._maps());b.calls.clear()
     choice={'apple_id':'unknown' if change=='unknown_id' else 'a','alcor_id':other['id'] if change=='different_local' else link['alcor_id']}
     with self.assertRaises(ValueError):s.start_sync([choice])
     self.assertEqual(s._stored_links(),before);self.assertEqual(s._maps(),maps);self.assertFalse(s._get('connected'));self.assertFalse(helper.writes(b))

 def test_readonly_missing_container_is_retained_without_adopting_same_name(self):
  helper=M['ImportUnlink']()
  for kind in ('calendar','reminders'):
   with self.subTest(kind=kind),M['M']['case'](kind) as (s,o,b):
    _,_,link=helper.setup(s,o,b,kind);selected=[{k:r[k] for k in ('apple_id','alcor_id')} for r in s._stored_links()]
    old=copy.deepcopy(b.sources[0]);remote=copy.deepcopy(b.rows[0]);s.disconnect()
    b.sources=[b.sources[1],{**old,'id':'same-name-new-id'}];remote['calendar_id' if kind=='calendar' else 'list_id']='same-name-new-id';b.rows=[b.rows[1],remote];b.calls.clear()
    result=s.start_sync(selected);s.sync()
    self.assertEqual(result['result']['status'],'partial');self.assertEqual(len(s._maps()),2);self.assertEqual(len(o.categories()),2)
    self.assertEqual(next(r for r in s._stored_links() if r['apple_id']=='a')['direction'],'import')
    self.assertFalse(any(r['apple_id']=='same-name-new-id' for r in s._stored_links()));self.assertFalse(helper.writes(b))
    b.sources=[r for r in b.sources if r['id']!='same-name-new-id']+[old];remote['calendar_id' if kind=='calendar' else 'list_id']='a';remote['title']='Exact original returns'
    self.assertEqual(s.sync()['status'],'success');self.assertTrue(any(r['title']=='Exact original returns' for r in (o.list_events() if kind=='calendar' else o.list_todos())))
    self.assertFalse(helper.writes(b))

if __name__=='__main__':unittest.main(verbosity=2)

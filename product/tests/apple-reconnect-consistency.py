"""Pause/restart/reconnect using real managers, synthetic owners and fake EventKit."""
import copy,json,runpy,unittest
from pathlib import Path
M=runpy.run_path(str(Path(__file__).with_name('apple-category-connection-cas.py')))

class ReconnectConsistency(unittest.TestCase):
    def setup(self,kind,s,o,b):
        second=copy.deepcopy(b.rows[0]);second.update(title='Second original',external_id='second-external')
        second['event_id' if kind=='calendar' else 'reminder_id']='second-native';b.rows.append(second)
        s.start_sync();key='alcor_id' if kind=='calendar' else 'todo_id'
        return key,{row['event_id' if kind=='calendar' else 'reminder_id']:row[key] for row in s._maps()}
    def reconnect(self,s,o,b,kind,restart=False):
        if restart:
            s=type(s)(bridge=b,db_path=s.db_path,**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}),clock=s.clock)
            s.start_auto=lambda:None
        links=[{k:r[k] for k in ('apple_id','alcor_id')} for r in s._stored_links()]
        before_deletions=s._get('sync_deletions');s.start_sync(links)
        self.assertEqual(s._get('sync_deletions'),before_deletions);return s
    def test_both_sides_add_and_independent_edits_converge_after_pause_or_restart(self):
        for kind in ('calendar','reminders'):
            for restart in (False,True):
                with self.subTest(kind=kind,restart=restart),M['case'](kind) as (s,o,b):
                    key,ids=self.setup(kind,s,o,b);first=next(v for k,v in ids.items() if k!='second-native')
                    links=copy.deepcopy(s._stored_links());s.disconnect();maps=copy.deepcopy(s._maps());b.calls.clear()
                    b.rows[0]['title']='Remote changed offline'
                    (o.update_event if kind=='calendar' else o.update_todo)(ids['second-native'],{'title':'Local changed offline'})
                    added=copy.deepcopy(b.rows[0]);added.update(title='Remote new offline',external_id='new-external')
                    added['event_id' if kind=='calendar' else 'reminder_id']='new-native';b.rows.append(added)
                    data={'title':'Local new offline','category_id':links[0]['alcor_id']}
                    if kind=='calendar':new=o.create_event({**data,'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'})
                    else:new=o.create_todo({**data,'due_on':'2026-11-09'})
                    s.sync();self.assertEqual(s._maps(),maps);self.assertFalse(b.calls)
                    s=self.reconnect(s,o,b,kind,restart);s.sync()
                    self.assertEqual(s._local(first)['title'],'Remote changed offline')
                    self.assertEqual(next(r['title'] for r in b.rows if r.get('event_id',r.get('reminder_id'))=='second-native'),'Local changed offline')
                    self.assertEqual(len(s._maps()),4);self.assertEqual(len(b.rows),4)
                    self.assertTrue(s._local(new['id']).get('apple'));self.assertEqual(s.status()['scan_claims'],[])
    def test_same_item_changed_on_both_sides_keeps_conflict_across_reconnect(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                key,ids=self.setup(kind,s,o,b);identity=next(v for k,v in ids.items() if k!='second-native');s.disconnect()
                (o.update_event if kind=='calendar' else o.update_todo)(identity,{'title':'Local choice'})
                b.rows[0]['title']='Remote choice';b.calls.clear()
                s=self.reconnect(s,o,b,kind,True);s.sync();s.disconnect();s=self.reconnect(s,o,b,kind)
                self.assertEqual(s._local(identity)['title'],'Local choice');self.assertEqual(b.rows[0]['title'],'Remote choice')
                self.assertEqual(next(r['state'] for r in s._maps() if r[key]==identity),'conflict')
                self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in b.calls))
    def test_historical_delete_intent_and_unknown_are_not_replayed_by_reconnect(self):
        for kind in ('calendar','reminders'):
            for state in ('pending','sending','uncertain'):
                with self.subTest(kind=kind,state=state),M['case'](kind) as (s,o,b):
                    key,ids=self.setup(kind,s,o,b);identity=ids['second-native'];document={'legacy':'preserved deletion intent'}
                    with o._connect() as db:db.execute('INSERT INTO apple_operations VALUES (?,?,?,?,?,?,?,?)',('legacy-delete','item',identity,'delete',json.dumps(document),state,'existing evidence','2026-11-07'))
                    original=copy.deepcopy(o.apple_operations());s.disconnect();b.calls.clear()
                    s=self.reconnect(s,o,b,kind,True);s.sync()
                    self.assertEqual(o.apple_operations(),original);self.assertIsNotNone(s._local(identity))
                    self.assertTrue(any(r[key]==identity for r in s._maps()))
                    self.assertFalse(any(op in ('delete','delete_calendar','reminder_delete','reminder_list_delete') for op,_ in b.calls))
    def test_remote_item_absence_keeps_calendar_pending_and_archives_verified_reminder(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                key,ids=self.setup(kind,s,o,b);identity=ids['second-native'];s.disconnect();b.rows=[b.rows[0]];b.calls.clear()
                s=self.reconnect(s,o,b,kind);s.clock=lambda:'2026-11-07T00:02:00Z';s.sync()
                if kind=='calendar':
                    self.assertIsNotNone(s._local(identity));self.assertEqual(next(r['state'] for r in s._maps() if r[key]==identity),'conflict')
                else:
                    self.assertIsNone(s._local(identity));self.assertTrue(o.list_apple_inbound_recoveries())
                self.assertFalse(any(op in ('delete','delete_calendar','reminder_delete','reminder_list_delete') for op,_ in b.calls))

    def test_apple_category_deleted_offline_keeps_unsynced_local_changes(self):
        for kind in ('calendar','reminders'):
            for change in ('new_member','edited_member'):
                with self.subTest(kind=kind,change=change),M['case'](kind) as (s,o,b):
                    other=copy.deepcopy(b.sources[0]);other.update(id='healthy',title='Healthy');b.sources.append(other)
                    key,ids=self.setup(kind,s,o,b);link=next(row for row in s._stored_links() if row['apple_id']=='a')
                    identity=next(iter(ids.values()));s.disconnect()
                    if change=='edited_member':
                        (o.update_event if kind=='calendar' else o.update_todo)(identity,{'title':'Local edit while disconnected'})
                    else:
                        data={'title':'New unsynced member','category_id':link['alcor_id']}
                        item=o.create_event({**data,'start_at':'2026-11-09T01:00:00Z','end_at':'2026-11-09T02:00:00Z'}) if kind=='calendar' else o.create_todo(data)
                        identity=item['id']
                    before=copy.deepcopy(s._local(identity));b.sources=[row for row in b.sources if row['id']!='a'];b.rows=[];b.calls.clear()
                    s=type(s)(bridge=b,db_path=s.db_path,**({'calendar_port':o} if kind=='calendar' else {'todo_port':o}),clock=s.clock)
                    s.start_auto=lambda:None
                    s.start_sync()  # The existing page reconnect calls start_sync without selections.
                    s.clock=lambda:'2026-11-07T00:02:00Z';s.sync();s.sync()
                    self.assertEqual(s._local(identity),before)
                    self.assertTrue(any(row['id']==link['alcor_id'] for row in o.categories()))
                    self.assertTrue(s.status()['category_conflicts']);self.assertFalse(o.list_apple_inbound_recoveries())
                    self.assertFalse(any(op in ('delete','delete_calendar','reminder_delete','reminder_list_delete','upsert','reminder_upsert') for op,_ in b.calls))

    def test_existing_explicit_reconnect_preserves_true_and_false_delete_setting(self):
        for kind in ('calendar','reminders'):
            for enabled in (True,False):
                with self.subTest(kind=kind,enabled=enabled),M['case'](kind) as (s,o,b):
                    category=o.create_category({'name':'Selected'})
                    s.start_sync([{'apple_id':'a','alcor_id':category['id']}])
                    s._set('sync_deletions',enabled)
                    keys=['sync_deletions','sync_all','auto_enabled','direction','calendar_ids' if kind=='calendar' else 'list_ids']
                    config={k:s._get(k) for k in keys};links=copy.deepcopy(s._stored_links());maps=copy.deepcopy(s._maps())
                    s.disconnect();s=self.reconnect(s,o,b,kind,True)
                    self.assertEqual({k:s._get(k) for k in keys},config)
                    fields=lambda rows:[{k:v for k,v in row.items() if k!='updated_at'} for row in rows]
                    self.assertEqual(fields(s._stored_links()),fields(links));self.assertEqual(fields(s._maps()),fields(maps))

    def test_existing_direction_is_preserved_including_readonly_import(self):
        for kind in ('calendar','reminders'):
            for direction,writable in (('import',True),('import',False),('export',True),('bidirectional',True)):
                with self.subTest(kind=kind,direction=direction,writable=writable),M['case'](kind) as (s,o,b):
                    b.sources[0]['writable']=writable
                    b.rows[0]['writable']=writable
                    category=o.create_category({'name':'Selected'})
                    s.replace_links([{'apple_id':'a','alcor_id':category['id'],'direction':direction,'enabled':True}]);s.sync()
                    mappings=copy.deepcopy(s._maps());s._set('sync_deletions',False);s.disconnect();b.calls.clear()
                    s=self.reconnect(s,o,b,kind,True)
                    self.assertEqual(s._stored_links()[0]['direction'],direction)
                    self.assertEqual(s._get('direction'),direction)
                    self.assertEqual([(r.get('alcor_id',r.get('todo_id')),r.get('event_id',r.get('reminder_id'))) for r in s._maps()],[(r.get('alcor_id',r.get('todo_id')),r.get('event_id',r.get('reminder_id'))) for r in mappings])
                    if direction=='import':
                        self.assertFalse(any(op in ('upsert','reminder_upsert','delete','reminder_delete','delete_calendar','reminder_list_delete') for op,_ in b.calls))
                        b.rows[0]['title']='Imported after reconnect';s.sync()
                        self.assertEqual(s._local(s._maps()[0].get('alcor_id',s._maps()[0].get('todo_id')))['title'],'Imported after reconnect')
    def test_new_explicit_selection_keeps_existing_default(self):
        for kind in ('calendar','reminders'):
            with self.subTest(kind=kind),M['case'](kind) as (s,o,b):
                first=o.create_category({'name':'First'})
                s.start_sync([{'apple_id':'a','alcor_id':first['id']}]);self.assertFalse(s._get('sync_deletions'))
                s._set('sync_deletions',True)
                second=o.create_category({'name':'Second'})
                b.sources.append({**b.sources[0],'id':'b','title':'Other'})
                s.start_sync([{'apple_id':'b','alcor_id':second['id']}]);self.assertFalse(s._get('sync_deletions'))

    def test_default_reconnect_preserves_existing_direction_and_delete_choice(self):
        for kind in ('calendar','reminders'):
            for enabled in (True,False):
                with self.subTest(kind=kind,enabled=enabled),M['case'](kind) as (s,o,b):
                    s.start_sync();link=s._stored_links()[0]
                    s.replace_links([{**link,'direction':'import','enabled':True}]);s._set('sync_deletions',enabled)
                    s.disconnect();s.start_sync();s.sync()
                    self.assertEqual(s._stored_links()[0]['direction'],'import');self.assertEqual(s._get('sync_deletions'),enabled)
                    b.rows[0]['title']='Only Apple imports';s.sync()
                    self.assertEqual(s._local(s._maps()[0].get('alcor_id',s._maps()[0].get('todo_id')))['title'],'Only Apple imports')

if __name__=='__main__':unittest.main(verbosity=2)

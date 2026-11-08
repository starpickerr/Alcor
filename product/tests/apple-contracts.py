"""Apple compatibility contracts, using temporary stores and synthetic EventKit only."""
import copy
from datetime import datetime,timedelta,timezone
import json
import runpy
import tempfile
import unittest
from pathlib import Path
M = runpy.run_path(str(Path(__file__).with_name('business-recovery.py')))
Business, Bridge = M['Business'], M['Bridge']
from apple_sync.calendar import AppleCalendarSync
from apple_sync.reminders import AppleReminderSync


class NativeBridge(Bridge):
    def __init__(self,kind):
        super().__init__(kind)
        self.sources[0].update(source_id='account1',source='iCloud',immutable=False)
        for row in self.rows: row['source_id'] = 'account1'
        self.calls = []
        self.offline = False
        self.sequence = 0

    def call(self,op,**payload):
        self.calls.append((op,copy.deepcopy(payload)))
        reminder = self.kind=='reminders'
        group,identity = ('list_id','reminder_id') if reminder else ('calendar_id','event_id')
        singular = 'reminder' if reminder else 'event'
        if op in ('status','reminder_status'):
            return {**super().call(op, **payload), 'delete_write_version':1}
        if self.offline and op not in ('status','reminder_status','calendars','reminder_lists'):
            raise RuntimeError('Synthetic offline')
        if op in ('calendars','reminder_lists'):
            return {'lists' if reminder else 'calendars':copy.deepcopy(self.sources),'sources':[{'id':'account1','title':'iCloud'},{'id':'account2','title':'Work'}],
                    'default_id':'a','default_source_id':'account1','source_ids':['account1','account2']}
        if op in ('create_calendar','reminder_list_create'):
            self.sequence += 1
            row = dict(id='created-'+str(self.sequence),title=payload['title'],color=payload['color'],source_id=payload.get('source_id','account1'),source='iCloud' if payload.get('source_id','account1')=='account1' else 'Work',writable=True,immutable=False)
            self.sources.append(row)
            if payload.get('receipt_binding'):
                Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'],'saved':row}))
            return {'list' if reminder else 'calendar':copy.deepcopy(row)}
        if op in ('events','reminders'):
            rows = [row for row in self.rows if row[group] in payload['list_ids' if reminder else 'calendar_ids']]
            if not reminder:
                lower,upper = (datetime.fromisoformat(payload[key].replace('Z','+00:00')) for key in ('start','end'))
                rows = [row for row in rows if lower<=datetime.fromisoformat(row['start_at'].replace('Z','+00:00'))<upper]
            return {singular+'s':copy.deepcopy(rows),'successful_list_ids' if reminder else 'successful_calendar_ids':[row['id'] for row in self.sources], 'failed_list_ids' if reminder else 'failed_calendar_ids':[]}
        if op in ('lookup','reminder_lookup','upsert','reminder_upsert','delete','reminder_delete','reminder_complete'):
            occurrence = payload.get('occurrence_date')
            def same(row):
                return row[group]==payload.get('lookup_'+group,payload[group]) and (row[identity]==payload.get(identity) or payload.get('external_id') and row.get('external_id')==payload['external_id']) and (reminder or not occurrence or datetime.fromisoformat(row['occurrence_date'].replace('Z','+00:00'))==datetime.fromisoformat(occurrence.replace('Z','+00:00')))
            matches = [row for row in self.rows if same(row)]
            row = matches[0] if len(matches)==1 else None
            if op in ('lookup','reminder_lookup'):
                return {singular:copy.deepcopy(row)}
            if op in ('delete','reminder_delete'):
                if not row and payload.get('require_match'):
                    raise ValueError('Native occurrence unavailable')
                if row and payload.get('require_exact_identity') and row[identity] != payload.get(identity):
                    raise ValueError('Native identity changed')
                if row and payload.get('expected_source_id') and row.get('source_id') != payload['expected_source_id']:
                    raise ValueError('Native source changed')
                if row and payload.get('expected_snapshot') and row != payload['expected_snapshot']:
                    raise ValueError('Native content changed')
                if row:
                    self.rows.remove(row)
                return {'deleted':True}
            if op=='reminder_complete':
                if not row or payload.get('occurrence_on')!=row.get('due_on'):
                    raise ValueError('Only current instance is completable')
                old = copy.deepcopy(row)
                row.update(completed=payload['completed'],completed_at='2026-11-07T03:00:00Z')
                if payload['completed'] and (row.get('recurrence') or {}).get('frequency','none')!='none':
                    following = copy.deepcopy(row)
                    following.update(reminder_id=row['reminder_id']+'-next',due_on=(datetime.fromisoformat(row['due_on'])+timedelta(days=1)).date().isoformat(),completed=False,completed_at=None)
                    self.rows.append(following)
                return {'reminder':copy.deepcopy(row)}
            if op=='reminder_upsert':
                target=next(source for source in self.sources if source['id']==payload[group])
                if payload.get('expected_source_id') and payload['expected_source_id']!=target.get('source_id'):
                    raise ValueError('Reminder destination source changed; write was not applied')
                if payload.get('expected_destination_snapshot') and payload['expected_destination_snapshot']!=target:
                    raise ValueError('Reminder destination changed; write was not applied')
                if payload.get('expected_absent') and row is not None:
                    raise ValueError('Reminder already exists; write was not applied')
                if payload.get('receipt_binding'):
                    receipt=Path(payload['receipt_file'])
                    if receipt.exists():raise ValueError('Reminder write already has a receipt; review before retrying')
                    receipt.write_text(json.dumps({'binding':payload['receipt_binding'],'intent':True}))
            if row is None:
                self.sequence += 1
                row = copy.deepcopy(Bridge(self.kind).rows[0])
                row.update({identity:'item-'+str(self.sequence),'external_id':'external-'+str(self.sequence),group:payload[group]})
                row['alcor_id']=''
                self.rows.append(row)
            row[group]=payload[group]
            row['source_id']=next(source['source_id'] for source in self.sources if source['id']==row[group])
            for key in ('title','notes','description','location','start_at','end_at','all_day','timezone','due_on','due_time','planned_on','remind_at','priority','completed','completed_at','url','alarms','floating'):
                if key in payload:
                    row[key]=payload[key]
            if not reminder:
                row['timezone']=payload.get('repeat_timezone',row['timezone'])
                row['occurrence_date']=row.get('occurrence_date') if matches else payload.get('occurrence_date',row['start_at'])
                if payload.get('series') is False and matches and row.get('recurrence',{}).get('frequency','none')!='none':
                    row['is_detached']=True
            if not matches or payload.get('set_recurrence'):
                row['recurrence']=payload.get('native_recurrence',{'frequency':payload.get('repeat_frequency','none'),'interval':payload.get('repeat_interval',1),'days':payload.get('repeat_days',[]),'until':payload.get('repeat_until')})
            if op=='reminder_upsert' and payload.get('receipt_binding'):
                Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'],'saved':row}))
            return {singular:copy.deepcopy(row)}
        if op in ('delete_calendar','reminder_list_delete'):
            target = next((row for row in self.sources if row['id'] == payload[group]), None)
            if payload.get('require_match') and not target:
                raise ValueError('Native category unavailable')
            if target and payload.get('expected_source_id') and target.get('source_id') != payload['expected_source_id']:
                raise ValueError('Native category source changed')
            if target and payload.get('expected_snapshot') and target != payload['expected_snapshot']:
                raise ValueError('Native category content changed')
            self.rows=[row for row in self.rows if row[group]!=payload[group]]
        return super().call(op,**payload)


class Contracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='stella-apple-contracts-')
        self.root = Path(self.temporary.name)
        self.business = Business(self.root/'business')

    def tearDown(self):
        self.business.meta.close()
        self.temporary.cleanup()

    def calendar(self, bridge=None):
        sync = AppleCalendarSync(bridge=bridge or Bridge('calendar'), db_path=self.root/'calendar-sync.sqlite3', calendar_port=self.business.calendar)
        sync.start_auto = lambda: None
        return sync

    def test_delete_keeps_local_data_until_apple_acknowledges(self):
        bridge = NativeBridge('calendar')
        sync = self.calendar(bridge)
        group = sync.start_sync()['state']['links'][0]
        row = self.business.calendar.list_events()[0]
        with self.assertRaisesRegex(PermissionError, '人工确认'):
            self.business.calendar.delete_category(group['alcor_id'])
        self.assertEqual(sync.sync()['deleted_remote'], 0)
        self.assertEqual(self.business.calendar.get_event(row['id'])['title'], row['title'])
        self.assertFalse(any(op == 'delete_calendar' for op, _ in bridge.calls))

    def test_content_writable_does_not_allow_immutable_category_changes(self):
        bridge = Bridge('calendar')
        bridge.sources[0].update(writable=True, immutable=True)
        sync = self.calendar(bridge)
        state = sync.start_sync()['state']
        group = next(row for row in state['links'] if row['apple_id']=='a')
        with self.assertRaises(PermissionError):
            self.business.calendar.delete_category(group['alcor_id'])
        with self.assertRaises(PermissionError):
            self.business.calendar.update_category(group['alcor_id'], {'name':'Disallowed'})
        event = self.business.calendar.list_events()[0]
        self.business.calendar.update_event(event['id'], {'title':'Allowed local content edit'})

    def test_cancel_pending_delete_preserves_category_and_records(self):
        bridge = NativeBridge('calendar')
        sync = self.calendar(bridge)
        group = sync.start_sync()['state']['links'][0]
        owner = self.business.calendar
        from business.apple_delete_approval import prepare, commit
        from apple_sync.approved_delete import prepare as prepare_native
        args = {'id': group['alcor_id']}
        operation = 'c' * 64
        plan = prepare(owner, 'calendar', 'delete_category', args, operation)
        native = prepare_native(sync, 'calendar', operation, plan)
        commit(owner, 'calendar', 'delete_category', args, operation, plan['snapshot'], native['native'])
        owner.cancel_apple_operation(operation)
        self.assertFalse(owner.apple_operations())
        sync.sync()
        self.assertTrue(owner.get_category(group['alcor_id']))
        self.assertEqual(len(owner.list_events()), 1)
        self.assertFalse(any(op == 'delete_calendar' for op, _ in bridge.calls))

    def reminder(self,bridge=None):
        sync=AppleReminderSync(bridge=bridge or NativeBridge('reminders'),db_path=self.root/'reminders-sync.sqlite3',todo_port=self.business.tasks,clock=lambda:'2026-11-07T00:00:00Z')
        sync.start_auto=lambda:None
        return sync

    def test_identity_reset_rebinds_without_changing_local_ids_or_losing_notes(self):
        bridge=NativeBridge('calendar');sync=self.calendar(bridge)
        sync.start_sync();before=sync._maps()[0];event=self.business.calendar.get_event(before['alcor_id'])
        self.business.calendar.update_event(event['id'],{'description':'Keep private local content'})
        bridge.sources[0]['id']='reset-calendar';bridge.rows[0].update(calendar_id='reset-calendar',event_id='reset-event')
        result=sync.sync()
        self.assertEqual(result['status'],'success',sync.status())
        self.assertEqual(sync._maps()[0]['alcor_id'],event['id'])
        self.assertEqual(self.business.calendar.get_event(event['id'])['description'],'Keep private local content')
        self.assertEqual(len(self.business.calendar.categories()),1)
        self.assertEqual(sync._stored_links()[0]['apple_id'],'reset-calendar')

    def test_disappearing_source_retains_data_until_explicit_resolution(self):
        bridge=NativeBridge('calendar');sync=self.calendar(bridge);sync.start_sync()
        event=self.business.calendar.list_events()[0];group=event['category_id'];bridge.sources=[];bridge.rows=[]
        self.assertEqual(sync.sync()['status'],'partial')
        self.assertTrue(self.business.calendar.get_event(event['id']))
        self.assertTrue(sync.status()['category_conflicts'])
        sync.resolve_category_conflict('a','keep_apple')
        with self.assertRaises(KeyError):self.business.calendar.get_category(group)

    def test_same_name_different_accounts_and_explicit_creation_account(self):
        bridge=NativeBridge('calendar');bridge.sources.append({**bridge.sources[0],'id':'b','source_id':'account2','source':'Work'})
        sync=self.calendar(bridge);self.assertEqual(sync.start_sync()['result']['status'],'success')
        groups=self.business.calendar.categories();self.assertEqual(len(groups),2)
        self.assertEqual({row['display_name'] for row in groups},{'Synthetic'})
        self.assertEqual({row['apple']['source_id'] for row in groups},{'account1','account2'})
        self.assertEqual(sync.sync()['status'],'success')
        self.assertEqual([row['title'] for row in bridge.sources],['Synthetic','Synthetic'])
        local=self.business.calendar.create_category({'name':'planA','source_id':'account2'})
        self.assertEqual(sync.sync()['status'],'success')
        remote=next(row for row in bridge.sources if row['title']=='planA');self.assertEqual(remote['source_id'],'account2')
        self.assertTrue(self.business.calendar.get_category(local['id']))

    def test_complex_reminder_is_visible_readonly_and_partial(self):
        bridge=NativeBridge('reminders');bridge.rows[0].update(alarm_count=1,remind_at=None,alarms=[{'offset':-900}],recurrence={'frequency':'monthly','month_days':[1,15],'interval':1,'count':12})
        sync=self.reminder(bridge);result=sync.start_sync()['result']
        self.assertEqual(result['imported'],1);self.assertEqual(result['status'],'partial')
        item=self.business.tasks.list_todos()[0]
        self.assertEqual(item['native_recurrence']['count'],12)
        self.assertTrue(sync.status()['readonly_issues'])
        self.assertIsNone(sync.status()['last_success'])
        with self.assertRaises(PermissionError):self.business.tasks.update_todo(item['id'],{'title':'Invalid'})
        with self.assertRaisesRegex(PermissionError, '人工确认'): self.business.tasks.delete_todo(item['id'])
        self.assertTrue(self.business.tasks.get_todo(item['id']))

    def test_adopting_apple_for_deleted_local_conflict_restores_locally_once(self):
        for kind in ('calendar','reminders'):
            bridge=NativeBridge(kind);sync=self.calendar(bridge) if kind=='calendar' else self.reminder(bridge)
            owner=self.business.calendar if kind=='calendar' else self.business.tasks
            sync.start_sync();item=(owner.list_events() if kind=='calendar' else owner.list_todos())[0]
            owner.apple_remove_item(item['id']);bridge.rows[0]['title']='Apple changed while local was deleted'
            sync.sync();self.assertTrue(sync.status()['conflicts'])
            before=sync._maps();native=[dict(row) for row in bridge.rows];bridge.calls.clear()
            sync.resolve_conflict(item['id'],'keep_apple')
            self.assertEqual(sync._local(item['id'])['title'],'Apple changed while local was deleted')
            self.assertEqual(len(sync._maps()),len(before));self.assertEqual(sync._maps()[0]['state'],'active');self.assertEqual(bridge.rows,native)
            sync.sync();sync.sync();self.assertEqual(len(sync._maps()),len(before))
            self.assertFalse(any(op in ('upsert','reminder_upsert') for op,_ in bridge.calls))
            self.assertEqual(sync.status()['scan_claims'],[])

    def test_timezone_floating_url_and_alarms_round_trip(self):
        bridge=NativeBridge('calendar');sync=self.calendar(bridge);sync.start_sync()
        item=self.business.calendar.list_events()[0]
        self.business.calendar.update_event(item['id'],{'repeat_timezone':'America/New_York','floating':True,'url':'https://example.com/meeting','alarms':[{'offset':-300}]})
        result=sync.sync();self.assertEqual(result['exported'],1,result)
        self.assertEqual(bridge.rows[0]['timezone'],'America/New_York')
        self.assertEqual(bridge.rows[0]['url'],'https://example.com/meeting')
        self.assertTrue(bridge.rows[0]['floating'])
        bridge.rows[0].update(timezone='Europe/Paris',floating=False,url='https://example.com/apple',alarms=[{'offset':-600}])
        self.assertEqual(sync.sync()['updated'],1)
        item=self.business.calendar.get_event(item['id']);self.assertEqual(item['repeat_timezone'],'Europe/Paris')
        self.assertEqual(item['alarms'],[{'offset':-600}]);self.assertFalse(item['floating'])
        self.assertEqual(sync.sync()['exported'],0)

    def test_default_destination_comes_from_apple_without_seeding_extra_groups(self):
        self.assertEqual(self.business.calendar.categories(),[])
        self.assertEqual(self.business.tasks.categories(),[])
        bridge=NativeBridge('calendar');bridge.sources.append({**bridge.sources[0],'id':'b','title':'Other'})
        sync=self.calendar(bridge);sync.start_sync()
        native=next(row for row in self.business.calendar.categories() if row['apple']['default'])
        item=self.business.calendar.create_event({'title':'Default destination','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z'})
        self.assertEqual(item['category_id'],native['id'])
        self.assertEqual(len(self.business.calendar.categories()),2)

    def test_far_future_range_loads_without_deleting_other_cached_records(self):
        bridge=NativeBridge('calendar');future={**bridge.rows[0],'event_id':'future','external_id':'future','occurrence_date':'2035-06-01T01:00:00Z','start_at':'2035-06-01T01:00:00Z','end_at':'2035-06-01T02:00:00Z'};bridge.rows.append(future)
        sync=self.calendar(bridge);sync.clock=lambda:'2026-11-07T00:00:00Z';sync.start_sync()
        self.assertEqual(len(self.business.calendar.list_events()),1)
        result=sync.sync_range('2035-06-01T00:00:00Z','2035-07-01T00:00:00Z');self.assertEqual(result['status'],'success',result)
        self.assertEqual(len(self.business.calendar.list_events()),2)
        count=sum(op=='events' for op,_ in bridge.calls);sync.sync_range('2035-06-02T00:00:00Z','2035-06-03T00:00:00Z')
        self.assertEqual(sum(op=='events' for op,_ in bridge.calls),count)
        with self.assertRaises(ValueError):sync.sync_range('2030-01-01T00:00:00Z','2035-01-01T00:00:00Z')
        sync.disconnect();self.assertEqual(sync.sync_range('2040-01-01T00:00:00Z','2040-02-01T00:00:00Z')['status'],'cached')

    def test_current_recurring_completion_advances_from_native_and_keeps_history(self):
        bridge=NativeBridge('reminders');bridge.rows[0].update(due_on='2026-11-07',recurrence={'frequency':'daily'})
        sync=self.reminder(bridge);sync.start_sync();item=self.business.tasks.list_todos()[0]
        with self.assertRaises(ValueError):self.business.tasks.complete_todo(item['id']+'@2026-11-08',True)
        self.business.tasks.complete_todo(item['id'],True)
        self.assertFalse(self.business.tasks.get_todo(item['id'])['completed_at'])
        result=sync.sync();self.assertEqual(result['status'],'success',sync.status())
        current=self.business.tasks.get_todo(item['id']);self.assertEqual(current['due_on'],'2026-11-08')
        self.assertEqual(current['status'],'todo');self.assertEqual(len(current['completion_history']),1)
        self.assertEqual(len(self.business.tasks.list_todos()),1)
        self.assertEqual(sync.sync()['exported'],0)
        self.assertEqual(len([op for op,_ in bridge.calls if op=='reminder_complete']),1)

    def local_series(self):
        bridge=NativeBridge('calendar');bridge.rows=[]
        item=self.business.calendar.create_event({'title':'Series','start_at':'2026-11-07T01:00:00Z','end_at':'2026-11-07T02:00:00Z','repeat_frequency':'daily','repeat_timezone':'Asia/Shanghai'})
        sync=self.calendar(bridge);sync.clock=lambda:'2026-11-07T00:00:00Z';sync.start_sync()
        first=bridge.rows[0]
        for offset in (1,2):
            occurrence={**first,'event_id':first['event_id']+'-'+str(offset),'start_at':f'2026-11-{7+offset:02}T01:00:00Z','end_at':f'2026-11-{7+offset:02}T02:00:00Z','occurrence_date':f'2026-11-{7+offset:02}T01:00:00Z'};bridge.rows.append(occurrence)
        sync.sync()
        return bridge,sync,item

    def test_unconfirmed_occurrence_delete_preserves_instances_after_restart(self):
        bridge, sync, item = self.local_series()
        with self.assertRaisesRegex(PermissionError, '人工确认'):
            self.business.calendar.exclude_occurrence(item['id'], '2026-11-08')
        self.assertEqual(sync.sync()['deleted_remote'], 0)
        self.assertEqual(self.calendar(bridge).sync()['deleted_remote'], 0)
        self.assertEqual(len(bridge.rows), 3)
        self.assertFalse(any(op == 'delete' for op, _ in bridge.calls))

    def test_legacy_marker_instance_edit_without_metadata_baseline_preserves_url(self):
        bridge,sync,item=self.local_series()
        # Swift presents legacy marker URLs as an empty public URL and alcor_id.
        for row in bridge.rows:
            row.update(alcor_id=item['id'],url='')
        sync._set('item-metadata:'+item['id'],None)
        self.business.calendar.update_event(item['id']+'@2026-11-08',{'title':'One exception','edit_scope':'this'})
        operation_id=self.business.calendar.apple_operations()[0]['id']
        writes=sum(op=='upsert' for op,_ in bridge.calls)
        result=sync.sync()
        self.assertEqual(result['status'],'success',sync.status())
        payload=next(payload for op,payload in reversed(bridge.calls) if op=='upsert')
        self.assertNotIn('url',payload)
        self.assertEqual(Path(payload['receipt_file']).name,operation_id+'.json')
        self.assertEqual(sum(op=='upsert' for op,_ in bridge.calls),writes+1)
        sync.sync()
        self.assertEqual(sum(op=='upsert' for op,_ in bridge.calls),writes+1)

    def test_explicit_instance_url_clear_without_baseline_is_preserved(self):
        bridge,sync,item=self.local_series()
        sync._set('item-metadata:'+item['id'],None)
        self.business.calendar.update_event(item['id']+'@2026-11-08',{'url':'','edit_scope':'this'})
        result=sync.sync()
        self.assertEqual(result['status'],'success',sync.status())
        payload=next(payload for op,payload in reversed(bridge.calls) if op=='upsert')
        self.assertIn('url',payload)
        self.assertEqual(payload['url'],'')

    def test_native_instance_edit_uses_requested_scope_without_changing_other_instances(self):
        bridge,sync,item=self.local_series()
        self.business.calendar.update_event(item['id']+'@2026-11-08',{'title':'One exception','edit_scope':'this'})
        self.assertEqual(self.business.calendar.get_event(item['id'])['title'],'Series')
        result=sync.sync();self.assertEqual(result['status'],'success',sync.status())
        self.assertEqual([row['title'] for row in bridge.rows],['Series','One exception','Series'])
        self.assertEqual(self.business.calendar.get_event(item['id'])['title'],'Series')
        self.assertTrue(any(row.get('native_series_key') for row in self.business.calendar.list_events() if row['id']!=item['id']))

    def test_remote_edit_after_preview_blocks_approved_delete(self):
        from business.apple_delete_approval import prepare, commit
        from apple_sync.approved_delete import prepare as native_plan, run
        for kind in ('calendar', 'reminders'):
            bridge = NativeBridge(kind)
            sync = self.calendar(bridge) if kind == 'calendar' else self.reminder(bridge)
            domain = 'calendar' if kind == 'calendar' else 'tasks'
            owner = getattr(self.business, domain)
            sync.start_sync()
            item = (owner.list_events() if kind == 'calendar' else owner.list_todos())[0]
            method = 'delete_event' if kind == 'calendar' else 'delete_todo'
            args = {'id' if kind == 'calendar' else 'todo_id': item['id']}
            op = 'd' * 64
            local = prepare(owner, domain, method, args, op)
            plan = native_plan(sync, domain, op, local)
            commit(owner, domain, method, args, op, local['snapshot'], plan['native'])
            bridge.rows[0]['title'] = 'Changed after preview'
            with self.assertRaises((ValueError, RuntimeError)):
                run(sync, plan, lambda *a: True, lambda action, **kw: getattr(owner, action)(**kw))
            self.assertFalse(any(op in ('delete','reminder_delete') for op, _ in bridge.calls))
            self.assertTrue(owner.apple_operations())
            self.assertEqual(bridge.rows[0]['title'], 'Changed after preview')

    def test_old_empty_categories_and_existing_content_survive_reinitialization(self):
        from business.calendar.domain.service import CalendarService
        owner=self.business.calendar
        with owner._connect() as db:
            for identity,name in [('other','其他'),('tasks','事务'),('special_dates','特殊日期')]:
                db.execute('INSERT INTO calendar_categories VALUES (?,?,?,?)',(identity,name,'#123456','2025-01-01'))
        before=owner.categories();reopened=CalendarService(owner.db_path)
        self.assertEqual(reopened.categories(),before)
        local=owner.create_event({'title':'Existing special date','category_id':'special_dates','start_at':'2026-11-07T00:00:00Z','end_at':'2026-11-07T01:00:00Z'})
        self.assertEqual(CalendarService(owner.db_path).get_event(local['id'])['category_id'],'special_dates')

    def test_first_metadata_upgrade_imports_new_fields_without_changing_local_content(self):
        bridge=NativeBridge('calendar');sync=self.calendar(bridge);sync.start_sync();item=self.business.calendar.list_events()[0]
        with sync._connect() as db:db.execute("DELETE FROM settings WHERE key LIKE 'item-metadata:%'")
        with self.business.calendar._connect() as db:db.execute("UPDATE calendar_events SET apple_data='{}' WHERE id=?",(item['id'],))
        bridge.rows[0].update(url='https://example.com/legacy-user-url',alarms=[{'offset':-300}])
        self.assertEqual(sync.sync()['status'],'success')
        restored=self.business.calendar.get_event(item['id'])
        self.assertEqual(restored['title'],item['title']);self.assertEqual(restored['url'],'https://example.com/legacy-user-url')
        self.assertEqual(restored['alarms'],[{'offset':-300}])

    def test_unconfirmed_future_delete_preserves_all_occurrences(self):
        bridge, sync, item = self.local_series()
        with self.assertRaisesRegex(ValueError, 'Apple 来源'):
            self.business.calendar.delete_event(item['id'], 'future', '2026-11-08')
        self.assertEqual(sync.sync()['deleted_remote'], 0)
        self.assertEqual(len(bridge.rows), 3)
        self.assertFalse(any(op == 'delete' for op, _ in bridge.calls))

    def test_failed_range_never_claims_complete_coverage(self):
        bridge=NativeBridge('calendar');sync=self.calendar(bridge);sync.start_sync()
        before=sync.status()['coverage'];bridge.offline=True
        result=sync.sync_range('2035-01-01T00:00:00Z','2035-02-01T00:00:00Z')
        self.assertNotEqual(result['status'],'success')
        self.assertEqual(sync.status()['coverage'],before)
        self.assertEqual(len(self.business.calendar.list_events()),1)

    def test_native_apple_completion_advances_same_series_without_duplicates(self):
        bridge=NativeBridge('reminders');bridge.rows[0].update(due_on='2026-11-07',recurrence={'frequency':'daily'})
        sync=self.reminder(bridge);sync.start_sync();item=self.business.tasks.list_todos()[0]
        bridge.call('reminder_complete',list_id='a',reminder_id='reminder',external_id='external',occurrence_on='2026-11-07',completed=True)
        self.assertEqual(sync.sync()['status'],'success',sync.status())
        self.assertEqual(len(self.business.tasks.list_todos()),1)
        self.assertEqual(self.business.tasks.get_todo(item['id'])['due_on'],'2026-11-08')
        self.assertEqual(len(self.business.tasks.get_todo(item['id'])['completion_history']),1)
        history=self.business.tasks.list_todos('2026-11-07','2026-11-09')
        self.assertEqual(sorted(row['status'] for row in history),['completed','todo'])

    def test_confirmed_source_deletion_propagates_but_unsynced_local_data_blocks_it(self):
        bridge=NativeBridge('calendar');sync=self.calendar(bridge);clock=['2026-11-07T00:00:00Z'];sync.clock=lambda:clock[0]
        sync.start_sync();group=self.business.calendar.categories()[0];bridge.sources=[];bridge.rows=[]
        self.assertEqual(sync.sync()['status'],'partial');self.assertTrue(self.business.calendar.get_category(group['id']))
        clock[0]='2026-11-07T00:02:00Z'
        self.assertEqual(sync.sync()['status'],'success',sync.status())
        self.assertEqual(self.business.calendar.categories(),[])

    def test_unconfirmed_category_delete_preserves_pending_item_actions(self):
        bridge = NativeBridge('reminders')
        bridge.rows[0].update(due_on='2026-11-07', recurrence={'frequency':'daily'})
        sync = self.reminder(bridge)
        sync.start_sync()
        owner = self.business.tasks
        item = owner.list_todos()[0]
        owner.complete_todo(item['id'], True)
        operations = owner.apple_operations()
        with self.assertRaisesRegex(PermissionError, '人工确认'):
            owner.delete_category(item['category_id'])
        self.assertEqual(owner.apple_operations(), operations)
        self.assertEqual(owner.get_todo(item['id'])['id'], item['id'])

    def test_legacy_deleted_category_with_retained_records_never_deletes_apple_data(self):
        bridge=NativeBridge('calendar');sync=self.calendar(bridge);sync.start_sync()
        owner=self.business.calendar;item=owner.list_events()[0];old=item['category_id'];fallback=owner.create_category({'name':'Legacy retained'})
        owner.apple_place_item(item['id'],fallback['id'])
        with owner._connect() as db:db.execute('DELETE FROM calendar_categories WHERE id=?',(old,))
        result=sync.sync();self.assertEqual(result['status'],'partial',sync.status())
        self.assertFalse(any(op=='delete_calendar' and payload['calendar_id']=='a' for op,payload in bridge.calls))
        self.assertEqual(owner.get_event(item['id'])['category_id'],old)
        self.assertTrue(owner.get_category(old))
        self.assertTrue(any(row['kind']=='legacy_retained' for row in sync.status()['category_conflicts']))
        sync.resolve_category_conflict('a','acknowledge')
        self.assertEqual(sync.sync()['status'],'success',sync.status())

    def test_moving_one_series_instance_uses_the_selected_calendar_and_original_lookup(self):
        bridge,sync,item=self.local_series()
        owner=self.business.calendar
        target=owner.create_category({'name':'Instance destination'})
        sync.sync()
        original=next(row['calendar_id'] for row in sync._maps() if row['alcor_id']==item['id'])
        destination=owner.get_category(target['id'])['apple']['apple_id']
        owner.update_event(item['id']+'@2026-11-08',{'category_id':target['id'],'edit_scope':'this'})
        sync.sync()
        payload=next(payload for op,payload in reversed(bridge.calls) if op=='upsert')
        self.assertEqual(payload['calendar_id'],destination)
        self.assertEqual(payload['lookup_calendar_id'],original)
        self.assertFalse(payload['series'])
        moved=next(row for row in bridge.rows if row['start_at'].startswith('2026-11-08'))
        self.assertEqual(moved['calendar_id'],destination)
        self.assertEqual(owner.get_event(item['id'])['category_id'],item['category_id'])

    def test_a_queued_instance_move_stops_if_the_original_calendar_becomes_readonly(self):
        bridge,sync,item=self.local_series();owner=self.business.calendar
        target=owner.create_category({'name':'Writable destination'});sync.sync()
        original=next(row['calendar_id'] for row in sync._maps() if row['alcor_id']==item['id'])
        owner.update_event(item['id']+'@2026-11-08',{'category_id':target['id'],'edit_scope':'this'})
        writes=sum(op=='upsert' for op,_ in bridge.calls)
        next(row for row in bridge.sources if row['id']==original)['writable']=False
        self.assertNotEqual(sync.sync()['status'],'success')
        self.assertEqual(sum(op=='upsert' for op,_ in bridge.calls),writes)
        self.assertEqual(owner.get_event(item['id'])['category_id'],item['category_id'])
        self.assertTrue(owner.apple_operations())


if __name__ == '__main__':
    unittest.main()

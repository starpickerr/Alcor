"""Synthetic Reminders regressions. No EventKit store access."""
import copy
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

M = runpy.run_path(str(Path(__file__).with_name('apple-contracts.py')))
Business, NativeBridge, AppleReminderSync = M['Business'], M['NativeBridge'], M['AppleReminderSync']
from apple_sync.bridge import AppleCalendarBridge, BridgeError

class AlarmBridge(NativeBridge):
    def call(self, op, **payload):
        result = super().call(op, **payload)
        if op == 'reminder_complete' and not payload['completed']:
            row = next(row for row in self.rows if row['reminder_id'] == result['reminder']['reminder_id'])
            row['completed_at'] = None; result['reminder'] = copy.deepcopy(row)
        if op == 'reminder_upsert':
            row = next(row for row in self.rows if row['reminder_id'] == result['reminder']['reminder_id'])
            if 'alarms' in payload:
                alarms = copy.deepcopy(payload['alarms'])
            elif 'remind_at' in payload:
                alarms = [{'absolute':payload['remind_at']}] if payload['remind_at'] else []
            else:
                alarms = copy.deepcopy(row.get('alarms', []))
            row.update(alarms=alarms, alarm_count=len(alarms), remind_at=alarms[0].get('absolute') if alarms else None)
            result['reminder'] = copy.deepcopy(row)
            if payload.get('receipt_binding'):
                Path(payload['receipt_file']).write_text(json.dumps({'binding':payload['receipt_binding'],'saved':row}))
        return result

class Regressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.business = Business(self.root/'business')
        self.bridge = AlarmBridge('reminders')
        self.sync = AppleReminderSync(bridge=self.bridge,db_path=self.root/'sync.sqlite',todo_port=self.business.tasks)
        self.sync.start_auto = lambda: None
    def tearDown(self):
        self.business.meta.close(); self.temp.cleanup()
    def start(self):
        result = self.sync.start_sync()['result']
        self.assertEqual(result['status'], 'success', result)
    def test_complete_dispatch_and_native_error(self):
        with tempfile.TemporaryDirectory() as directory:
            app=Path(directory)/'App.app'; (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/MacOS/AlcorCalendarBridge').touch()
            expected={'op':'reminder_complete','list_id':'list','reminder_id':'id','external_id':'external','occurrence_on':'2026-11-07','completed':True}
            response={'ok':True,'reminder':{'id':'done'}}
            class Launcher:
                def __init__(self, command, **kwargs):
                    assert json.loads(Path(command[command.index('--stdin')+1]).read_text()) == expected
                    Path(command[command.index('--stdout')+1]).write_text(json.dumps(response))
                def communicate(self, timeout=None): return ('','')
                def poll(self): return 0
            with patch('apple_sync.bridge.subprocess.Popen',Launcher):
                bridge=AppleCalendarBridge(helper_path=app,platform='darwin')
                self.assertEqual(bridge.call('reminder_complete',**{k:v for k,v in expected.items() if k!='op'}), {'reminder':{'id':'done'}})
                response.update(ok=False,error='read only')
                with self.assertRaisesRegex(BridgeError,'read only'):
                    bridge.call('reminder_complete',**{k:v for k,v in expected.items() if k!='op'})
                with self.assertRaisesRegex(BridgeError,'unknown EventKit operation'): bridge.call('not_a_real_operation')
                bridge.close()
    def test_recurring_absolute_import_update_complete_restart(self):
        at='2026-11-08T01:00:00Z'
        self.bridge.rows[0].update(due_on='2026-11-07',due_time='09:00',alarm_count=1,remind_at=at,alarms=[{'absolute':at}],recurrence={'frequency':'daily'})
        self.start(); task=self.business.tasks.list_todos()[0]; identity=task['id']
        self.assertIsNone(task['remind_at']); self.assertEqual(task['alarms'],[{'absolute':at}]); self.assertEqual(task['native_snapshot']['remind_at'],at)
        self.business.tasks.update_todo(identity,{'title':'Changed'}); self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.bridge.rows[0]['alarms'],[{'absolute':at}]); self.assertEqual(self.sync.sync()['exported'],0)
        self.business.tasks.complete_todo(identity,True); self.assertEqual(self.sync.sync()['status'],'success')
        task=self.business.tasks.get_todo(identity); self.assertEqual(task['due_on'],'2026-11-08'); self.assertEqual(len(task['completion_history']),1)
        restarted=AppleReminderSync(bridge=self.bridge,db_path=self.root/'sync.sqlite',todo_port=self.business.tasks); restarted.start_auto=lambda:None
        self.assertEqual(restarted.sync()['exported'],0); self.assertEqual(self.business.tasks.list_todos()[0]['id'],identity)
        with self.assertRaises(ValueError): self.business.tasks.create_todo({'title':'invalid','repeat_frequency':'daily','remind_at':at})
    def test_alarm_intent_create_edit_clear_and_retry(self):
        self.bridge.rows=[]; self.start(); t=self.business.tasks.create_todo({'title':'UI','alarms':[{'absolute':'2026-11-08T01:00:00Z'}]}); self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.bridge.rows[0]['alarm_count'],1)
        self.business.tasks.update_todo(t['id'],{'title':'Changed','notes':'Keep alarm','priority':1}); self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.bridge.rows[0]['alarms'],[{'absolute':'2026-11-08T01:00:00Z'}]); self.assertNotIn('remind_at',[p for o,p in self.bridge.calls if o=='reminder_upsert'][-1])
        self.business.tasks.update_todo(t['id'],{'alarms':[{'absolute':'2026-11-09T02:00:00Z'}]}); self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.bridge.rows[0]['remind_at'],'2026-11-09T02:00:00Z')
        self.business.tasks.update_todo(t['id'],{'alarms':[]}); self.bridge.offline=True; self.assertEqual(self.sync.sync()['status'],'error'); self.assertEqual(self.bridge.rows[0]['alarm_count'],1)
        self.bridge.offline=False; self.assertEqual(self.sync.sync()['status'],'success'); self.assertEqual(self.bridge.rows[0]['alarm_count'],0)
    def test_legacy_remind_at_create_replace_clear(self):
        self.bridge.rows=[]; self.start(); t=self.business.tasks.create_todo({'title':'Tool','remind_at':'2026-11-08T01:00:00Z'}); self.assertEqual(self.sync.sync()['status'],'success')
        self.assertEqual(self.bridge.rows[0]['remind_at'],'2026-11-08T01:00:00+00:00')
        self.business.tasks.update_todo(t['id'],{'remind_at':'2026-11-09T02:00:00Z'}); self.assertEqual(self.sync.sync()['status'],'success'); self.assertEqual(self.bridge.rows[0]['remind_at'],'2026-11-09T02:00:00+00:00')
        self.business.tasks.update_todo(t['id'],{'remind_at':None}); self.assertEqual(self.sync.sync()['status'],'success'); self.assertEqual(self.bridge.rows[0]['alarm_count'],0)
    def test_recurring_absolute_remote_update_and_move_preserve_identity(self):
        at='2026-11-08T01:00:00Z'; later='2026-11-09T01:00:00Z'
        self.bridge.sources.append({**self.bridge.sources[0], 'id':'b','title':'Second'})
        self.bridge.rows[0].update(due_on='2026-11-07',alarm_count=1,remind_at=at,alarms=[{'absolute':at}],recurrence={'frequency':'daily'})
        self.start(); task=self.business.tasks.list_todos()[0]; identity=task['id']
        self.bridge.rows[0].update(remind_at=later,alarms=[{'absolute':later}])
        self.assertEqual(self.sync.sync()['updated'],1)
        task=self.business.tasks.get_todo(identity); self.assertEqual(task['alarms'],[{'absolute':later}]); self.assertIsNone(task['remind_at'])
        target=next(link['alcor_id'] for link in self.sync.status()['links'] if link['apple_id']=='b')
        self.business.tasks.update_todo(identity,{'category_id':target})
        result=self.sync.sync(); self.assertEqual(result['status'],'success',result)
        self.assertEqual(self.bridge.rows[0]['list_id'],'b'); self.assertEqual(self.bridge.rows[0]['alarms'],[{'absolute':later}])
        self.assertEqual(self.business.tasks.list_todos()[0]['id'],identity); self.assertEqual(self.sync.sync()['exported'],0)
    def test_queued_completion_through_python_bridge_retries_without_repeating_success(self):
        self.bridge.rows[0].update(due_on='2026-11-07',recurrence={'frequency':'daily'})
        app=self.root/'App.app'; (app/'Contents/MacOS').mkdir(parents=True)
        (app/'Contents/MacOS/AlcorCalendarBridge').touch()
        native=self.bridge.call
        dispatch=AppleCalendarBridge(helper_path=app,platform='darwin')
        state={'fail':True,'requests':[]}
        class Launcher:
            def __init__(self,command,**kwargs):
                request=json.loads(Path(command[command.index('--stdin')+1]).read_text())
                state['requests'].append(request)
                if state['fail']:
                    response={'ok':False,'error':'Synthetic native pre-save failure','no_side_effects':True}
                else:
                    response={'ok':True,**native(request.pop('op'),**request)}
                Path(command[command.index('--stdout')+1]).write_text(json.dumps(response))
            def communicate(self,timeout=None): return ('','')
            def poll(self): return 0
        def call(op,**payload):
            if op != 'reminder_complete': return native(op,**payload)
            with patch('apple_sync.bridge.subprocess.Popen',Launcher): return dispatch.call(op,**payload)
        self.bridge.call=call
        self.start(); item=self.business.tasks.list_todos()[0]
        with self.assertRaises(ValueError): self.business.tasks.complete_todo(item['id']+'@2026-11-08',True)
        self.business.tasks.complete_todo(item['id'],True)
        result=self.sync.sync(); self.assertEqual(result['status'],'partial'); self.assertEqual(len(state['requests']),1)
        self.assertEqual(self.business.tasks.get_todo(item['id'])['due_on'],'2026-11-07')
        state['fail']=False; self.assertEqual(self.sync.sync()['status'],'success')
        task=self.business.tasks.get_todo(item['id']); self.assertEqual(task['due_on'],'2026-11-08'); self.assertEqual(len(task['completion_history']),1)
        self.assertEqual(self.sync.sync()['exported'],0); self.assertEqual(len(state['requests']),2)
        self.assertEqual(state['requests'][-1]['occurrence_on'],'2026-11-07'); self.assertTrue(state['requests'][-1]['completed'])
        dispatch.close()
    def test_reopen_current_recurring_and_guard_advanced_history(self):
        self.bridge.rows[0].update(due_on='2026-11-07',completed=True,completed_at='2026-11-07T01:00:00Z',recurrence={'frequency':'daily'})
        self.start(); item=self.business.tasks.list_todos()[0]
        self.business.tasks.complete_todo(item['id'],False)
        self.assertEqual(self.sync.sync()['status'],'success')
        self.assertFalse(self.bridge.rows[0]['completed'])
        self.assertFalse(next(p for o,p in self.bridge.calls if o=='reminder_complete')['completed'])
        self.business.tasks.complete_todo(item['id'],True); self.assertEqual(self.sync.sync()['status'],'success')
        with self.assertRaisesRegex(ValueError,'Apple'): self.business.tasks.complete_todo(item['id'],False)
    def test_relative_alarm_is_preserved_readonly_and_unbound_completion_stays_local(self):
        self.bridge.rows[0].update(alarm_count=1,remind_at=None,alarms=[{'offset':-900}],recurrence={'frequency':'daily'})
        result=self.sync.start_sync()['result']; self.assertEqual(result['status'],'partial')
        task=self.business.tasks.list_todos()[0]; self.assertEqual(task['alarms'],[{'offset':-900}])
        with self.assertRaises(PermissionError): self.business.tasks.update_todo(task['id'],{'title':'No write'})
        self.assertEqual(self.bridge.rows[0]['alarms'],[{'offset':-900}])
        unbound=self.business.tasks.create_todo({'title':'Local task'})
        self.business.tasks.complete_todo(unbound['id'],True)
        self.assertEqual(self.business.tasks.get_todo(unbound['id'])['status'],'completed')
        self.assertFalse(any(op=='reminder_complete' for op,_ in self.bridge.calls))
    def test_unknown_legacy_baseline_cannot_erase_alarm(self):
        self.bridge.rows=[]; self.start(); t=self.business.tasks.create_todo({'title':'UI','alarms':[{'absolute':'2026-11-08T01:00:00Z'}]}); self.sync.sync()
        with self.sync._connect() as db: db.execute("DELETE FROM settings WHERE key=?",('reminder-local-remind-at:'+t['id'],))
        self.business.tasks.update_todo(t['id'],{'title':'Changed'})
        result=self.sync.sync(); self.assertEqual(result['conflicts'],1); self.assertEqual(self.bridge.rows[0]['alarm_count'],1)
        self.sync.resolve_conflict(t['id'],'keep_alcor'); self.assertEqual(self.bridge.rows[0]['alarm_count'],1)

if __name__=='__main__': unittest.main()

"""Real AppleWorker message loop; only EventKit is replaced by a synthetic bridge."""
import copy, json, runpy, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
M = runpy.run_path(str(ROOT/'product/tests/apple-contracts.py'))
import worker

class SyntheticBridge:
    def __init__(self):
        self.bridges = {kind: M['NativeBridge'](kind) for kind in ('calendar', 'reminders')}
        native=self.bridges['calendar'];template=copy.deepcopy(native.rows[0]);template['recurrence']={'frequency':'daily','interval':1}
        native.rows=[{**copy.deepcopy(template),'event_id':'series-'+str(day),'occurrence_date':f'2026-11-{day:02}T01:00:00Z','start_at':f'2026-11-{day:02}T01:00:00Z','end_at':f'2026-11-{day:02}T02:00:00Z'} for day in (7,8,9)]
        native.sources.append({**native.sources[0],'id':'other','source_id':'account2','title':'Other source'})
        native.rows.append({**copy.deepcopy(native.rows[2]),'event_id':'foreign','calendar_id':'other','source_id':'account2'})
        self.log = Path(sys.argv[1])/'synthetic-native-writes.jsonl'
        (Path(sys.argv[1])/'synthetic-runtime.json').write_text(json.dumps({'executable': sys.executable, 'version': sys.version, 'official_desktop': False}))
    def call(self, op, **payload):
        kind = 'reminders' if op.startswith('reminder') else 'calendar'
        if op=='delete' and payload.get('series'):
            native=self.bridges[kind];expected=payload['expected_snapshot']
            selected=[r for r in native.rows if r['event_id']==payload['event_id'] and r['calendar_id']==payload['calendar_id']]
            if selected!=[expected] or expected['source_id']!=payload['expected_source_id']:raise ValueError('native snapshot changed')
            target=datetime.fromisoformat(expected['occurrence_date'].replace('Z','+00:00'))
            native.rows=[r for r in native.rows if not(r['source_id']==expected['source_id'] and r['calendar_id']==expected['calendar_id'] and r.get('external_id')==expected['external_id'] and datetime.fromisoformat(r['occurrence_date'].replace('Z','+00:00'))>=target)]
            for r in native.rows:
                if r['source_id']==expected['source_id'] and r['calendar_id']==expected['calendar_id'] and r.get('external_id')==expected['external_id']:r['recurrence']={**r['recurrence'],'until':'2026-11-07T15:59:59Z'}
            result={'deleted':True}
        else:result = self.bridges[kind].call(op, **payload)
        if op in ('status', 'reminder_status'):
            return {**result, 'delete_write_version': 1}
        if op in ('delete', 'reminder_delete', 'delete_calendar', 'reminder_list_delete'):
            proof = {'version': 1, 'binding': payload['receipt_binding'], 'op': op,
                     'source_id': payload['expected_source_id'], 'expected_snapshot': payload['expected_snapshot'],
                     'series': payload.get('series', False)}
            result = {'deleted': True, 'delete_receipt': proof}
            Path(payload['receipt_file']).write_text(json.dumps({'binding': payload['receipt_binding'], **result}))
            with self.log.open('a') as stream: stream.write(json.dumps({'op': op})+'\n')
        return result

# Test-only dependency injection before the real worker constructs anything.
# No native bridge, native helper, owner database or Profile exists outside the supplied temporary root.
worker.AppleCalendarBridge = SyntheticBridge
for manager in (worker.AppleCalendarSync, worker.AppleReminderSync):
    manager.start_auto = lambda self: None
    manager.resume_auto = lambda self: None
worker.main()

"""Real AppleWorker; synthetic EventKit accepts only exact conditional item updates."""
import copy
import json
import runpy
import sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
M = runpy.run_path(str(ROOT/'product/tests/apple-contracts.py'))
import worker

class SyntheticBridge:
    def __init__(self):
        self.root = Path(sys.argv[1]); self.changed = set()
        assert self.root.parent.name.startswith('alcor-bound-conflict-wire-')
        self.bridges = {kind: M['NativeBridge'](kind) for kind in ('calendar','reminders')}
        for kind, bridge in self.bridges.items():
            key, group = ('event_id','calendar_id') if kind == 'calendar' else ('reminder_id','list_id')
            bridge.sources.append({**bridge.sources[0], 'id':'b', 'source_id':'account2', 'title':'Other source'})
            bridge.rows.append({**copy.deepcopy(bridge.rows[0]), key:'other-native', group:'b', 'source_id':'account2', 'external_id':'other-external', 'title':'Unrelated item'})
        if (self.root/'same-event-occurrences').exists():
            bridge=self.bridges['calendar']; original=bridge.rows[0]
            original['recurrence']={'frequency':'daily','interval':1}
            bridge.rows.append({**copy.deepcopy(original),'title':'Different occurrence',
                'occurrence_date':'2026-11-06T01:00:00Z','start_at':'2026-11-06T01:00:00Z','end_at':'2026-11-06T02:00:00Z'})
        (self.root/'synthetic-runtime.json').write_text(json.dumps({'executable':sys.executable,'version':sys.version,'official_desktop':False}))
    def call(self, op, **payload):
        kind = 'reminders' if op.startswith('reminder') else 'calendar'
        bridge = self.bridges[kind]
        key, group = ('reminder_id','list_id') if kind == 'reminders' else ('event_id','calendar_id')
        for marker in ('native-change','healthy-change'):
            if (self.root/marker).exists() and marker not in self.changed:
                self.changed.add(marker)
                for b in self.bridges.values():
                    for row in b.rows:
                        if (row['title'] == 'Unrelated item') == (marker == 'healthy-change'):
                            row['title'] += ' changed in Apple'
        if (self.root/'wrong-source').exists():
            for b in self.bridges.values(): b.rows[0]['source_id']='wrong-source'
        if (self.root/'deny-access').exists(): raise PermissionError('synthetic native permission revoked')
        reads=('status','reminder_status','calendars','reminder_lists','events','reminders','lookup','reminder_lookup')
        if op in reads:
            result=bridge.call(op,**payload)
            return {**result,'approved_export_version':1} if op in ('status','reminder_status') else result
        if op not in ('upsert','reminder_upsert'): raise AssertionError('Unexpected synthetic native operation: '+op)
        assert not payload.get('expected_absent') and payload.get(key) and payload.get(group)
        rows=[r for r in bridge.rows if r[key]==payload[key] and r[group]==payload[group]]
        if kind=='calendar' and payload.get('occurrence_date'):
            instant=datetime.fromisoformat(payload['occurrence_date'].replace('Z','+00:00'));assert instant.tzinfo is not None
            rows=[r for r in rows if abs((datetime.fromisoformat(r['occurrence_date'].replace('Z','+00:00'))-instant).total_seconds())<1]
        assert len(rows)==1 and rows[0]==payload['expected_snapshot']
        target=next(r for r in bridge.sources if r['id']==payload[group])
        assert target==payload['expected_destination_snapshot'] and target['source_id']==payload['expected_source_id']
        receipt=Path(payload['receipt_file']); assert receipt.is_relative_to(self.root) and not receipt.exists()
        if kind=='calendar':receipt.write_text(json.dumps({'binding':payload['receipt_binding'],'intent':True}))
        result=bridge.call(op,**payload);saved=result['reminder' if kind=='reminders' else 'event']
        if kind=='calendar':receipt.write_text(json.dumps({'binding':payload['receipt_binding'],'saved':saved}))
        with (self.root/'native-writes.jsonl').open('a') as stream: stream.write(json.dumps({'op':op,'native':saved})+'\n')
        if (self.root/'lose-native-response').exists(): raise TimeoutError('Synthetic lost native reply after receipt')
        return result

worker.AppleCalendarBridge=SyntheticBridge
for manager in (worker.AppleCalendarSync,worker.AppleReminderSync):
    manager.start_auto=lambda self:None
    manager.resume_auto=lambda self:None
worker.main()

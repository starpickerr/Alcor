"""Real AppleWorker protocol with a strictly read-only synthetic EventKit dependency."""
import json
import runpy
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
M = runpy.run_path(str(ROOT/'product/tests/apple-contracts.py'))
import worker

class SyntheticReadOnlyBridge:
    def __init__(self):
        self.bridges = {kind: M['NativeBridge'](kind) for kind in ('calendar', 'reminders')}
        self.log = Path(sys.argv[1])/'synthetic-native-calls.jsonl'
        self.changed = False
        (Path(sys.argv[1])/'synthetic-runtime.json').write_text(json.dumps({'executable': sys.executable, 'version': sys.version, 'official_desktop': False}))
    def call(self, op, **payload):
        with self.log.open('a') as stream: stream.write(json.dumps({'op': op})+'\n')
        if not self.changed and (self.log.parent/'synthetic-change-native').exists():
            self.changed = True
            for bridge in self.bridges.values():
                for row in bridge.rows: row['title'] += ' changed in Apple'
        if (self.log.parent/'synthetic-wrong-source').exists():
            for bridge in self.bridges.values():
                for row in bridge.rows: row['source_id'] = 'wrong-source'
        if (self.log.parent/'synthetic-deny-access').exists():
            raise PermissionError('synthetic native read permission revoked')
        if op not in ('status','reminder_status','calendars','reminder_lists','events','reminders','lookup','reminder_lookup'):
            raise AssertionError('Native writes or permission requests forbidden in restore fixture: '+op)
        return self.bridges['reminders' if op.startswith('reminder') else 'calendar'].call(op, **payload)

worker.AppleCalendarBridge = SyntheticReadOnlyBridge
for manager in (worker.AppleCalendarSync, worker.AppleReminderSync):
    manager.start_auto = lambda self: None
    manager.resume_auto = lambda self: None
worker.main()

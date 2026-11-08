"""Real AppleWorker message loop; only EventKit is replaced by a synthetic bridge."""
import json, runpy, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
M = runpy.run_path(str(ROOT/'product/tests/apple-contracts.py'))
import worker
from apple_sync.bridge import BridgeError

class SyntheticBridge:
    def __init__(self):
        self.bridges = {kind: M['NativeBridge'](kind) for kind in ('calendar', 'reminders')}
        self.log = Path(sys.argv[1])/'synthetic-native-writes.jsonl'
        self.not_sent = (Path(sys.argv[1])/'synthetic-not-sent-once').exists()
        (Path(sys.argv[1])/'synthetic-runtime.json').write_text(json.dumps({'executable': sys.executable, 'version': sys.version, 'official_desktop': False}))
    def call(self, op, **payload):
        kind = 'reminders' if op.startswith('reminder') else 'calendar'
        if self.not_sent and op in ('delete', 'reminder_delete'):
            self.not_sent = False
            raise BridgeError('synthetic proven unsent', native_quiesced=True, native_no_side_effects=True)
        result = self.bridges[kind].call(op, **payload)
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

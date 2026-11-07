import subprocess
import tempfile
from pathlib import Path

source=Path(__file__).resolve().parents[2]/'packages/stella-apple-sync/python/apple_sync/native'
with tempfile.TemporaryDirectory(prefix='stella-native-fields-') as temporary:
    root=Path(temporary)
    main=(source/'CalendarBridge.swift').read_text().split('if let index = CommandLine.arguments.firstIndex')[0]
    main += r'''
let event=EKEvent(eventStore:EKEventStore())
event.url=URL(string:"https://example.com/user-url")
event.alarms=[EKAlarm(relativeOffset:-900),EKAlarm(absoluteDate:Date(timeIntervalSince1970:12345))]
assert(publicAlarms(event).count==2)
assert(publicAlarms(event).contains { $0["offset"] as? Double == -900 })
try applyPublicFields(event,["url":"https://example.com/changed","alarms":[["offset":-300.0]]])
assert(event.url?.absoluteString=="https://example.com/changed")
assert(event.alarms?.first?.relativeOffset == -300)
let rule=try recurrenceRule(["native_recurrence":["frequency":"monthly","interval":2,"rule_count":1,"count":12,"weekday_ordinals":[["day":0,"week":2]],"months":[1,3]]])
assert(rule?.interval==2)
assert(rule?.daysOfTheWeek?.first?.weekNumber==2)
assert(rule?.recurrenceEnd?.occurrenceCount==12)
let path=CommandLine.arguments[1]+"/native-receipts/write.json"
try FileManager.default.createDirectory(atPath:CommandLine.arguments[1]+"/native-receipts",withIntermediateDirectories:true)
let request:[String:Any] = ["receipt_file":path]
try saveReceipt(request,["intent":true])
do { _=try receiptLookup(request,"event_id"); fatalError("Interrupted create must not be retried") } catch {}
try saveReceipt(request,["saved":["event_id":"native-id","external_id":"external"]])
let lookedUp = try receiptLookup(request,"event_id")
assert(lookedUp["event_id"] as? String == "native-id")
print("PASS: native alarms, public URL, ordinal/count recurrence and durable write receipts without Apple access")
'''
    (root/'main.swift').write_text(main)
    subprocess.run(['/usr/bin/swiftc','-framework','EventKit',str(root/'main.swift'),*[str(source/name) for name in ('ReminderBridge.swift','ReminderFields.swift','OccurrenceIdentity.swift','ScanWindows.swift')],'-o',str(root/'check')],check=True)
    subprocess.run([str(root/'check'),str(root)],check=True)

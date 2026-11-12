import subprocess
import shutil
import tempfile
from pathlib import Path

source=Path(__file__).resolve().parents[2]/'packages/alcor-apple-sync/python/apple_sync/native'
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
try saveReceipt(request,["saved":["event_id":"native-id","external_id":"external","stella_id":"old-local-id"]])
let lookedUp = try receiptLookup(request,"event_id")
assert(lookedUp["event_id"] as? String == "native-id")
assert(legacyMarker("stella://calendar/local-id", "calendar")=="local-id")
assert(legacyMarker("stella://reminder/task-id", "reminder")=="task-id")
assert(legacyMarker("alcor://calendar/local-id", "calendar").isEmpty)
let store=EKEventStore()
let cal=EKCalendar(for:.event,eventStore:store)
let markerEvent=EKEvent(eventStore:store)
markerEvent.calendar=cal; markerEvent.startDate=Date(timeIntervalSince1970:1); markerEvent.endDate=Date(timeIntervalSince1970:2)
markerEvent.url=URL(string:"stella://calendar/local-id")
let serializedEvent=serialize(markerEvent)
assert(serializedEvent["alcor_id"] as? String == "local-id")
assert(serializedEvent["url"] as? String == "")
let list=EKCalendar(for:.reminder,eventStore:store)
let markerReminder=EKReminder(eventStore:store)
markerReminder.calendar=list; markerReminder.url=URL(string:"stella://reminder/task-id")
let serializedReminder=serializeReminder(markerReminder)
assert(serializedReminder["alcor_id"] as? String == "task-id")
assert(serializedReminder["url"] as? String == "")
let legacy="stella://calendar/local-id"
event.url=URL(string:legacy)
try applyPublicFields(event,[:])
assert(event.url?.absoluteString==legacy)
assert(matchesMarkerOccurrence(candidateMarker:legacy,candidateOccurrence:Date(timeIntervalSince1970:1),requestedMarker:legacy,requestedOccurrence:Date(timeIntervalSince1970:1)))
assert(!matchesMarkerOccurrence(candidateMarker:legacy,candidateOccurrence:Date(timeIntervalSince1970:1),requestedMarker:legacy,requestedOccurrence:Date(timeIntervalSince1970:2)))
let newEvent=EKEvent(eventStore:EKEventStore())
try applyPublicFields(newEvent,[:])
assert(newEvent.url==nil)
print("PASS: native alarms, public URL, ordinal/count recurrence and durable write receipts without Apple access")
'''
    (root/'main.swift').write_text(main)
    helpers = ('ReminderBridge.swift','ReminderFields.swift','OccurrenceIdentity.swift','ScanWindows.swift')
    for name in helpers:
        shutil.copyfile(source / name, root / name)
    subprocess.run(['/usr/bin/swiftc','-framework','EventKit',str(root/'main.swift'),*[str(root/name) for name in helpers],'-o',str(root/'check')],check=True)
    subprocess.run([str(root/'check'),str(root)],check=True)

"""Execute the shipped Swift upsert branch against a tiny fake store, never EventKit."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[2]
NATIVE=ROOT/'packages/alcor-apple-sync/python/apple_sync/native'

class NativeCreateContract(unittest.TestCase):
 def test_real_swift_conditions_precede_save_and_receipt_binds_result(self):
  reminder=(NATIVE/'ReminderBridge.swift').read_text()
  shared=(NATIVE/'CalendarBridge.swift').read_text()
  body=reminder[reminder.index('    if op == "reminder_upsert" {'):reminder.index('    if op == "reminder_rename"')]
  receipt=shared[shared.index('func receipt(_ request:'):shared.index('// A process handles one request.')]
  swift=r'''
import Foundation
func fail(_ message:String)->Never { print("FAIL: "+message); exit(2) }
func check(_ value:Bool) { if !value { fail("contract assertion") } }
struct BridgeFailure: Error { let message:String; init(_ message:String) { self.message=message } }
func text(_ row:[String:Any],_ key:String)->String? { row[key] as? String }
func required(_ row:[String:Any],_ key:String)throws->String { guard let s=text(row,key),!s.isEmpty else { throw BridgeFailure("missing") }; return s }
func date(_ value:String?)->Date? { value.flatMap { ISO8601DateFormatter().date(from:$0) } }
class Source { var sourceIdentifier="account1" }
enum Entity { case reminder }
class List { var calendarIdentifier="a"; var source:Source?=Source(); var allowsContentModifications=true; var title="Synthetic" }
class EKAlarm { init(absoluteDate:Date) {} }
class EKReminder {
 var calendar:List!;var calendarItemIdentifier="new-item";var title="";var notes=""
 var dueDateComponents:DateComponents?;var startDateComponents:DateComponents?
 var alarms:[EKAlarm]?;var priority=0;var isCompleted=false;var completionDate:Date?;var recurrenceRules:[String]?
 init(eventStore:EKEventStore) { calendar=eventStore.list }
}
class EKEventStore {
 var list=List();var existing:EKReminder?;var saves=0;var fail=false
 func calendars(for:Entity)->[List] { [list] }
 func save(_ item:EKReminder,commit:Bool)throws { saves+=1;if fail { throw BridgeFailure("save unknown") };existing=item }
}
func reminderLists(_ store:EKEventStore)->[[String:Any]] { [["id":"a","source_id":store.list.source!.sourceIdentifier,"title":store.list.title,"writable":true]] }
func findReminder(_ store:EKEventStore,_ req:[String:Any])throws->EKReminder? { store.existing }
func serializeReminder(_ item:EKReminder)->[String:Any] { ["reminder_id":item.calendarItemIdentifier,"list_id":item.calendar.calendarIdentifier,"source_id":item.calendar.source!.sourceIdentifier,"title":item.title] }
func reminderComponents(_ day:String?,_ time:String?,_ zone:String?)throws->DateComponents? { nil }
func hasNonAbsoluteAlarm(_ alarms:[EKAlarm])->Bool { false }
func recurrenceRule(_ req:[String:Any])throws->String? { nil }
func applyPublicFields(_ item:EKReminder,_ req:[String:Any])throws {}
''' + receipt + '\nfunc run(_ request:[String:Any],_ store:EKEventStore)throws->[String:Any] { let op="reminder_upsert"\n' + body + '\nthrow BridgeFailure("unknown")\n}\n' + r'''
let root=CommandLine.arguments[1]
try FileManager.default.createDirectory(atPath:root,withIntermediateDirectories:true)
func req(_ name:String,_ store:EKEventStore)->[String:Any] { ["list_id":"a","title":"New","expected_source_id":"account1","expected_destination_snapshot":reminderLists(store)[0],"expected_absent":true,"receipt_binding":String(repeating:"a",count:64),"receipt_file":root+"/"+name+".json"] }
for fault in ["source","snapshot","existing"] {
 let store=EKEventStore();let request=req(fault,store)
 if fault=="source" { store.list.source!.sourceIdentifier="account2" }
 if fault=="snapshot" { store.list.title="Changed" }
 if fault=="existing" { store.existing=EKReminder(eventStore:store) }
 do { _=try run(request,store);fail("accepted changed target") } catch { check(store.saves==0);check(!FileManager.default.fileExists(atPath:request["receipt_file"] as! String)) }
}
let store=EKEventStore();let request=req("success",store);let result=try run(request,store)
let saved=try receipt(request)!
check(store.saves==1 && saved["binding"] as? String==request["receipt_binding"] as? String)
check(NSDictionary(dictionary:saved["saved"] as! [String:Any]).isEqual(to:result["reminder"] as! [String:Any]))
do { _=try run(request,store);fail("replayed") } catch { check(store.saves==1) }
let failed=EKEventStore();failed.fail=true;let unknown=req("unknown",failed)
do { _=try run(unknown,failed);fail("save succeeded") } catch { check(failed.saves==1);check(try receipt(unknown)?["intent"] as? Bool==true) }
print("PASS: real upsert branch, source/snapshot/absence, bound receipt and unknown intent")
'''
  work=ROOT/'.create-source/native-contract';work.mkdir(parents=True,exist_ok=True)
  (work/'main.swift').write_text(swift)
  subprocess.run(['/usr/bin/swiftc',str(work/'main.swift'),'-o',str(work/'fake-reminder')],check=True)
  with tempfile.TemporaryDirectory(prefix='case-',dir=work) as tmp:
   subprocess.run([str(work/'fake-reminder'),str(Path(tmp)/'native-receipts')],check=True)

if __name__=='__main__':unittest.main(verbosity=2)

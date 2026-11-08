"""Compile actual identity observation against tiny fake EventKit types; no EventKit."""
import subprocess,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class NativeIdentity(unittest.TestCase):
 def test_public_query_contract_retains_candidates_and_uncertainty(self):
  source=(ROOT/'packages/alcor-apple-sync/python/apple_sync/native/CalendarBridge.swift').read_text();code=source[source.index('func lookupEventIdentity('):source.index('func recurrenceRule(')]
  harness=r'''import Foundation
struct BridgeFailure:Error {let message:String;init(_ message:String){self.message=message}}
func required(_ row:[String:Any],_ key:String)throws->String {guard let value=row[key] as? String,!value.isEmpty else{throw BridgeFailure(key)};return value}
enum SourceType {case calDAV,exchange,mobileMe,local}
class Source {var sourceIdentifier="account";var sourceType=SourceType.calDAV}
class CalendarObject {var calendarIdentifier="calendar";var source=Source()}
class EKEvent {var eventIdentifier:String?="event";var calendar=CalendarObject()}
enum Entity {case event}
class EKEventStore {
 var target=CalendarObject();var sources:[Source] { [target.source] };var rows=[EKEvent]();var direct:EKEvent?;var changed=false;var reads=0
 func calendars(for entity:Entity)->[CalendarObject]{[target]}
 func calendarItems(withExternalIdentifier key:String)->[Any]{rows}
 func event(withIdentifier key:String)->EKEvent?{direct}
}
var authorization="full_access"
func auth()->String{authorization}
func calendars(_ store:EKEventStore)->[[String:Any]] {store.reads += 1;return [["id":"calendar","source_id":store.target.source.sourceIdentifier,"title":store.changed && store.reads>1 ? "changed":"same"]]}
func serialize(_ event:EKEvent)->[String:Any]{["event_id":event.eventIdentifier ?? "","calendar_id":event.calendar.calendarIdentifier]}
'''
  checks=r'''
let request:[String:Any]=["calendar_id":"calendar","expected_source_id":"account","event_id":"event","external_id":"server-event"]
func probe(_ store:EKEventStore)throws->[String:Any]{try lookupEventIdentity(store,request)["identity_observation"] as! [String:Any]}
let empty=try probe(EKEventStore());assert(empty["status"] as? String == "absent");assert(empty["source_type"] as? String == "caldav")
let moved=EKEventStore();let other=EKEvent();other.eventIdentifier="new-id";other.calendar.calendarIdentifier="other-calendar";moved.rows=[other];assert(try probe(moved)["status"] as? String == "present")
let direct=EKEventStore();direct.direct=EKEvent();assert(try probe(direct)["status"] as? String == "present")
let local=EKEventStore();local.target.source.sourceType = .local;assert(try probe(local)["status"] as? String == "unknown")
let changed=EKEventStore();changed.changed=true;assert(try probe(changed)["status"] as? String == "unknown")
let account=EKEventStore();account.target.source.sourceIdentifier="different";assert(try probe(account)["status"] as? String == "unknown")
authorization="denied";assert(try probe(EKEventStore())["status"] as? String == "unknown")
print("PASS: exact global identity observation; candidates/source changes/permission loss stay non-absent; no EventKit")
'''
  # Swift assert's autoclosure is nonthrowing; evaluate throwing queries first.
  import re
  checks=re.sub(r'assert\(try probe\(([^)]+)\)\["status"\] as\? String == "([^"]+)"\)',r'do { let row = try probe(\1); assert(row["status"] as? String == "\2") }',checks)
  checks=checks.replace('assert(try probe(EKEventStore())["status"] as? String == "unknown")','do { let row = try probe(EKEventStore()); assert(row["status"] as? String == "unknown") }')
  with tempfile.TemporaryDirectory(prefix='native-identity-') as tmp:
   directory=Path(tmp);(directory/'main.swift').write_text(harness+code+checks)
   subprocess.run(['/usr/bin/swiftc',str(directory/'main.swift'),'-o',str(directory/'test')],check=True)
   subprocess.run([str(directory/'test')],check=True)
if __name__=='__main__':unittest.main(verbosity=2)

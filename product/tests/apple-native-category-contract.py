#!/usr/bin/env python3
"""Synthetic category receipt/error contract checks; never starts EventKit."""
import importlib.util
import json
import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / 'packages/alcor-apple-sync/python/apple_sync/native'
spec = importlib.util.spec_from_file_location('category_bridge_under_test', ROOT / 'packages/alcor-apple-sync/python/apple_sync/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
BINDING = 'a' * 64


class FakeProcess:
    def __init__(self, response):
        self.response = response

    def communicate(self, request, timeout):
        return json.dumps(self.response), ''


class CategoryBridgeProofTests(unittest.TestCase):
    def call_error(self, op, payload, response):
        client = bridge.AppleCalendarBridge('/synthetic/helper', platform='darwin')
        with patch.object(Path, 'is_file', return_value=True), \
             patch.object(client, '_start', return_value=FakeProcess(response)), \
             patch.object(client, '_finish', return_value=True):
            with self.assertRaises(bridge.BridgeError) as caught:
                client.call(op, **payload)
        return caught.exception

    def test_only_matching_versioned_proof_is_accepted(self):
        for op in ('rename', 'reminder_rename'):
            request = {'category_write_version': 1, 'receipt_binding': BINDING}
            proof = {'ok': False, 'error': 'synthetic', 'no_side_effects': True,
                     'category_write_version': 1, 'receipt_binding': BINDING, 'op': op}
            self.assertTrue(self.call_error(op, request, proof).native_no_side_effects)
            variants = [
                {**proof, 'category_write_version': None},
                {**proof, 'category_write_version': True},
                {**proof, 'receipt_binding': 'b' * 64},
                {**proof, 'op': 'reminder_rename' if op == 'rename' else 'rename'},
                {**proof, 'no_side_effects': False},
                {'ok': False, 'error': 'old helper', 'no_side_effects': True},
            ]
            for bad in variants:
                self.assertFalse(self.call_error(op, request, bad).native_no_side_effects, bad)
            for bad_request in ({'category_write_version': 1, 'receipt_binding': 'invalid'},
                                {'category_write_version': True, 'receipt_binding': BINDING},
                                {'receipt_binding': BINDING}):
                self.assertFalse(self.call_error(op, bad_request, proof).native_no_side_effects)

    def test_legacy_completion_behavior_is_unchanged(self):
        error = self.call_error('reminder_complete', {}, {'ok': False, 'no_side_effects': True})
        self.assertTrue(error.native_no_side_effects)


class CategoryNativeSourceContractTests(unittest.TestCase):
    def test_native_rename_against_fake_store(self):
        source = (NATIVE / 'CalendarBridge.swift').read_text()
        algorithm = source[source.index('var categorySaveStarted = false'):source.index('func recurrenceFields(')]
        failure = source[source.index('func failureResponse('):source.index('if let index = CommandLine.arguments.firstIndex(of: "--pid-file")')]
        harness = r'''''''''
import Foundation
struct BridgeFailure: Error { let message: String; init(_ message: String) { self.message = message } }
func text(_ row:[String:Any], _ key:String) -> String? { row[key] as? String }
func required(_ row:[String:Any], _ key:String) throws -> String { guard let x = text(row,key), !x.isEmpty else { throw BridgeFailure("missing") }; return x }
enum EKEntityType { case event, reminder }
class Source { var sourceIdentifier = "source" }
class EKCalendar { var calendarIdentifier = "id"; var source:Source? = Source(); var title = "old"; var isImmutable = false; var color = "#000000" }
class EKEventStore { var item = EKCalendar(); var fail = false; var saves = 0
    func calendars(for:EKEntityType) -> [EKCalendar] { [item] }
    func saveCalendar(_ calendar:EKCalendar, commit:Bool) throws { saves += 1; if fail { throw BridgeFailure("save failed") } }
}
func row(_ store:EKEventStore) -> [String:Any] { ["id":store.item.calendarIdentifier, "title":store.item.title, "source_id":store.item.source!.sourceIdentifier, "color":store.item.color] }
func calendars(_ store:EKEventStore) -> [[String:Any]] { [row(store)] }
func reminderLists(_ store:EKEventStore) -> [[String:Any]] { [row(store)] }
func setCalendarColor(_ calendar:EKCalendar, _ request:[String:Any]) throws { if let c = text(request,"color") { calendar.color = c } }
func receipt(_ request:[String:Any]) throws -> [String:Any]? { guard let p = text(request,"receipt_file"), URL(fileURLWithPath:p).deletingLastPathComponent().lastPathComponent == "native-receipts" else { throw BridgeFailure("bad path") }; return nil }
func saveReceipt(_ request:[String:Any], _ value:[String:Any]) throws { let p = text(request,"receipt_file")!; try JSONSerialization.data(withJSONObject:value).write(to:URL(fileURLWithPath:p)) }
var reminderCompletionSaveStarted = false
''''''''' + algorithm + failure + r'''''''''
let dir = CommandLine.arguments[1]
try FileManager.default.createDirectory(atPath:dir, withIntermediateDirectories:true)
let binding = String(repeating:"a", count:64)
func request(_ op:String, _ path:String) -> [String:Any] { ["op":op, op == "rename" ? "calendar_id" : "list_id":"id", "title":"new", "color":"#123456", "expected_source_id":"source", "expected_snapshot":["id":"id", "title":"old", "source_id":"source", "color":"#000000"], "category_write_version":1, "receipt_binding":binding, "receipt_file":path] }
for op in ["rename", "reminder_rename"] {
    let entity: EKEntityType = op == "rename" ? .event : .reminder
    let store = EKEventStore(); let path = dir + "/" + op + ".json"; let req = request(op,path)
    var stale = req; stale["expected_source_id"] = "other"; categorySaveStarted = false
    do { _ = try renameCategory(store,stale,entity); fatalError("accepted changed source") } catch { precondition(store.saves == 0); precondition(failureResponse(stale,"x")["no_side_effects"] as? Bool == true) }
    var changed = req; changed["expected_snapshot"] = ["id":"id", "title":"different", "source_id":"source", "color":"#000000"]
    do { _ = try renameCategory(store,changed,entity); fatalError("accepted changed snapshot") } catch { precondition(store.saves == 0); precondition(failureResponse(changed,"x")["no_side_effects"] as? Bool == true) }
    var malformed = req; malformed["receipt_binding"] = "bad"
    do { _ = try renameCategory(store,malformed,entity); fatalError("accepted malformed binding") } catch { precondition(store.saves == 0) }
    let response = try renameCategory(store,req,entity)
    let proof = response["category_receipt"] as! [String:Any]
    precondition(store.saves == 1 && proof["op"] as? String == op && proof["binding"] as? String == binding)
    precondition((proof["result"] as! [String:Any])["title"] as? String == "new")
    categorySaveStarted = false
    do { _ = try renameCategory(store,req,entity); fatalError("replayed") } catch { precondition(store.saves == 1); precondition(failureResponse(req,"x")["no_side_effects"] as? Bool == false) }
    let failed = EKEventStore(); failed.fail = true
    let failReq = request(op,dir + "/fail-" + op + ".json"); categorySaveStarted = false
    do { _ = try renameCategory(failed,failReq,entity); fatalError("save succeeded") } catch { precondition(failed.saves == 1); precondition(failureResponse(failReq,"x")["no_side_effects"] as? Bool == false) }
}
'''''''''
        work = ROOT / '.phase7/native-category-test'
        work.mkdir(parents=True, exist_ok=True)
        (work / 'main.swift').write_text(harness)
        subprocess.run(['/usr/bin/swiftc', str(work / 'main.swift'), '-o', str(work / 'fake-category-test')], check=True)
        # Unique test receipt directory is isolated from any real profile or EventKit data.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='case-', dir=work) as tmp:
            subprocess.run([str(work / 'fake-category-test'), str(Path(tmp) / 'native-receipts')], check=True)

    def test_shared_native_rename_conditions_precede_save(self):
        source = (NATIVE / 'CalendarBridge.swift').read_text()
        start = source.index('func renameCategory(')
        end = source.index('func recurrenceFields(', start)
        body = source[start:end]
        save = body.index('try store.saveCalendar(')
        for fragment in ('category_write_version', 'expected_source_id', 'expected_snapshot',
                         'receipt_binding', 'receipt_file', 'categoryReceiptAbsent(request)',
                         'NSDictionary(dictionary:current).isEqual(to:expected)',
                         'categorySaveStarted = true', 'try saveReceipt(request,["intent":true])'):
            self.assertLess(body.index(fragment), save, fragment)
        self.assertGreater(body.index('"category_receipt":proof'), save)
        self.assertIn('if op == "rename" { return try renameCategory(store, request, .event) }', source)
        reminder = (NATIVE / 'ReminderBridge.swift').read_text()
        self.assertIn('if op == "reminder_rename" { return try renameCategory(store, request, .reminder) }', reminder)
        self.assertIn('"category_write_version": 1', source)
        self.assertIn('"category_write_version": 1', reminder)


if __name__ == '__main__':
    unittest.main()

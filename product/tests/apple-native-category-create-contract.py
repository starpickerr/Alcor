#!/usr/bin/env python3
"""Synthetic native category create rejection contracts; never invokes EventKit."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / 'packages/alcor-apple-sync/python/apple_sync/native'
WORK = ROOT / '.phase8/native-create-test'
spec = importlib.util.spec_from_file_location('create_bridge_under_test', ROOT / 'packages/alcor-apple-sync/python/apple_sync/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
BINDING = 'a' * 64


class FakeProcess:
    def __init__(self, response): self.response = response
    def communicate(self, request, timeout): return json.dumps(self.response), ''


class BridgeProofTests(unittest.TestCase):
    def call_error(self, op, request, response):
        client = bridge.AppleCalendarBridge('/synthetic/helper', platform='darwin')
        with patch.object(Path, 'is_file', return_value=True), \
             patch.object(client, '_start', return_value=FakeProcess(response)), \
             patch.object(client, '_finish', return_value=True):
            with self.assertRaises(bridge.BridgeError) as caught: client.call(op, **request)
        return caught.exception.native_no_side_effects

    def test_matching_versioned_proof_only(self):
        for op in ('create_calendar', 'reminder_list_create'):
            request = {'category_create_version': 1, 'source_id': 'source', 'receipt_binding': BINDING, 'receipt_file': '/synthetic/native-receipts/receipt.json'}
            proof = {'ok': False, 'no_side_effects': True, 'category_create_version': 1, 'receipt_binding': BINDING, 'op': op}
            self.assertTrue(self.call_error(op, request, proof))
            for bad in ({**proof, 'category_create_version': True}, {**proof, 'category_create_version': 2},
                        {**proof, 'receipt_binding': 'b'*64}, {**proof, 'op': 'rename'},
                        {**proof, 'no_side_effects': False}, {'ok': False, 'no_side_effects': True}):
                self.assertFalse(self.call_error(op, request, bad), bad)
            for bad in ({**request, 'category_create_version': True}, {**request, 'category_create_version': 2},
                        {**request, 'receipt_binding': 'invalid'}, {**request, 'source_id': ''},
                        {**request, 'receipt_file': '/synthetic/other/receipt.json'},
                        {k:v for k,v in request.items() if k != 'category_create_version'}):
                self.assertFalse(self.call_error(op, bad, proof), bad)


class NativeCreateTests(unittest.TestCase):
    def test_real_algorithm_with_fake_store(self):
        source = (NATIVE / 'CalendarBridge.swift').read_text()
        receipt = source[source.index('func receipt('):source.index('func validateBoundWriteRequest(')]
        proof = source[source.index('var categorySaveStarted = false'):source.index('func renameCategory(')]
        create = source[source.index('func createCalendar('):source.index('func calendars(')]
        failure = source[source.index('func failureResponse('):source.index('if let index = CommandLine.arguments.firstIndex(of: "--pid-file")')]
        harness = r'''
import Foundation
import CoreFoundation
import Darwin
struct BridgeFailure: Error { let message:String; init(_ message:String) { self.message = message } }
func text(_ row:[String:Any], _ key:String) -> String? { row[key] as? String }
func required(_ row:[String:Any], _ key:String) throws -> String { guard let s = text(row,key), !s.isEmpty else { throw BridgeFailure("missing") }; return s }
enum EKEntityType { case event, reminder }
enum SourceType { case local, other }
class Source { var sourceIdentifier = "source"; var sourceType = SourceType.local; var title = "Account" }
class EKCalendar { var calendarIdentifier = "new-id"; var source = Source(); var title = ""; var allowsContentModifications = true; var isImmutable = false
    init(for:EKEntityType, eventStore:EKEventStore) {} }
class EKEventStore { var sources = [Source()]; var saves = 0; var fail = false; var existing:[EKCalendar] = []
    var defaultCalendarForNewEvents:EKCalendar? { nil }
    func defaultCalendarForNewReminders() -> EKCalendar? { nil }
    func calendars(for:EKEntityType) -> [EKCalendar] { existing }
    func saveCalendar(_ c:EKCalendar, commit:Bool) throws { saves += 1; if fail { throw BridgeFailure("save failed") }; existing.append(c) }
}
func calendarColor(_ c:EKCalendar) -> String { "#123456" }
func setCalendarColor(_ c:EKCalendar, _ r:[String:Any]) throws { if text(r,"color") == "bad" { throw BridgeFailure("bad color") } }
var reminderCompletionSaveStarted = false
''' + receipt + proof + create + failure + r'''
let dir = CommandLine.arguments[1]
try FileManager.default.createDirectory(atPath:dir, withIntermediateDirectories:true)
let binding = String(repeating:"a", count:64)
func req(_ op:String, _ path:String) -> [String:Any] { ["op":op, "title":"New", "source_id":"source", "category_create_version":1, "receipt_binding":binding, "receipt_file":path] }
func safe(_ r:[String:Any]) -> Bool { failureResponse(r,"x")["no_side_effects"] as? Bool == true }
for op in ["create_calendar", "reminder_list_create"] {
    let entity:EKEntityType = op == "create_calendar" ? .event : .reminder
    let p = dir + "/" + op + ".json"; let r = req(op,p); let store = EKEventStore()
    var unavailable = r; unavailable["source_id"] = "missing"; categoryCreateSaveStarted = false
    do { _ = try createCalendar(store,entity,unavailable); fatalError("accepted source") } catch { precondition(store.saves == 0 && safe(unavailable)) }
    var invalid = r; invalid["color"] = "bad"; categoryCreateSaveStarted = false
    do { _ = try createCalendar(store,entity,invalid); fatalError("accepted color") } catch { precondition(store.saves == 0 && safe(invalid)) }
    for contents in ["{}", "{\"binding\":\"wrong\"}", "{bad", "{\"binding\":\"" + binding + "\",\"intent\":true}"] {
        try contents.write(toFile:p, atomically:true, encoding:.utf8); categoryCreateSaveStarted = false
        do { _ = try createCalendar(store,entity,r); fatalError("accepted existing receipt") } catch { precondition(store.saves == 0 && !safe(r)) }
        try FileManager.default.removeItem(atPath:p)
    }
    let unknown = req(op, dir + "/missing/native-receipts/receipt.json"); categoryCreateSaveStarted = false
    do { _ = try createCalendar(store,entity,unknown); fatalError("accepted unreadable directory") } catch { precondition(store.saves == 0 && !safe(unknown)) }
    let failReq = req(op,dir + "/failed-" + op + ".json"); let failing = EKEventStore(); failing.fail = true; categoryCreateSaveStarted = false
    do { _ = try createCalendar(failing,entity,failReq); fatalError("save succeeded") } catch { precondition(failing.saves == 1 && !safe(failReq)) }
    try FileManager.default.removeItem(atPath:text(failReq,"receipt_file")!); precondition(!safe(failReq))
    categoryCreateSaveStarted = false
    let saved = try createCalendar(store,entity,r); precondition(store.saves == 1 && text(saved,"id") == "new-id")
    let disk = try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:p))) as! [String:Any]
    precondition(text(disk,"binding") == binding && (disk["saved"] as? [String:Any])?["id"] as? String == "new-id")
    categoryCreateSaveStarted = false
    do { _ = try createCalendar(store,entity,r); fatalError("replayed success") } catch { precondition(store.saves == 1 && !safe(r)) }
    var malformed = req(op,dir + "/invalid-" + op); malformed["category_create_version"] = true; categoryCreateSaveStarted = false
    do { _ = try createCalendar(store,entity,malformed); fatalError("accepted bool version") } catch { precondition(!safe(malformed)) }
}
'''
        WORK.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='case-', dir=WORK) as tmp:
            tmp = Path(tmp)
            (tmp / 'main.swift').write_text(harness)
            subprocess.run(['/usr/bin/swiftc', str(tmp / 'main.swift'), '-o', str(tmp / 'test')], check=True)
            subprocess.run([str(tmp / 'test'), str(tmp / 'native-receipts')], check=True)

    def test_status_and_save_order(self):
        source = (NATIVE / 'CalendarBridge.swift').read_text()
        body = source[source.index('func createCalendar('):source.index('func calendars(')]
        save = body.index('try store.saveCalendar(')
        for value in ('validCategoryCreateRequest(request)', 'categoryReceiptAbsent(request)', 'try saveCategoryCreateIntent(request)', 'categoryCreateSaveStarted = true'):
            self.assertLess(body.index(value), save)
        self.assertIn('"category_create_version": 1', source)
        self.assertIn('"category_create_version": 1', (NATIVE / 'ReminderBridge.swift').read_text())

if __name__ == '__main__': unittest.main()

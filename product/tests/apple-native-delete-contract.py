"""Conditional deletion receipts with fake processes and Foundation only; no EventKit."""
import importlib.util,json,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2];NATIVE=ROOT/'packages/alcor-apple-sync/python/apple_sync/native'
spec=importlib.util.spec_from_file_location('delete_bridge',ROOT/'packages/alcor-apple-sync/python/apple_sync/bridge.py');bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
class Process:
    def __init__(self,response):self.response=response
    def communicate(self,request,timeout):return json.dumps(self.response),''
class NativeDelete(unittest.TestCase):
    def test_only_bound_quiesced_rejection_proves_not_sent(self):
        for op in ('delete','reminder_delete','delete_calendar','reminder_list_delete'):
            payload={'delete_write_version':1,'receipt_binding':'a'*64,'receipt_file':'/synthetic/native-receipts/delete.json','require_match':True,'expected_source_id':'source','expected_snapshot':{'id':'target'}}
            proof={'ok':False,'error':'synthetic pre-effect refusal','delete_write_version':1,'receipt_binding':'a'*64,'op':op,'no_side_effects':True}
            for response,quiesced,want in [(proof,True,True),(proof,False,False),({**proof,'receipt_binding':'b'*64},True,False),({**proof,'op':'upsert'},True,False),({**proof,'delete_write_version':True},True,False),({**proof,'no_side_effects':False},True,False),({'ok':False,'no_side_effects':True},True,False)]:
                client=bridge.AppleCalendarBridge('/synthetic/helper',platform='darwin')
                with patch.object(Path,'is_file',return_value=True),patch.object(client,'_start',return_value=Process(response)),patch.object(client,'_finish',return_value=quiesced):
                    with self.assertRaises(bridge.BridgeError) as error:client.call(op,**payload)
                self.assertEqual(error.exception.native_no_side_effects,want)
    def test_native_receipt_code_rejects_missing_contract_and_never_overwrites_unknown(self):
        source=(NATIVE/'CalendarBridge.swift').read_text()
        receipt=source[source.index('func receipt('):source.index('func validateBoundWriteRequest(')]
        category=source[source.index('func categoryReceiptAbsent('):source.index('// Only versioned creates')]
        delete=source[source.index('// One private Host-approved delete.'):source.index('func renameCategory(')]
        harness='''import Foundation\nimport CoreFoundation\nimport Darwin\nstruct BridgeFailure:Error { let message:String;init(_ message:String){self.message=message} }\nfunc text(_ row:[String:Any],_ key:String)->String? { row[key] as? String }\nfunc required(_ row:[String:Any],_ key:String)throws->String { guard let v=text(row,key),!v.isEmpty else {throw BridgeFailure("missing")};return v }\n'''
        checks=r'''
let root=URL(fileURLWithPath:CommandLine.arguments[1]).appendingPathComponent("native-receipts")
try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
for (index,op) in ["delete","reminder_delete","delete_calendar","reminder_list_delete"].enumerated() {
    let request:[String:Any] = ["op":op,"delete_write_version":1,"receipt_binding":String(repeating:"a",count:64),"receipt_file":root.appendingPathComponent("\(index).json").path,"require_match":true,"expected_source_id":"source","expected_snapshot":["id":"target"],"series":false]
    assert(validDeleteRequest(request));assert(categoryReceiptAbsent(request))
    for key in ["receipt_binding","receipt_file","expected_source_id","expected_snapshot","require_match","delete_write_version"] {
        var invalid=request;invalid.removeValue(forKey:key);assert(!validDeleteRequest(invalid))
    }
    var invalid=request;invalid["delete_write_version"]=true;assert(!validDeleteRequest(invalid))
    deleteSaveStarted=false;try beginDelete(request);assert(deleteSaveStarted)
    let intent=try receipt(request)!;assert(intent["intent"] as? Bool == true)
    do {try beginDelete(request);fatalError("unknown attempt overwritten")} catch {}
    let stillIntent=try receipt(request)!;assert(stillIntent["intent"] as? Bool == true)
    let result=try finishDelete(request);assert(result["deleted"] as? Bool == true)
    let proof=result["delete_receipt"] as! [String:Any]
    assert(proof["binding"] as? String == String(repeating:"a",count:64));assert(proof["op"] as? String == op)
    assert(proof["source_id"] as? String == "source");assert(proof["series"] as? Bool == false)
    let saved=try receipt(request)!;assert(saved["deleted"] as? Bool == true)
    do {try beginDelete(request);fatalError("completed attempt repeated")} catch {}
}
print("PASS: exact request fields, exclusive intent, unknown/completed no overwrite, durable target-bound receipts; no EventKit")
'''
        with tempfile.TemporaryDirectory(prefix='native-delete-') as tmp:
            root=Path(tmp);(root/'main.swift').write_text(harness+receipt+category+delete+checks)
            subprocess.run(['/usr/bin/swiftc',str(root/'main.swift'),'-o',str(root/'test')],check=True)
            subprocess.run([str(root/'test'),str(root)],check=True)

if __name__=='__main__':unittest.main(verbosity=2)

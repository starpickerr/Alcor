import AppKit
import Foundation

func reply(_ operation: String, _ outcome: String) {
    let value: [String: Any] = ["version": 1, "operation_id": operation, "outcome": outcome]
    if let data = try? JSONSerialization.data(withJSONObject: value), let text = String(data: data, encoding: .utf8) { print(text) }
}

// No EventKit, account access or write opcode exists in this confirmation process.
let data = FileHandle.standardInput.readDataToEndOfFile()
guard data.count <= 500_000,
      let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      request["version"] as? Int == 1,
      let operation = request["operation_id"] as? String,
      operation.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil,
      let detail = request["detail"] as? String, !detail.isEmpty, detail.count <= 120_000 else {
    reply("", "unavailable")
    exit(0)
}
let kind = request["kind"] as? String ?? "delete"
guard ["delete", "apple-update"].contains(kind), request["kind"] == nil || request["kind"] is String else {
    reply(operation, "unavailable")
    exit(0)
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let alert = NSAlert()
alert.alertStyle = .warning
alert.messageText = kind == "apple-update" ? "Alcor 本次 Apple 更新确认" : "Alcor 本次删除确认"
alert.informativeText = kind == "apple-update" ? "请核对发送到 Apple 的对象和内容。取消或关闭窗口不会执行更新。" : "请核对本次对象和范围。取消或关闭窗口不会执行删除。"
alert.addButton(withTitle: "取消")
alert.addButton(withTitle: "确认执行")
alert.buttons[0].keyEquivalent = "\r"
alert.buttons[1].keyEquivalent = ""
let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 600, height: 320))
scroll.hasVerticalScroller = true
scroll.borderType = .bezelBorder
let text = NSTextView(frame: scroll.bounds)
text.isEditable = false
text.isSelectable = true
text.font = .systemFont(ofSize: NSFont.systemFontSize)
text.textContainerInset = NSSize(width: 8, height: 8)
text.isVerticallyResizable = true
text.isHorizontallyResizable = false
text.autoresizingMask = [.width]
text.textContainer?.widthTracksTextView = true
text.string = detail
text.setAccessibilityLabel(kind == "apple-update" ? "本次 Apple 更新对象、内容和影响" : "本次删除对象、范围和影响")
scroll.documentView = text
alert.accessoryView = scroll
var expired = false
let timer = Timer(timeInterval: 240, repeats: false) { _ in
    expired = true
    app.abortModal()
    alert.window.orderOut(nil)
}
RunLoop.main.add(timer, forMode: .modalPanel)
app.finishLaunching()
app.activate(ignoringOtherApps: false)
let began = ProcessInfo.processInfo.systemUptime
func diagnostic(_ values: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: values), let text = String(data: data, encoding: .utf8) {
        FileHandle.standardError.write(Data((text + "\n").utf8))
    }
}
diagnostic(["event": "ready", "time": ISO8601DateFormatter().string(from: Date()),
            "default_button": alert.window.defaultButtonCell?.title ?? "none",
            "cancel_key": alert.buttons[0].keyEquivalent.unicodeScalars.map { Int($0.value) },
            "confirm_key": alert.buttons[1].keyEquivalent.unicodeScalars.map { Int($0.value) },
            "window_level": alert.window.level.rawValue])
let response = alert.runModal()
diagnostic(["event": "settled", "time": ISO8601DateFormatter().string(from: Date()),
            "modal_response": response.rawValue, "expired": expired,
            "elapsed_ms": Int((ProcessInfo.processInfo.systemUptime - began) * 1000)])
timer.invalidate()
reply(operation, !expired && response == .alertSecondButtonReturn ? "confirmed" : "cancelled")

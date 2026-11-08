import Foundation
import CoreFoundation
import EventKit
import AppKit
import Darwin

setvbuf(stdout, nil, _IONBF, 0)

struct BridgeFailure: Error {
    let message: String
    init(_ message: String) { self.message = message }
}

func emit(_ value: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.fragmentsAllowed, .sortedKeys]),
          let line = String(data: data, encoding: .utf8) else { return }
    print(line)
}

func text(_ object: [String: Any], _ key: String) -> String? {
    object[key] as? String
}

func required(_ object: [String: Any], _ key: String) throws -> String {
    guard let value = text(object, key), !value.isEmpty else { throw BridgeFailure("Missing \(key)") }
    return value
}

let formatter: ISO8601DateFormatter = {
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return f
}()
let fallbackFormatter = ISO8601DateFormatter()

func date(_ value: String?) -> Date? {
    guard let value else { return nil }
    return formatter.date(from: value) ?? fallbackFormatter.date(from: value)
}

func floatingDate(_ instant: Date, _ zone: String?) -> Date {
    var source = Calendar(identifier:.gregorian)
    source.timeZone = TimeZone(identifier:zone ?? "") ?? TimeZone.current
    var parts = source.dateComponents([.year,.month,.day,.hour,.minute,.second],from:instant)
    parts.timeZone = TimeZone.current
    return Calendar(identifier:.gregorian).date(from:parts) ?? instant
}

func iso(_ value: Date?) -> String? {
    value.map { formatter.string(from: $0) }
}

func auth() -> String {
    switch EKEventStore.authorizationStatus(for: .event) {
    case .fullAccess, .authorized: return "full_access"
    case .notDetermined: return "not_determined"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .writeOnly: return "write_only"
    @unknown default: return "unknown"
    }
}

func requireAccess() throws {
    guard auth() == "full_access" else { throw BridgeFailure("Calendar full access is required") }
}


func publicAlarms(_ item: EKCalendarItem) -> [[String: Any]] {
    let rows = (item.alarms ?? []).map { alarm in
        var result: [String: Any] = ["offset": alarm.relativeOffset]
        if let absolute = iso(alarm.absoluteDate) { result["absolute"] = absolute }
        if let location = alarm.structuredLocation {
            var place: [String: Any] = ["title": location.title ?? "", "radius": location.radius]
            if let geo = location.geoLocation { place["latitude"] = geo.coordinate.latitude; place["longitude"] = geo.coordinate.longitude }
            result["location"] = place
            result["proximity"] = alarm.proximity.rawValue
        }
        return result
    }
    func key(_ row: [String:Any]) -> String { String(data:(try? JSONSerialization.data(withJSONObject:row,options:.sortedKeys)) ?? Data(),encoding:.utf8) ?? "" }
    return rows.sorted { key($0)<key($1) }
}

func applyPublicFields(_ item: EKCalendarItem, _ request: [String: Any]) throws {
    if request.keys.contains("url") {
        let value = text(request,"url") ?? ""
        guard value.count <= 8192, !value.lowercased().hasPrefix("javascript:"), !value.lowercased().hasPrefix("data:") else { throw BridgeFailure("Invalid URL") }
        item.url = value.isEmpty ? nil : URL(string:value)
    }
    if let rows = request["alarms"] as? [[String: Any]] {
        guard rows.count <= 50 else { throw BridgeFailure("Too many alarms") }
        item.alarms = try rows.map { row in
            if row["location"] != nil { throw BridgeFailure("Location alarms are preserved read only") }
            if let value = text(row,"absolute") {
                guard let moment = date(value) else { throw BridgeFailure("Invalid alarm date") }
                return EKAlarm(absoluteDate:moment)
            }
            guard let offset = row["offset"] as? Double, offset.isFinite else { throw BridgeFailure("Invalid alarm offset") }
            return EKAlarm(relativeOffset:offset)
        }
    }
}

// Record intent before EventKit commits so an interrupted create is never blindly duplicated.
func receipt(_ request: [String: Any]) throws -> [String: Any]? {
    if text(request,"receipt_binding") != nil && text(request,"receipt_file") == nil { throw BridgeFailure("Bound receipt path is required") }
    guard let path = text(request,"receipt_file") else { return nil }
    guard URL(fileURLWithPath:path).deletingLastPathComponent().lastPathComponent == "native-receipts" else { throw BridgeFailure("Invalid receipt path") }
    let binding = text(request, "receipt_binding")
    if let binding, (binding.count != 64 || !binding.unicodeScalars.allSatisfy({ CharacterSet(charactersIn:"0123456789abcdef").contains($0) })) {
        throw BridgeFailure("Invalid receipt binding")
    }
    guard FileManager.default.fileExists(atPath:path) else { return nil }
    guard let value = try JSONSerialization.jsonObject(with:Data(contentsOf:URL(fileURLWithPath:path))) as? [String:Any] else { throw BridgeFailure("Invalid operation receipt") }
    if let binding, text(value,"binding") != binding { throw BridgeFailure("Operation receipt does not match the approved write") }
    return value
}
func saveReceipt(_ request: [String: Any], _ value: [String: Any]) throws {
    guard let path = text(request,"receipt_file") else { return }
    _ = try receipt(request)
    var stored = value
    if let binding = text(request,"receipt_binding") { stored["binding"] = binding }
    let data = try JSONSerialization.data(withJSONObject:stored)
    try data.write(to:URL(fileURLWithPath:path),options:.atomic)
    try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:path)
}
func receiptLookup(_ request: [String: Any], _ identity: String) throws -> [String: Any] {
    guard let saved = try receipt(request) else { return request }
    guard let row = saved["saved"] as? [String:Any], let id = text(row,identity) else { throw BridgeFailure("Apple write was interrupted; review the native item before retrying this create") }
    var lookup = request
    lookup[identity] = id
    lookup["external_id"] = row["external_id"]
    lookup["occurrence_date"] = row["occurrence_date"]
    return lookup
}

func validateBoundWriteRequest(_ request: [String: Any]) throws {
    guard request.keys.contains("receipt_binding") else { return } // Existing non-transfer flows retain their behavior.
    guard let binding = text(request,"receipt_binding"), binding.count == 64,
          binding.unicodeScalars.allSatisfy({ CharacterSet(charactersIn:"0123456789abcdef").contains($0) }),
          let path = text(request,"receipt_file"), !path.isEmpty,
          let source = text(request,"expected_source_id"), !source.isEmpty,
          request["expected_destination_snapshot"] is [String:Any] else {
        throw BridgeFailure("Approved write is missing its receipt or destination conditions")
    }
    let expectedExisting = request["expected_snapshot"] is [String:Any]
    let expectedAbsent = request["expected_absent"] as? Bool == true
    guard expectedExisting != expectedAbsent else { throw BridgeFailure("Approved write must bind one target state") }
}

// A process handles one request. Once save starts, even a thrown error has an unknown outcome.
var categorySaveStarted = false
var categoryCreateSaveStarted = false

func categoryReceiptAbsent(_ request: [String: Any]) -> Bool {
    guard let path = text(request,"receipt_file"), !path.isEmpty else { return false }
    let url = URL(fileURLWithPath:path)
    let parent = url.deletingLastPathComponent()
    guard parent.lastPathComponent == "native-receipts", !url.lastPathComponent.isEmpty,
          let names = try? FileManager.default.contentsOfDirectory(atPath:parent.path) else { return false }
    return !names.contains(url.lastPathComponent)
}

// Only versioned creates with a complete binding can produce a safe rejection proof.
func validCategoryCreateRequest(_ request: [String: Any]) -> Bool {
    guard let version = request["category_create_version"],
          CFGetTypeID(version as CFTypeRef) != CFBooleanGetTypeID(),
          version as? Int == 1,
          let source = text(request,"source_id"), !source.isEmpty,
          let binding = text(request,"receipt_binding"), binding.count == 64,
          binding.unicodeScalars.allSatisfy({ CharacterSet(charactersIn:"0123456789abcdef").contains($0) }),
          let path = text(request,"receipt_file"), !path.isEmpty else { return false }
    let url = URL(fileURLWithPath:path)
    return url.deletingLastPathComponent().lastPathComponent == "native-receipts" && !url.lastPathComponent.isEmpty
}

func saveCategoryCreateIntent(_ request: [String: Any]) throws {
    // O_EXCL prevents a concurrent or historical receipt from being overwritten.
    guard let path = text(request,"receipt_file"), let binding = text(request,"receipt_binding") else {
        throw BridgeFailure("Category create receipt is missing")
    }
    categoryCreateSaveStarted = true // An attempted receipt write may have an uncertain outcome.
    let descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
    guard descriptor >= 0 else { throw BridgeFailure("Category create receipt could not be created safely") }
    let handle = FileHandle(fileDescriptor:descriptor, closeOnDealloc:true)
    let data = try JSONSerialization.data(withJSONObject:["intent":true, "binding":binding])
    try handle.write(contentsOf:data)
    try handle.close()
}

// One private Host-approved delete. The receipt binds data, not human authority.
var deleteSaveStarted = false
func validDeleteRequest(_ request: [String: Any]) -> Bool {
    guard let version = request["delete_write_version"], CFGetTypeID(version as CFTypeRef) != CFBooleanGetTypeID(), version as? Int == 1,
          request["require_match"] as? Bool == true, request["expected_snapshot"] is [String:Any],
          let source = text(request,"expected_source_id"), !source.isEmpty,
          let binding = text(request,"receipt_binding"), binding.count == 64,
          binding.unicodeScalars.allSatisfy({CharacterSet(charactersIn:"0123456789abcdef").contains($0)}),
          let path = text(request,"receipt_file"), URL(fileURLWithPath:path).deletingLastPathComponent().lastPathComponent == "native-receipts" else { return false }
    return true
}
func beginDelete(_ request: [String:Any]) throws {
    guard validDeleteRequest(request), categoryReceiptAbsent(request) else { throw BridgeFailure("Delete receipt is missing, already exists or is uncertain") }
    deleteSaveStarted = true
    let path = try required(request,"receipt_file")
    let descriptor = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, mode_t(0o600))
    guard descriptor >= 0 else { throw BridgeFailure("Delete intent could not be recorded safely") }
    let handle = FileHandle(fileDescriptor:descriptor,closeOnDealloc:true)
    try handle.write(contentsOf:JSONSerialization.data(withJSONObject:["binding":try required(request,"receipt_binding"),"intent":true]))
    try handle.synchronize();try handle.close()
}
func finishDelete(_ request: [String:Any]) throws -> [String:Any] {
    let proof:[String:Any] = ["version":1,"binding":try required(request,"receipt_binding"),"op":try required(request,"op"),
                            "source_id":try required(request,"expected_source_id"),"expected_snapshot":request["expected_snapshot"]!,"series":request["series"] as? Bool ?? false]
    let result:[String:Any] = ["deleted":true,"delete_receipt":proof]
    try saveReceipt(request,result)
    return result
}

func renameCategory(_ store: EKEventStore, _ request: [String: Any], _ entity: EKEntityType) throws -> [String: Any] {
    let op = try required(request, "op")
    let key = entity == .event ? "calendar_id" : "list_id"
    let targetID = try required(request, key)
    let sourceID = try required(request, "expected_source_id")
    let title = try required(request, "title")
    guard request["category_write_version"] as? Int == 1,
          let expected = request["expected_snapshot"] as? [String: Any],
          let binding = text(request, "receipt_binding"), binding.count == 64,
          binding.unicodeScalars.allSatisfy({ CharacterSet(charactersIn:"0123456789abcdef").contains($0) }),
          let path = text(request, "receipt_file"), !path.isEmpty else {
        throw BridgeFailure("Category write is missing its conditions or receipt")
    }
    if request.keys.contains("color") && text(request, "color") == nil { throw BridgeFailure("Invalid calendar color") }
    // A previous receipt may represent an already committed or uncertain write.
    if !categoryReceiptAbsent(request) {
        categorySaveStarted = true
        throw BridgeFailure("Category write already has a receipt; review before retrying")
    }
    _ = try receipt(request) // Validate receipt location and binding before mutation.
    guard let calendar = store.calendars(for:entity).first(where: { $0.calendarIdentifier == targetID }) else {
        throw BridgeFailure("Category is unavailable")
    }
    guard calendar.source?.sourceIdentifier == sourceID else { throw BridgeFailure("Category source changed; write was not applied") }
    let rows = entity == .event ? calendars(store) : reminderLists(store)
    guard let current = rows.first(where: { ($0["id"] as? String) == targetID }),
          NSDictionary(dictionary:current).isEqual(to:expected) else {
        throw BridgeFailure("Category changed; write was not applied")
    }
    guard !calendar.isImmutable else { throw BridgeFailure("Category properties are immutable") }
    calendar.title = title
    try setCalendarColor(calendar, request)
    try saveReceipt(request,["intent":true])
    categorySaveStarted = true
    try store.saveCalendar(calendar,commit:true)
    let savedRows = entity == .event ? calendars(store) : reminderLists(store)
    guard let result = savedRows.first(where: { ($0["id"] as? String) == targetID }) else {
        throw BridgeFailure("Saved category is unavailable")
    }
    let proof: [String:Any] = ["version":1, "binding":binding, "op":op, "target_id":targetID,
                              "source_id":sourceID, "expected_snapshot":expected, "title":title,
                              "color":request["color"] ?? NSNull(), "result":result]
    try saveReceipt(request,["category_receipt":proof])
    return [entity == .event ? "calendar" : "list": result, "category_receipt":proof]
}

func recurrenceFields(_ rules: [EKRecurrenceRule]) -> [String: Any] {
    guard let rule = rules.first else {
        return ["frequency": "none", "days": [], "until": NSNull(), "interval": 1,
                "count": 0, "rule_count": 0,
                "month_days": [], "months": [], "week_numbers": [], "year_days": [], "set_positions": []]
    }
    let frequency: String
    switch rule.frequency {
    case .daily: frequency = "daily"
    case .weekly: frequency = "weekly"
    case .monthly: frequency = "monthly"
    case .yearly: frequency = "yearly"
    @unknown default: frequency = "unsupported"
    }
    let days = (rule.daysOfTheWeek ?? []).map { ($0.dayOfTheWeek.rawValue + 5) % 7 }
    return ["frequency": frequency, "days": days, "weekday_ordinals": (rule.daysOfTheWeek ?? []).map { ["day":($0.dayOfTheWeek.rawValue + 5) % 7,"week":$0.weekNumber] }, "until": iso(rule.recurrenceEnd?.endDate) ?? NSNull(),
            "interval": rule.interval, "count": rule.recurrenceEnd?.occurrenceCount ?? 0,
            "rule_count": rules.count,
            "month_days": (rule.daysOfTheMonth ?? []).map { $0.intValue },
            "months": (rule.monthsOfTheYear ?? []).map { $0.intValue },
            "week_numbers": (rule.weeksOfTheYear ?? []).map { $0.intValue },
            "year_days": (rule.daysOfTheYear ?? []).map { $0.intValue },
            "set_positions": (rule.setPositions ?? []).map { $0.intValue }]
}

func recurrence(_ event: EKEvent) -> [String: Any] { recurrenceFields(event.recurrenceRules ?? []) }

// Historical URLs are read only identities. New writes never create a marker.
func legacyMarker(_ url: String, _ kind: String) -> String {
    let prefix = "stella://\(kind)/"
    return url.hasPrefix(prefix) ? String(url.dropFirst(prefix.count)) : ""
}

func serialize(_ event: EKEvent) -> [String: Any] {
    let url = event.url?.absoluteString ?? ""
    let marker = legacyMarker(url, "calendar")
    return [
        "calendar_id": event.calendar.calendarIdentifier,
        "source_id": event.calendar.source?.sourceIdentifier ?? "",
        "event_id": event.eventIdentifier ?? "",
        "external_id": event.calendarItemExternalIdentifier ?? "",
        "occurrence_date": iso(event.occurrenceDate) ?? iso(event.startDate) ?? "",
        "alcor_id": marker, "url": marker.isEmpty ? url : "", "alarms": publicAlarms(event),
        "floating": event.timeZone == nil, "series_key": event.calendarItemExternalIdentifier ?? event.eventIdentifier ?? "",
        "attendees": (event.attendees ?? []).map { ["name":$0.name ?? "", "url":$0.url.absoluteString,"status":$0.participantStatus.rawValue,"role":$0.participantRole.rawValue,"type":$0.participantType.rawValue,"self":$0.isCurrentUser] },
        "title": event.title ?? "", "description": event.notes ?? "", "location": event.location ?? "",
        "start_at": iso(event.startDate) ?? "", "end_at": iso(event.endDate) ?? "",
        "all_day": event.isAllDay, "timezone": event.timeZone?.identifier ?? TimeZone.current.identifier,
        "recurrence": recurrence(event), "recurrence_rules": (event.recurrenceRules ?? []).map { recurrenceFields([$0]) }, "is_detached": event.isDetached,
        "last_modified": iso(event.lastModifiedDate) ?? "",
        "writable": event.calendar.allowsContentModifications,
    ]
}

func calendarColor(_ calendar: EKCalendar) -> String {
    guard let color = NSColor(cgColor: calendar.cgColor)?.usingColorSpace(.deviceRGB) else { return "#6B8AF7" }
    return String(format: "#%02X%02X%02X", Int(round(color.redComponent * 255)), Int(round(color.greenComponent * 255)), Int(round(color.blueComponent * 255)))
}

func setCalendarColor(_ calendar: EKCalendar, _ request: [String: Any]) throws {
    guard let hex = text(request, "color") else { return }
    guard hex.count == 7, hex.first == "#", let number = UInt32(hex.dropFirst(), radix: 16) else {
        throw BridgeFailure("Invalid calendar color")
    }
    calendar.cgColor = NSColor(srgbRed: CGFloat((number >> 16) & 255) / 255,
                              green: CGFloat((number >> 8) & 255) / 255,
                              blue: CGFloat(number & 255) / 255, alpha: 1).cgColor
}

func createCalendar(_ store: EKEventStore, _ entity: EKEntityType, _ request: [String: Any]) throws -> [String: Any] {
    if request.keys.contains("category_create_version") {
        guard validCategoryCreateRequest(request) else { throw BridgeFailure("Category create is missing its source or receipt binding") }
        // Never replay a create with a pre-existing or unreadable receipt state.
        if !categoryReceiptAbsent(request) {
            categoryCreateSaveStarted = true
            throw BridgeFailure("Category create already has a receipt or its state is unavailable; review before retrying")
        }
        _ = try receipt(request)
    }
    let title = try required(request, "title")
    let preferred = entity == .event ? store.defaultCalendarForNewEvents : store.defaultCalendarForNewReminders()
    let requestedSource = text(request, "source_id")
    guard let source = requestedSource == nil ? (preferred?.source ?? store.sources.first(where: { $0.sourceType == .local })) : store.sources.first(where: { $0.sourceIdentifier == requestedSource }) else {
        throw BridgeFailure("Selected Apple account is unavailable")
    }
    if let saved = try receipt(request) {
        if request.keys.contains("category_create_version") {
            categoryCreateSaveStarted = true
            throw BridgeFailure("Category create already has a receipt; review before retrying")
        }
        guard let row = saved["saved"] as? [String:Any], let identity = text(row,"id"),
              let existing = store.calendars(for:entity).first(where: { $0.calendarIdentifier == identity && $0.source.sourceIdentifier == source.sourceIdentifier }) else { throw BridgeFailure("Calendar create was interrupted; review the source before retrying") }
        return ["id":existing.calendarIdentifier,"title":existing.title,"color":calendarColor(existing),"writable":existing.allowsContentModifications,"immutable":existing.isImmutable,"source_id":source.sourceIdentifier,"source":source.title]
    }
    let calendar = EKCalendar(for: entity, eventStore: store)
    calendar.title = title
    calendar.source = source
    try setCalendarColor(calendar, request)
    if request.keys.contains("category_create_version") { try saveCategoryCreateIntent(request) }
    else { try saveReceipt(request,["intent":true]) }
    categoryCreateSaveStarted = true
    try store.saveCalendar(calendar,commit:true)
    let result: [String:Any] = ["id":calendar.calendarIdentifier,"title":calendar.title,"color":calendarColor(calendar),"writable":true,"source_id":source.sourceIdentifier,"source":source.title,"immutable":false]
    try saveReceipt(request,["saved":result])
    return result
}

func calendars(_ store: EKEventStore) -> [[String: Any]] {
    store.calendars(for: .event).map { calendar in
        ["id": calendar.calendarIdentifier, "title": calendar.title,
         "source": calendar.source.title, "source_id": calendar.source.sourceIdentifier, "immutable": calendar.isImmutable, "subscribed": calendar.isSubscribed, "color": calendarColor(calendar), "writable": calendar.allowsContentModifications]
    }
}

func scan(_ store: EKEventStore, _ ids: [String], _ start: Date, _ end: Date) -> [String: Any] {
    let all = Dictionary(uniqueKeysWithValues: store.calendars(for: .event).map { ($0.calendarIdentifier, $0) })
    var events: [[String: Any]] = []
    var successful: [String] = []
    var errors: [String] = []
    for id in ids {
        guard let calendar = all[id] else { errors.append(id); continue }
        var rows: [String: EKEvent] = [:]
        for (lower, upper) in eventScanWindows(start, end) {
            let predicate = store.predicateForEvents(withStart: lower, end: upper, calendars: [calendar])
            for event in store.events(matching: predicate) {
                let key = "\(event.eventIdentifier ?? "")|\(iso(event.occurrenceDate ?? event.startDate) ?? "")"
                rows[key] = event
            }
            if rows.count > 20000 { break }
        }
        // A truncated scan must never be interpreted as a complete inventory.
        guard rows.count <= 20000 else { errors.append(id); continue }
        events.append(contentsOf: rows.values.map(serialize))
        successful.append(id)
    }
    return ["events": events, "successful_calendar_ids": successful, "failed_calendar_ids": errors]
}

func sameOccurrence(_ event: EKEvent, _ request: [String: Any]) -> Bool {
    guard let wanted = date(text(request, "occurrence_date")) else { return true }
    return abs((event.occurrenceDate ?? event.startDate).timeIntervalSince(wanted)) < 1
}

func findEvent(_ store: EKEventStore, _ request: [String: Any]) throws -> EKEvent? {
    let calendarID = try text(request, "lookup_calendar_id") ?? required(request, "calendar_id")
    if let eventID = text(request, "event_id"), !eventID.isEmpty,
       let event = store.event(withIdentifier: eventID),
       event.calendar.calendarIdentifier == calendarID, sameOccurrence(event, request) {
        return event
    }
    let marker = text(request, "alcor_id") ?? ""
    let external = text(request, "external_id") ?? ""
    guard !marker.isEmpty || !external.isEmpty else { return nil }
    let start = date(text(request, "scan_start")) ?? Date().addingTimeInterval(-366 * 86400)
    let end = date(text(request, "scan_end")) ?? Date().addingTimeInterval(732 * 86400)
    let inventory = store.calendars(for: .event).first { $0.calendarIdentifier == calendarID }
    guard let inventory else { return nil }
    for (lower, upper) in eventScanWindows(start, end) {
        let rows = store.events(matching: store.predicateForEvents(withStart: lower, end: upper, calendars: [inventory]))
        if let found = rows.first(where: { event in
            if !marker.isEmpty, matchesMarkerOccurrence(
                candidateMarker: event.url?.absoluteString,
                candidateOccurrence: event.occurrenceDate ?? event.startDate,
                requestedMarker: "stella://calendar/\(marker)",
                requestedOccurrence: date(text(request, "occurrence_date"))) { return true }
            return !external.isEmpty && event.calendarItemExternalIdentifier == external && sameOccurrence(event, request)
        }) { return found }
    }
    return nil
}

// This is an observation of the local EventKit store, not an iCloud tombstone.
func lookupEventIdentity(_ store: EKEventStore, _ request: [String: Any]) throws -> [String: Any] {
    let calendarID = try required(request, "calendar_id")
    let sourceID = try required(request, "expected_source_id")
    let eventID = try required(request, "event_id")
    let externalID = try required(request, "external_id")
    var observation: [String:Any] = ["version":1, "status":"unknown", "calendar_id":calendarID,
        "source_id":sourceID, "event_id":eventID, "external_id":externalID, "candidates":[]]
    let before = calendars(store).sorted { ($0["id"] as? String ?? "") < ($1["id"] as? String ?? "") }
    let sources = store.sources.map { $0.sourceIdentifier }.sorted()
    guard let calendar = store.calendars(for:.event).first(where: { $0.calendarIdentifier == calendarID }),
          calendar.source.sourceIdentifier == sourceID, sources.contains(sourceID) else {
        return ["identity_observation":observation]
    }
    // Local/birthday external IDs are only local IDs and are not sync-proof.
    switch calendar.source.sourceType {
    case .calDAV: observation["source_type"] = "caldav"
    case .exchange: observation["source_type"] = "exchange"
    case .mobileMe: observation["source_type"] = "mobileme"
    default: return ["identity_observation":observation]
    }
    let externalMatches = store.calendarItems(withExternalIdentifier:externalID)
    var matches = externalMatches.compactMap { $0 as? EKEvent }
    if let direct = store.event(withIdentifier:eventID), !matches.contains(where: {
        $0.eventIdentifier == direct.eventIdentifier && $0.calendar.calendarIdentifier == direct.calendar.calendarIdentifier
    }) { matches.append(direct) }
    let after = calendars(store).sorted { ($0["id"] as? String ?? "") < ($1["id"] as? String ?? "") }
    guard auth() == "full_access", NSArray(array:before).isEqual(to:after),
          store.sources.map({ $0.sourceIdentifier }).sorted() == sources else {
        return ["identity_observation":observation]
    }
    observation["candidates"] = matches.map(serialize)
    observation["status"] = matches.isEmpty && externalMatches.isEmpty ? "absent" : "present"
    return ["identity_observation":observation]
}

func recurrenceRule(_ payload: [String: Any]) throws -> EKRecurrenceRule? {
    if let native = payload["native_recurrence"] as? [String: Any], !native.isEmpty {
        guard (native["rule_count"] as? Int ?? 1) <= 1 else { throw BridgeFailure("Multiple recurrence rules are read only") }
        let name = text(native,"frequency") ?? "none"
        if name == "none" { return nil }
        let frequencies: [String:EKRecurrenceFrequency] = ["daily":.daily,"weekly":.weekly,"monthly":.monthly,"yearly":.yearly]
        guard let frequency = frequencies[name], let interval = native["interval"] as? Int, (1...999).contains(interval) else { throw BridgeFailure("Invalid native recurrence") }
        var end: EKRecurrenceEnd?
        if let until = date(text(native,"until")) { end = EKRecurrenceEnd(end:until) }
        if let count = native["count"] as? Int, count > 0 { end = EKRecurrenceEnd(occurrenceCount:count) }
        let ordinal = native["weekday_ordinals"] as? [[String:Int]]
        let weekdays = ordinal ?? (native["days"] as? [Int] ?? []).map { ["day":$0,"week":0] }
        let days = weekdays.compactMap { row -> EKRecurrenceDayOfWeek? in
            guard let day = row["day"], (0...6).contains(day), let ekDay = EKWeekday(rawValue:((day+1)%7)+1) else { return nil }
            return EKRecurrenceDayOfWeek(ekDay,weekNumber:row["week"] ?? 0)
        }
        func numbers(_ key:String) -> [NSNumber]? { (native[key] as? [Int]).map { $0.map { NSNumber(value:$0) } } }
        return EKRecurrenceRule(recurrenceWith:frequency,interval:interval,daysOfTheWeek:days.isEmpty ? nil : days,daysOfTheMonth:numbers("month_days"),monthsOfTheYear:numbers("months"),weeksOfTheYear:numbers("week_numbers"),daysOfTheYear:numbers("year_days"),setPositions:numbers("set_positions"),end:end)
    }
    let frequencyName = text(payload, "repeat_frequency") ?? "none"
    if frequencyName == "none" { return nil }
    let frequency: EKRecurrenceFrequency
    switch frequencyName {
    case "daily": frequency = .daily
    case "weekly": frequency = .weekly
    case "monthly": frequency = .monthly
    case "yearly": frequency = .yearly
    default: throw BridgeFailure("Unsupported recurrence")
    }
    let interval = payload["repeat_interval"] as? Int ?? 1
    guard (1...99).contains(interval) else { throw BridgeFailure("Invalid repeat interval") }
    var end: EKRecurrenceEnd? = nil
    if let until = text(payload, "repeat_until"), !until.isEmpty {
        let zone = TimeZone(identifier: text(payload, "repeat_timezone") ?? text(payload, "timezone") ?? "UTC") ?? .gmt
        var parts = DateComponents()
        let numbers = until.split(separator: "-").compactMap { Int($0) }
        if numbers.count == 3 {
            parts.year = numbers[0]; parts.month = numbers[1]; parts.day = numbers[2]
            parts.hour = 23; parts.minute = 59; parts.second = 59; parts.timeZone = zone
            if let last = Calendar(identifier: .gregorian).date(from: parts) { end = EKRecurrenceEnd(end: last) }
        }
    }
    if frequency == .weekly, let days = payload["repeat_days"] as? [Int], !days.isEmpty {
        let ekDays = days.compactMap { EKWeekday(rawValue: (($0 + 1) % 7) + 1) }.map { EKRecurrenceDayOfWeek($0) }
        return EKRecurrenceRule(recurrenceWith: frequency, interval: interval,
            daysOfTheWeek: ekDays, daysOfTheMonth: nil, monthsOfTheYear: nil,
            weeksOfTheYear: nil, daysOfTheYear: nil, setPositions: nil, end: end)
    }
    return EKRecurrenceRule(recurrenceWith: frequency, interval: interval, end: end)
}

func run(_ request: [String: Any]) throws -> [String: Any] {
    let op = try required(request, "op")
    if ["delete", "delete_calendar", "reminder_delete", "reminder_list_delete"].contains(op) && !validDeleteRequest(request) { throw BridgeFailure("Delete requires the private conditional receipt contract") }
    if op == "reminders" || (op.hasPrefix("reminder_") && op != "reminder_watch") {
        return try runReminder(request)
    }
    if op == "status" { return ["supported": true, "authorization": auth(), "watch_control_version": 1, "category_write_version": 1, "category_create_version": 1, "approved_export_version": 1, "delete_write_version": 1, "identity_lookup_version": 1] }
    let store = EKEventStore()
    if op == "request_access" {
        let semaphore = DispatchSemaphore(value: 0)
        var granted = false
        var accessError: Error?
        if #available(macOS 14.0, *) {
            store.requestFullAccessToEvents { result, error in granted = result; accessError = error; semaphore.signal() }
        } else {
            store.requestAccess(to: .event) { result, error in granted = result; accessError = error; semaphore.signal() }
        }
        guard semaphore.wait(timeout: .now() + 120) == .success else { throw BridgeFailure("Calendar permission request timed out") }
        if let accessError { throw BridgeFailure(accessError.localizedDescription) }
        store.reset()
        return ["authorization": auth(), "granted": granted]
    }
    try requireAccess()
    if op == "create_calendar" { return ["calendar": try createCalendar(store, .event, request)] }
    if op == "calendars" { return ["calendars": calendars(store), "source_ids": store.sources.map { $0.sourceIdentifier }, "sources": store.sources.map { ["id":$0.sourceIdentifier,"title":$0.title,"can_create":$0.sourceType != .subscribed && $0.sourceType != .birthdays] }, "default_id":store.defaultCalendarForNewEvents?.calendarIdentifier ?? "", "default_source_id":store.defaultCalendarForNewEvents?.source.sourceIdentifier ?? ""] }
    if op == "events" {
        let ids = request["calendar_ids"] as? [String] ?? []
        guard let start = date(text(request, "start")), let end = date(text(request, "end")), end > start else {
            throw BridgeFailure("Invalid scan window")
        }
        return scan(store, ids, start, end)
    }
    if op == "lookup" {
        return ["event": try findEvent(store, request).map(serialize) ?? NSNull()]
    }
    if op == "lookup_identity" { return try lookupEventIdentity(store, request) }
    if op == "upsert" {
        try validateBoundWriteRequest(request)
        let calendarID = try required(request, "calendar_id")
        guard let calendar = store.calendars(for: .event).first(where: { $0.calendarIdentifier == calendarID }) else {
            throw BridgeFailure("Calendar is unavailable")
        }
        guard calendar.allowsContentModifications else { throw BridgeFailure("Calendar is read only") }
        if let expectedSource = text(request, "expected_source_id"), calendar.source?.sourceIdentifier != expectedSource {
            throw BridgeFailure("Calendar source changed; write was not applied")
        }
        if let expected = request["expected_destination_snapshot"] as? [String: Any] {
            guard let current = calendars(store).first(where: { ($0["id"] as? String) == calendarID }),
                  NSDictionary(dictionary: current).isEqual(to: expected) else { throw BridgeFailure("Calendar changed; write was not applied") }
        }
        let existing = try findEvent(store, receiptLookup(request,"event_id"))
        if text(request,"receipt_binding") != nil, let saved = try receipt(request)?["saved"] as? [String: Any] {
            guard let existing, NSDictionary(dictionary: serialize(existing)).isEqual(to: saved) else {
                throw BridgeFailure("Previously written event changed; review before retrying")
            }
            return ["event":serialize(existing)]
        }
        if let existing, !existing.calendar.allowsContentModifications { throw BridgeFailure("Source is read only") }
        if let expected = request["expected_snapshot"] as? [String: Any] {
            guard let existing, NSDictionary(dictionary: serialize(existing)).isEqual(to: expected) else { throw BridgeFailure("Event changed; write was not applied") }
        }
        if request["expected_absent"] as? Bool == true, existing != nil { throw BridgeFailure("Event already exists; write was not applied") }
        if (text(request, "event_id") ?? "") != "" || (text(request, "external_id") ?? "") != "" {
            guard existing != nil else { throw BridgeFailure("Linked event is unavailable") }
        }
        let event = existing ?? EKEvent(eventStore: store)
        event.calendar = calendar
        guard let start = date(text(request, "start_at")), let end = date(text(request, "end_at")), end >= start,
              ((request["all_day"] as? Bool) != true || end > start) else {
            throw BridgeFailure("Invalid event time")
        }
        event.title = try required(request, "title")
        event.notes = text(request, "description") ?? ""
        event.location = text(request, "location") ?? ""
        event.startDate = request["floating"] as? Bool == true ? floatingDate(start,text(request,"repeat_timezone") ?? text(request,"timezone")) : start
        event.endDate = request["floating"] as? Bool == true ? floatingDate(end,text(request,"repeat_timezone") ?? text(request,"timezone")) : end
        event.isAllDay = request["all_day"] as? Bool ?? false
        event.timeZone = request["floating"] as? Bool == true ? nil : TimeZone(identifier: text(request, "repeat_timezone") ?? text(request, "timezone") ?? "UTC")
        try applyPublicFields(event,request)
        if existing == nil || request["set_recurrence"] as? Bool == true || request["native_recurrence"] != nil {
            event.recurrenceRules = try recurrenceRule(request).map { [$0] }
        }
        let span: EKSpan = request["series"] as? Bool == true ? .futureEvents : .thisEvent
        if existing == nil || text(request,"receipt_binding") != nil { try saveReceipt(request,["intent":true]) }
        try store.save(event, span: span, commit: true)
        let result = serialize(event)
        try saveReceipt(request,["saved":result])
        return ["event": result]
    }
    if op == "rename" { return try renameCategory(store, request, .event) }
    if op == "delete_calendar" {
        let calendarID = try required(request, "calendar_id")
        guard let calendar = store.calendars(for: .event).first(where: { $0.calendarIdentifier == calendarID }) else {
            if request["require_match"] as? Bool == true { throw BridgeFailure("Calendar identity is unavailable; deletion was not applied") }
            return ["deleted": true]
        }
        if let expectedSource = text(request, "expected_source_id"), calendar.source?.sourceIdentifier != expectedSource {
            throw BridgeFailure("Calendar source changed; deletion was not applied")
        }
        if let expected = request["expected_snapshot"] as? [String: Any] {
            guard let current = calendars(store).first(where: { ($0["id"] as? String) == calendarID }),
                  NSDictionary(dictionary: current).isEqual(to: expected) else {
                throw BridgeFailure("Calendar changed; deletion was not applied")
            }
        }
        guard !calendar.isImmutable else { throw BridgeFailure("Calendar cannot be deleted") }
        try beginDelete(request)
        try store.removeCalendar(calendar, commit: true)
        return try finishDelete(request)
    }
    if op == "delete" {
        guard let event = try findEvent(store, request) else {
            if request["require_match"] as? Bool == true { throw BridgeFailure("Occurrence identity is unavailable; deletion was not applied") }
            return ["deleted": true]
        }
        if request["require_exact_identity"] as? Bool == true {
            guard let requestedID = text(request, "event_id"), !requestedID.isEmpty,
                  event.eventIdentifier == requestedID,
                  event.calendar.calendarIdentifier == text(request, "calendar_id") else {
                throw BridgeFailure("Event identity changed; deletion was not applied")
            }
        }
        if let expectedSource = text(request, "expected_source_id"), event.calendar.source?.sourceIdentifier != expectedSource {
            throw BridgeFailure("Event source changed; deletion was not applied")
        }
        if let expected = request["expected_snapshot"] as? [String: Any],
           !NSDictionary(dictionary: serialize(event)).isEqual(to: expected) {
            throw BridgeFailure("Event changed; deletion was not applied")
        }
        guard event.calendar.allowsContentModifications else { throw BridgeFailure("Calendar is read only") }
        try beginDelete(request)
        try store.remove(event, span: request["series"] as? Bool == true ? .futureEvents : .thisEvent, commit: true)
        return try finishDelete(request)
    }
    throw BridgeFailure("Unknown operation")
}

func failureResponse(_ request: [String: Any], _ message: String) -> [String: Any] {
    let op = text(request,"op") ?? ""
    var result: [String:Any] = ["ok":false, "error":message,
                                "no_side_effects":op == "reminder_complete" && !reminderCompletionSaveStarted]
    if ["delete","delete_calendar","reminder_delete","reminder_list_delete"].contains(op) {
        result["no_side_effects"] = validDeleteRequest(request) && !deleteSaveStarted && categoryReceiptAbsent(request)
        result["delete_write_version"] = 1
        result["receipt_binding"] = text(request,"receipt_binding") ?? ""
        result["op"] = op
    }
    if op == "create_calendar" || op == "reminder_list_create" {
        result["no_side_effects"] = validCategoryCreateRequest(request) && !categoryCreateSaveStarted && categoryReceiptAbsent(request)
        result["category_create_version"] = 1
        result["receipt_binding"] = text(request,"receipt_binding") ?? ""
        result["op"] = op
    }
    if op == "rename" || op == "reminder_rename" {
        // Existing receipt means this binding may have already reached EventKit.
        result["no_side_effects"] = !categorySaveStarted && categoryReceiptAbsent(request)
        result["category_write_version"] = 1
        result["receipt_binding"] = text(request,"receipt_binding") ?? ""
        result["op"] = op
    }
    return result
}

if let index = CommandLine.arguments.firstIndex(of: "--pid-file"), index + 1 < CommandLine.arguments.count {
    do { try String(getpid()).write(toFile: CommandLine.arguments[index + 1], atomically: true, encoding: .utf8) }
    catch { exit(1) }
}

guard let line = readLine(), let data = line.data(using: .utf8),
      let request = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
    emit(["ok": false, "error": "Invalid request"])
    exit(1)
}
if text(request, "op") == "watch" || text(request, "op") == "reminder_watch" {
    do {
        if text(request, "op") == "reminder_watch" { try requireReminderAccess() }
        else { try requireAccess() }
        let store = EKEventStore()
        let token = NotificationCenter.default.addObserver(forName: .EKEventStoreChanged, object: store, queue: nil) { _ in
            emit(["changed": true])
        }
        withExtendedLifetime(token) {
            let path = text(request, "watch_control")
            var lease = WatchLease(now: DispatchTime.now().uptimeNanoseconds)
            while true {
                _ = RunLoop.current.run(mode: .default, before: Date(timeIntervalSinceNow: 0.2))
                guard let path else { continue } // Compatibility with older callers.
                let value = try? String(contentsOfFile: path, encoding: .utf8)
                if lease.shouldStop(value, now: DispatchTime.now().uptimeNanoseconds) { break }
            }
        }
    } catch {
        emit(["ok": false, "error": "EventKit full access is required"])
    }
} else {
    do {
        var result = try run(request)
        result["ok"] = true
        emit(result)
    } catch let failure as BridgeFailure {
        emit(failureResponse(request, failure.message))
    } catch {
        emit(failureResponse(request, "EventKit operation failed"))
    }
}

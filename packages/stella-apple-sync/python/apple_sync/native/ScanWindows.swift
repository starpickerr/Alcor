import Foundation

// EventKit silently truncates queries spanning more than four years.
func eventScanWindows(_ start: Date, _ end: Date) -> [(Date, Date)] {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(secondsFromGMT: 0)!
    var cursor = start
    var windows: [(Date, Date)] = []
    while cursor < end {
        let next = min(calendar.date(byAdding: .year, value: 3, to: cursor) ?? end, end)
        windows.append((cursor, next))
        cursor = next
    }
    return windows
}

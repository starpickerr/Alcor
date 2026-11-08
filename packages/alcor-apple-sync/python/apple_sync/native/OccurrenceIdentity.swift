import Foundation

// A watcher exits if its owning worker asks it to stop or stops refreshing its lease.
struct WatchLease {
    private var value: String?
    private var changedAt: UInt64
    private let timeout: UInt64

    init(now: UInt64, timeout: UInt64 = 90_000_000_000) {
        changedAt = now
        self.timeout = timeout
    }

    mutating func shouldStop(_ current: String?, now: UInt64) -> Bool {
        guard let current else { return true }
        if current == "stop" { return true }
        if current != value {
            value = current
            changedAt = now
            return false
        }
        return now >= changedAt && now - changedAt >= timeout
    }
}

func matchesMarkerOccurrence(candidateMarker: String?, candidateOccurrence: Date,
                             requestedMarker: String, requestedOccurrence: Date?) -> Bool {
    guard candidateMarker == requestedMarker else { return false }
    guard let requestedOccurrence else { return true }
    return abs(candidateOccurrence.timeIntervalSince(requestedOccurrence)) < 1
}

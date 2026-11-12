import Foundation
#if os(iOS)
import ActivityKit
import Foundation

struct ArrivalActivityAttributes: ActivityAttributes {
    struct ContentState: Codable, Hashable {
        var distance: String
        var altitude: String
        var status: String
        var updated: String
    }
    var tail: String
    var aircraftType: String
    var startedAt: Date = .now
}
#endif
enum ActivityPolicy {
    static func canStart(_ flight: Arrival, now: Date = .now) -> Bool {
        guard let seen = Format.date(flight.last_seen), now.timeIntervalSince(seen) <= 300,
              seen.timeIntervalSince(now) <= 60 else { return false }
        return flight.status == "inside 10 NM" || flight.status?.hasPrefix("inbound") == true
    }
    static func shouldEnd(_ flight: Arrival?, now: Date = .now) -> Bool {
        guard let flight else { return true }
        return !canStart(flight, now: now)
    }
}

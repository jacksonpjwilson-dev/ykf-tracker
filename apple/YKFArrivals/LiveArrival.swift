import ActivityKit
import Foundation

enum LiveArrival {
    static func content(_ flight: Arrival) -> ActivityContent<ArrivalActivityAttributes.ContentState> {
        ActivityContent(state: .init(distance: flight.distance, altitude: flight.alt ?? "Not reported", status: flight.displayStatus, updated: flight.last_seen ?? ""), staleDate: Format.date(flight.last_seen)?.addingTimeInterval(300))
    }
    static func start(_ flight: Arrival) async throws {
        #if BASIC_APP
        throw ActivityError.basicBuild
        #else
        guard ActivityPolicy.canStart(flight) else { throw ActivityError.noFreshArrival }
        guard ActivityAuthorizationInfo().areActivitiesEnabled else { throw ActivityError.disabled }
        for activity in Activity<ArrivalActivityAttributes>.activities {
            if activity.attributes.tail == flight.tail { await activity.update(content(flight)); return }
        }
        _ = try Activity.request(attributes: ArrivalActivityAttributes(tail: flight.tail, aircraftType: flight.type ?? "Type unknown"), content: content(flight), pushType: nil)
        #endif
    }
    static func update(_ snapshot: Snapshot) async {
        for activity in Activity<ArrivalActivityAttributes>.activities {
            let flight = snapshot.flights.first { $0.tail == activity.attributes.tail }
            if snapshot.active != true || ActivityPolicy.shouldEnd(flight) || Date.now.timeIntervalSince(activity.attributes.startedAt) > 3600 {
                await activity.end(nil, dismissalPolicy: .immediate)
            } else if let flight { await activity.update(content(flight)) }
        }
    }
    enum ActivityError: LocalizedError {
        case noFreshArrival, disabled, basicBuild
        var errorDescription: String? {
            if self == .basicBuild { return "Live Activities require the full app and widget extension." }
            return self == .disabled ? "Enable Live Activities in system settings." : "A fresh backend-reported inbound aircraft is required."
        }
    }
}

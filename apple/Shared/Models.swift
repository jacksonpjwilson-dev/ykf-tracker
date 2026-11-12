import Foundation

struct Arrival: Codable, Identifiable, Hashable {
    var tail: String
    var type: String?
    var eta: String?
    var dist_nm: Double?
    var alt: String?
    var last_seen: String?
    var status: String?
    var scheduled: Bool?
    var note: String?
    var skycare: Bool?
    // Reserved for an authorized, versioned private interface. Public feed omits these.
    var latitude: Double?
    var longitude: Double?
    var position_timestamp: String?
    var heading: Double?
    var groundspeed_kt: Double?
    var vertical_rate_fpm: Double?
    var bearing: Double?
    var source: String?
    var confidence: String?
    var id: String { tail }
    var displayStatus: String {
        status == "landed?" ? "Contact lost • landing unconfirmed" : (status ?? "Unknown")
    }
    var distance: String { Format.distance(dist_nm) }
    var hasPosition: Bool {
        guard let latitude, let longitude, Format.date(position_timestamp) != nil else { return false }
        return (-90...90).contains(latitude) && (-180...180).contains(longitude)
    }
    func positionStale(at now: Date = .now) -> Bool {
        guard let date = Format.date(position_timestamp) else { return true }
        return now.timeIntervalSince(date) > 300 || date.timeIntervalSince(now) > 60
    }
}
struct Shift: Codable, Hashable {
    var id: String?
    var start: String?
    var end: String?
    var window_start: String?
    var window_end: String?
    func contains(_ date: Date) -> Bool {
        guard let start = Format.date(window_start), let end = Format.date(window_end) else { return false }
        return start <= date && date < end
    }
}
struct Feed: Codable { var provider: String?; var ok: Bool? }
struct Snapshot: Codable {
    var generated: String?
    var airport: String?
    var radius_nm: Double?
    var active: Bool?
    var shift: Shift?
    var arrivals_updated: String?
    var arrivals_stale: Bool?
    var feed: Feed?
    var next_window: Shift?
    var rows: [Arrival]?
    var flights: [Arrival] { rows ?? [] }
    func nextExpected(at now: Date = .now) -> Arrival? {
        flights.compactMap { flight -> (Arrival, Date)? in
            guard flight.scheduled == true, let date = Format.scheduledDate(flight.eta, shift: shift), date >= now else { return nil }
            return (flight, date)
        }.min { $0.1 < $1.1 }?.0
    }
    func health(at now: Date = .now) -> String {
        guard let generated = Format.date(generated), let active else { return "Unknown status" }
        if !active {
            if let start = Format.date(next_window?.window_start), start <= now { return "Stale shift information" }
            return "Not on shift • last published \(Format.time(self.generated))"
        }
        guard now.timeIntervalSince(generated) <= 300, generated.timeIntervalSince(now) <= 60 else { return "Stale data" }
        if feed?.ok == false { return "Provider degraded" }
        return feed?.ok == true ? "Monitoring healthy" : "Feed status unknown"
    }
}
struct AlertEvent: Codable, Identifiable, Hashable {
    var id: String
    var alert_type: String?
    var created: String?
    var tail: String?
    var text: String?
    var basis: String?
    var distance_nm: Double?
    var acknowledged: Bool?
}
enum HistoryExport {
    static func csv(_ rows: [(Date, Arrival)]) -> String {
        func quote(_ value: String) -> String { "\"" + value.replacingOccurrences(of: "\"", with: "\"\"") + "\"" }
        let header = "publication_utc,registration,status,scheduled_eta_toronto,distance_nm,last_seen\n"
        return header + rows.map { date, flight in
            [ISO8601DateFormatter().string(from: date), flight.tail, flight.displayStatus, flight.eta ?? "", flight.dist_nm.map(String.init(describing:)) ?? "", flight.last_seen ?? ""].map(quote).joined(separator: ",")
        }.joined(separator: "\n")
    }
}
enum AlertMerge {
    static func unique(_ events: [AlertEvent]) -> [AlertEvent] {
        var seen = Set<String>()
        return events.filter { !$0.id.isEmpty && seen.insert($0.id).inserted }
    }
}
enum Format {
    static func date(_ value: String?) -> Date? {
        guard let value else { return nil }
        let f = ISO8601DateFormatter()
        if let date = f.date(from: value) { return date }
        f.formatOptions.insert(.withFractionalSeconds)
        return f.date(from: value)
    }
    static func scheduledDate(_ eta: String?, shift: Shift?) -> Date? {
        guard let eta, let shift, let start = date(shift.start), let base = date(shift.window_start) else { return nil }
        let pieces = eta.split(separator: ":").compactMap { Int($0) }
        guard pieces.count == 2, (0...23).contains(pieces[0]), (0...59).contains(pieces[1]) else { return nil }
        var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(identifier: "America/Toronto")!
        guard var scheduled = calendar.date(bySettingHour: pieces[0], minute: pieces[1], second: 0, of: start) else { return nil }
        // Same overnight interpretation as core.eta_datetime; this remains a scheduled estimate.
        if scheduled < base.addingTimeInterval(-7200) {
            guard let next = calendar.date(byAdding: .day, value: 1, to: scheduled) else { return nil }
            scheduled = next
        }
        return scheduled
    }
    static func distance(_ value: Double?) -> String {
        guard let value, value.isFinite, value >= 0 else { return "Not reported" }
        return String(format: "%.1f NM", value)
    }
    static func time(_ value: String?) -> String {
        guard let date = date(value) else { return "Not reported" }
        let f = DateFormatter(); f.timeZone = TimeZone(identifier: "America/Toronto")
        f.dateFormat = "MMM d, HH:mm z"; return f.string(from: date)
    }
}
enum Route: Equatable {
    case arrivals, flight(String), health, shifts, alerts
    init?(url: URL) {
        guard url.scheme == "ykf" else { return nil }
        switch url.host {
        case "arrivals": self = .arrivals
        case "flight":
            let tail = url.lastPathComponent
            guard !tail.isEmpty, tail != "/" else { return nil }; self = .flight(tail)
        case "health": self = .health
        case "shifts": self = .shifts
        case "alerts": self = .alerts
        default: return nil
        }
    }
}

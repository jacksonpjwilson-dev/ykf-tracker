import AppIntents
import Foundation

enum ArrivalDestination: String, AppEnum {
    case arrivals, next, nearby, health, shifts, alerts
    static var typeDisplayRepresentation = TypeDisplayRepresentation(name: "YKF view")
    static var caseDisplayRepresentations: [Self: DisplayRepresentation] = [
        .arrivals: "Arrivals", .next: "Next expected aircraft", .nearby: "Within 10 NM", .health: "Tracker status", .shifts: "Next monitoring shift", .alerts: "Recent alerts"
    ]
}
struct ShowArrivalsIntent: AppIntent {
    static var title: LocalizedStringResource = "Show YKF arrivals"
    static var description = IntentDescription("Read the latest cached backend publication. Does not query ADS-B providers.")
    static var openAppWhenRun = true
    @Parameter(title: "View", default: .arrivals) var destination: ArrivalDestination
    func perform() async throws -> some IntentResult & ProvidesDialog & OpensIntent {
        let snapshot = SharedCache.read()
        let flights = snapshot?.flights ?? []
        let text: String
        var route = "arrivals"
        switch destination {
        case .arrivals: text = "\(flights.count) published arrivals."
        case .next:
            let next = snapshot?.nextExpected()
            text = next.map { "\($0.tail), scheduled ETA \($0.eta ?? "not reported") Toronto time." } ?? "No expected aircraft in the cache."
        case .nearby: text = flights.filter { $0.status == "inside 10 NM" }.map(\.tail).joined(separator: ", ").nilIfEmpty ?? "No backend-reported aircraft inside 10 NM in the cache."
        case .health: route = "health"; text = snapshot?.health() ?? "No cached tracker status."
        case .shifts: route = "shifts"; text = "Next monitoring window: \(Format.time(snapshot?.next_window?.window_start))."
        case .alerts: route = "alerts"; text = "Opening received arrival alerts."
        }
        return .result(opensIntent: OpenURLIntent(URL(string: "ykf://\(route)")!), dialog: IntentDialog(stringLiteral: "\(text) Publication: \(Format.time(snapshot?.generated))."))
    }
}
struct OpenAircraftIntent: AppIntent {
    static var title: LocalizedStringResource = "Open tracked YKF aircraft"
    static var openAppWhenRun = true
    @Parameter(title: "Registration") var registration: String
    func perform() async throws -> some IntentResult & OpensIntent {
        let tail = SharedCache.read()?.flights.first { $0.tail.caseInsensitiveCompare(registration) == .orderedSame }?.tail
        let route = tail.map { "ykf://flight/\($0.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? "")" } ?? "ykf://arrivals"
        return .result(opensIntent: OpenURLIntent(URL(string: route)!))
    }
}
struct YKFShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: ShowArrivalsIntent(), phrases: ["Show arrivals in \(.applicationName)", "Check \(.applicationName)"], shortTitle: "YKF arrivals", systemImageName: "airplane.arrival")
    }
}
private extension String { var nilIfEmpty: String? { isEmpty ? nil : self } }

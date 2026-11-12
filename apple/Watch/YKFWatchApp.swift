import SwiftUI
import WatchConnectivity
import WatchKit
import WidgetKit

@MainActor @Observable final class WatchStore: NSObject, WCSessionDelegate {
    var snapshot: Snapshot?
    var alerts: [AlertEvent] = []
    override init() {
        super.init()
        if let data = UserDefaults.standard.data(forKey: "alerts") { alerts = (try? JSONDecoder().decode([AlertEvent].self, from: data)) ?? [] }
        if let data = UserDefaults.standard.data(forKey: "snapshot") { snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) }
        if WCSession.isSupported() { WCSession.default.delegate = self; WCSession.default.activate() }
    }
    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        let payload = session.receivedApplicationContext
        let snapshot = payload["snapshot"] as? Data; let alerts = payload["alerts"] as? Data
        Task { @MainActor in self.applyContext(snapshot: snapshot, alerts: alerts) }
    }
    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        let snapshot = applicationContext["snapshot"] as? Data; let alerts = applicationContext["alerts"] as? Data
        Task { @MainActor in self.applyContext(snapshot: snapshot, alerts: alerts) }
    }
    private func applyContext(snapshot: Data?, alerts: Data?) {
        if let snapshot { apply(snapshot) }
        if let alerts, let events = try? JSONDecoder().decode([AlertEvent].self, from: alerts) {
            let ids = Set(self.alerts.map(\.id))
            let freshImportant = events.contains { !ids.contains($0.id) && ["near-10nm", "landed-unalerted", "tracker-gap", "tracker-health"].contains($0.alert_type ?? "") && (Format.date($0.created).map { Date.now.timeIntervalSince($0) < 300 } ?? false) }
            self.alerts = AlertMerge.unique(events); UserDefaults.standard.set(alerts, forKey: "alerts")
            if freshImportant && UserDefaults.standard.bool(forKey: "arrivalHaptics") { WKInterfaceDevice.current().play(.notification) }
        }
    }
    private func apply(_ data: Data) {
        guard let snapshot = try? JSONDecoder().decode(Snapshot.self, from: data) else { return }
        self.snapshot = snapshot; UserDefaults.standard.set(data, forKey: "snapshot")
        try? SharedCache.write(data); WidgetCenter.shared.reloadAllTimelines()
    }
}
@main struct YKFWatchApp: App {
    @State private var store = WatchStore()
    @AppStorage("arrivalHaptics") private var haptics = false
    var body: some Scene {
        WindowGroup {
            NavigationStack {
                List {
                    TimelineView(.periodic(from: .now, by: 60)) { time in Text(store.snapshot?.health(at: time.date) ?? "Open iPhone app to sync").font(.caption) }
                    Text("Published \(Format.time(store.snapshot?.generated))").font(.caption2)
                    ForEach(store.snapshot?.flights ?? []) { flight in
                        NavigationLink {
                            List { Text(flight.tail).font(.headline); Text(flight.type ?? "Type not reported"); Text(flight.distance); Text(flight.alt ?? "Altitude unknown"); Text(flight.displayStatus); Text("ETA \(flight.eta ?? "Not reported") Toronto time"); Text("Last seen \(Format.time(flight.last_seen))").font(.caption) }
                        } label: {
                            VStack(alignment: .leading) { Text(flight.tail).font(.headline); Text(flight.distance).monospacedDigit(); Text(flight.displayStatus).font(.caption) }
                        }
                    }
                    if store.snapshot?.flights.isEmpty != false { Text("No published aircraft") }
                    Section("Recent received alerts") {
                        ForEach(store.alerts.prefix(10)) { event in
                            NavigationLink { ScrollView { Text(event.text ?? "Alert content unavailable"); Text(Format.time(event.created)).font(.caption) } } label: { Text(event.text ?? event.alert_type ?? "Arrival alert").font(.caption).lineLimit(3) }
                        }
                        if store.alerts.isEmpty { Text("No synchronized alerts").font(.caption) }
                    }
                    Toggle("Arrival haptics", isOn: $haptics).font(.caption)
                    Text("Watch sync and haptics are opportunistic. iPhone remote notifications use Apple's delivery services.").font(.caption2)
                }.navigationTitle("YKF Arrivals")
            }
        }
    }
}

import SwiftUI
import SwiftData
import WidgetKit

@Model final class CachedSnapshot {
    @Attribute(.unique) var key: String
    var data: Data
    var received: Date
    init(data: Data) { key = "latest"; self.data = data; received = .now }
}
@Model final class AircraftPreference {
    @Attribute(.unique) var tail: String
    var favourite: Bool
    var notifications: Bool
    init(tail: String) { self.tail = tail; favourite = false; notifications = true }
}
@Model final class HistoryRecord {
    @Attribute(.unique) var key: String
    var tail: String
    var observed: Date
    var data: Data
    init(key: String, flight: Arrival, date: Date, data: Data) {
        self.key = key; tail = flight.tail; observed = date; self.data = data
    }
}
@Model final class StoredAlert {
    @Attribute(.unique) var eventID: String
    var data: Data
    var received: Date
    var acknowledged: Bool
    init(event: AlertEvent, data: Data) {
        eventID = event.id; self.data = data; received = .now; acknowledged = false
    }
}
@MainActor @Observable final class ArrivalStore {
    var snapshot: Snapshot?
    var error: String?
    var refreshing = false
    var lastSync: Date?
    let client = BackendClient()
    var context: ModelContext?
    private var lastAttempt: Date?
    func load(context: ModelContext) {
        self.context = context
        do {
            if let cached = try context.fetch(FetchDescriptor<CachedSnapshot>()).first {
                snapshot = try JSONDecoder().decode(Snapshot.self, from: cached.data)
                lastSync = cached.received
            }
        } catch { self.error = "Cached data could not be read." }
    }
    func synchronizeAlerts() async {
        guard let context, let address = UserDefaults.standard.string(forKey: "notificationService"),
              let base = URL(string: address), base.scheme == "https", base.host != nil,
              let credential = Keychain.get("notificationCredential"), !credential.isEmpty else { return }
        do {
            var request = URLRequest(url: base.appendingPathComponent("alerts")); request.timeoutInterval = 20
            request.setValue("Bearer \(credential)", forHTTPHeaderField: "Authorization")
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let events = try JSONDecoder().decode([AlertEvent].self, from: data)
            WatchBridge.shared.sendAlerts(data)
            var ids = Set(try context.fetch(FetchDescriptor<StoredAlert>()).map(\.eventID))
            for event in AlertMerge.unique(events) where !ids.contains(event.id) {
                context.insert(StoredAlert(event: event, data: try JSONEncoder().encode(event))); ids.insert(event.id)
            }
            let cutoff = Date.now.addingTimeInterval(-172800)
            for record in try context.fetch(FetchDescriptor<StoredAlert>()) where record.received < cutoff { context.delete(record) }
            try context.save()
            UserDefaults.standard.removeObject(forKey: "pushError")
        } catch { UserDefaults.standard.set("Notification history service unavailable. Existing alerts remain cached.", forKey: "pushError") }
    }
    func refresh() async {
        guard !refreshing, lastAttempt.map({ Date.now.timeIntervalSince($0) >= 60 }) ?? true else { return }
        lastAttempt = .now
        refreshing = true; defer { refreshing = false }
        do {
            let (snapshot, data) = try await client.snapshot()
            self.snapshot = snapshot; lastSync = .now; error = nil
            if let context {
                let cached = try context.fetch(FetchDescriptor<CachedSnapshot>()).first
                if let cached { cached.data = data; cached.received = .now }
                else { context.insert(CachedSnapshot(data: data)) }
                let existing = Set(try context.fetch(FetchDescriptor<HistoryRecord>()).map(\.key))
                for flight in snapshot.flights {
                    let key = "\(snapshot.generated ?? "unknown")|\(flight.tail)"
                    if !existing.contains(key) {
                        context.insert(HistoryRecord(key: key, flight: flight, date: Format.date(snapshot.generated) ?? .now, data: try JSONEncoder().encode(flight)))
                    }
                }
                let cutoff = Date.now.addingTimeInterval(-2 * 86400)
                for record in try context.fetch(FetchDescriptor<HistoryRecord>()) where record.observed < cutoff { context.delete(record) }
                try context.save()
            }
            try SharedCache.write(data); WidgetCenter.shared.reloadAllTimelines()
            await LiveArrival.update(snapshot)
            WatchBridge.shared.send(data)

        } catch is CancellationError { }
        catch { self.error = "Synchronization failed. Showing the last available publication; check its timestamp." }
        await synchronizeAlerts()
    }
}

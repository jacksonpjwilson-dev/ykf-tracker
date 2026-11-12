import SwiftUI
import SwiftData
import MapKit
import Charts

struct RootView: View {
    @Environment(ArrivalStore.self) private var store
    @Environment(\.modelContext) private var context
    @Environment(\.scenePhase) private var phase
    @State private var tab = 0
    @State private var selected: Arrival?
    @State private var pendingFlight: String?
    @AppStorage("appearance") private var appearance = "system"
    var body: some View {
        TabView(selection: $tab) {
            NavigationStack { ArrivalsView() }.tabItem { Label("Arrivals", systemImage: "airplane.arrival") }.tag(0)
            NavigationStack { AircraftMap() }.tabItem { Label("Map", systemImage: "map") }.tag(1)
            NavigationStack { MonitoringView() }.tabItem { Label("Monitoring", systemImage: "waveform.path.ecg") }.tag(2)
            NavigationStack { AlertsView() }.tabItem { Label("Alerts", systemImage: "bell") }.tag(3)
            NavigationStack { HistoryView() }.tabItem { Label("History", systemImage: "clock.arrow.circlepath") }.tag(4)
        }
        .preferredColorScheme(appearance == "dark" ? .dark : appearance == "light" ? .light : nil)
        .sheet(item: $selected) { flight in NavigationStack { FlightDetail(flight: flight) } }
        .onOpenURL { url in
            guard let route = Route(url: url) else { return }
            switch route {
            case .arrivals: tab = 0
            case .flight(let tail):
                tab = 0; pendingFlight = tail
                selected = store.snapshot?.flights.first { $0.tail == tail }
                if selected != nil { pendingFlight = nil }
            case .health, .shifts: tab = 2
            case .alerts: tab = 3
            }
        }
        .onChange(of: store.snapshot?.generated) { _, _ in
            if let tail = pendingFlight, let flight = store.snapshot?.flights.first(where: { $0.tail == tail }) {
                selected = flight; pendingFlight = nil
            }
        }
        .task {
            store.load(context: context)
            while !Task.isCancelled {
                if phase == .active { await store.refresh() }
                do { try await Task.sleep(for: .seconds(60)) } catch { break }
            }
        }
        .onChange(of: phase) { _, value in if value == .active { Task { await store.refresh() } } }
    }
}
struct Freshness: View {
    @Environment(ArrivalStore.self) private var store
    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { timeline in
            VStack(alignment: .leading, spacing: 6) {
                Label(store.snapshot?.health(at: timeline.date) ?? "Unknown status", systemImage: "waveform.path.ecg")
                    .font(.subheadline.weight(.semibold))
                Text("Published \(Format.time(store.snapshot?.generated))").font(.caption).foregroundStyle(.secondary)
                if let date = Format.date(store.snapshot?.generated) {
                    Text(date, style: .relative).font(.caption.monospacedDigit()) + Text(" since publication").font(.caption)
                }
                if store.snapshot?.arrivals_stale == true { Label("Schedule list is old", systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
                if let error = store.error { Label(error, systemImage: "wifi.exclamationmark").font(.caption).foregroundStyle(.orange) }
            }
        }
    }
}
struct ArrivalsView: View {
    @Environment(ArrivalStore.self) private var store
    @Query private var preferences: [AircraftPreference]
    @State private var query = ""
    @State private var filter = "All"
    private var flights: [Arrival] {
        (store.snapshot?.flights ?? []).filter { flight in
            (query.isEmpty || flight.tail.localizedCaseInsensitiveContains(query)) &&
            (filter == "All" || filter == "Scheduled" && flight.scheduled == true ||
             filter == "Unscheduled" && flight.scheduled == false ||
             filter == "Skycare" && flight.skycare == true ||
             filter == "Favourites" && preferences.contains { $0.tail == flight.tail && $0.favourite })
        }
    }
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("CYKF").font(.largeTitle.bold().monospaced())
                        Spacer()
                        TimelineView(.periodic(from: .now, by: 1)) { t in
                            Text(Format.time(ISO8601DateFormatter().string(from: t.date)))
                                .font(.caption.monospacedDigit())
                        }
                    }
                    Text("WATERLOO · ARRIVAL OPERATIONS").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    Freshness()
                    HStack {
                        metric("Inbound", "\(store.snapshot?.flights.filter { $0.status?.hasPrefix("inbound") == true || $0.status == "inside 10 NM" }.count ?? 0)")
                        Spacer()
                        metric("Scheduled", "\(store.snapshot?.flights.filter { $0.scheduled == true }.count ?? 0)")
                        Spacer()
                        metric("Window", store.snapshot?.active == true ? "Active" : "Inactive / unknown")
                    }
                }.padding(.vertical, 8)
            }
            Section {
                Picker("Filter", selection: $filter) {
                    ForEach(["All", "Scheduled", "Unscheduled", "Skycare", "Favourites"], id: \.self) { Text($0) }
                }
                if filter == "Skycare" { Text("Only explicit backend designations are shown. The public feed suppresses operator information.").font(.caption).foregroundStyle(.secondary) }
                ForEach(flights) { flight in
                    NavigationLink { FlightDetail(flight: flight) } label: { FlightCard(flight: flight) }
                }
                if flights.isEmpty {
                    ContentUnavailableView("No matching arrivals", systemImage: "airplane", description: Text(store.snapshot?.active == false ? "Monitoring is off shift. Next window: \(Format.time(store.snapshot?.next_window?.window_start))" : "No aircraft have been published for this filter."))
                }
            } header: { Text("Arrival board") } footer: { Text("Schedule ETAs are in America/Toronto. Community ADS-B and GitHub publication may lag or miss aircraft.") }
        }
        .navigationTitle("YKF Arrivals").navigationBarTitleDisplayMode(.inline)
        .searchable(text: $query, prompt: "Search tracked registrations")
        .refreshable { await store.refresh() }
        .toolbar { NavigationLink { SettingsView() } label: { Image(systemName: "gearshape") }.accessibilityLabel("Settings") }
    }
    private func metric(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading) { Text(value).font(.headline.monospacedDigit()); Text(title).font(.caption).foregroundStyle(.secondary) }
    }
}
struct FlightCard: View {
    let flight: Arrival
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading) { Text(flight.tail).font(.title2.bold().monospaced()); Text(flight.type ?? "Type not reported").font(.caption).foregroundStyle(.secondary) }
                Spacer()
                Text(flight.distance).font(.headline.monospacedDigit())
            }
            Label(flight.displayStatus, systemImage: flight.status == "inside 10 NM" ? "exclamationmark.circle.fill" : "airplane")
                .font(.subheadline.weight(.semibold)).foregroundStyle(flight.status == "inside 10 NM" ? .orange : .blue)
            HStack { Text(flight.alt ?? "Altitude not reported"); Spacer(); Text("ETA \(flight.eta ?? "—")") }.font(.caption.monospacedDigit())
            Text("\(flight.scheduled == true ? "Scheduled · FlightBridge" : flight.scheduled == false ? "Unscheduled" : "Schedule unknown") · Last seen \(Format.time(flight.last_seen))")
                .font(.caption2).foregroundStyle(.secondary)
        }.padding(.vertical, 10).accessibilityElement(children: .combine)
    }
}
struct FlightDetail: View {
    let flight: Arrival
    @Environment(\.modelContext) private var context
    @Query private var preferences: [AircraftPreference]
    @Query private var alerts: [StoredAlert]
    @State private var message: String?
    var preference: AircraftPreference? { preferences.first { $0.tail == flight.tail } }
    var body: some View {
        List {
            Section { FlightCard(flight: flight) }
            Section("Reported evidence") {
                row("Groundspeed", flight.groundspeed_kt.map { "\(Int($0)) kt" })
                row("Vertical speed", flight.vertical_rate_fpm.map { "\(Int($0)) ft/min" })
                row("Heading", flight.heading.map { "\(Int($0))°" })
                row("Bearing", flight.bearing.map { "\(Int($0))°" })
                row("Latitude", flight.latitude.map { String(format: "%.5f", $0) })
                row("Longitude", flight.longitude.map { String(format: "%.5f", $0) })
                row("Position timestamp", Format.time(flight.position_timestamp))
                row("Source", flight.source); row("Confidence", flight.confidence)
                Text("Distance is backend-reported. Scheduled ETA is not a measured arrival time. Missing contact does not confirm landing.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Aircraft preferences") {
                Toggle("Favourite", isOn: Binding(get: { preference?.favourite ?? false }, set: { setPreference(favourite: $0) }))
                Toggle("Arrival notifications", isOn: Binding(get: { preference?.notifications ?? true }, set: { setPreference(notifications: $0) }))
                Text("Preferences are stored locally. Server-side notification filtering requires the configured notification service.").font(.caption)
                Button("Follow with Live Activity") {
                    Task { do { try await LiveArrival.start(flight) } catch { message = error.localizedDescription } }
                }
                if let message { Text(message).font(.caption).foregroundStyle(.orange) }
            }
            Section("Received alert history") {
                ForEach(alerts.filter { (try? JSONDecoder().decode(AlertEvent.self, from: $0.data).tail) == flight.tail }) { alert in
                    Text((try? JSONDecoder().decode(AlertEvent.self, from: alert.data).text) ?? "Unavailable alert")
                }
                Text("The public live feed does not include alerts. Only events received by this installation appear here.").font(.caption).foregroundStyle(.secondary)
            }
        }.navigationTitle(flight.tail)
    }
    private func row(_ title: String, _ value: String?) -> some View { LabeledContent(title, value: value ?? "Not reported") }
    private func setPreference(favourite: Bool? = nil, notifications: Bool? = nil) {
        let record = preference ?? AircraftPreference(tail: flight.tail)
        if preference == nil { context.insert(record) }
        if let favourite { record.favourite = favourite }; if let notifications { record.notifications = notifications }
        do {
            try context.save()
            Task { do { try await NotificationRegistration.syncDevice() } catch { message = "Saved locally; notification service preferences could not be synchronized." } }
        } catch { message = "Could not save preference." }
    }
}
struct AircraftMap: View {
    @Environment(ArrivalStore.self) private var store
    @State private var satellite = false
    @State private var selected: String?
    static let airport = CLLocationCoordinate2D(latitude: 43.46083, longitude: -80.37861)
    var flights: [Arrival] { store.snapshot?.flights.filter(\.hasPosition) ?? [] }
    var body: some View {
        VStack(spacing: 0) {
            Map(initialPosition: .region(MKCoordinateRegion(center: Self.airport, latitudinalMeters: 150000, longitudinalMeters: 150000)), selection: $selected) {
                Marker("CYKF", systemImage: "airplane.circle.fill", coordinate: Self.airport).tint(.blue)
                ForEach([3, 5, 10, 20, 40], id: \.self) { radius in
                    MapCircle(center: Self.airport, radius: Double(radius) * 1852).foregroundStyle(.blue.opacity(0.025)).stroke(.blue.opacity(0.35), lineWidth: 1)
                }
                ForEach(flights) { flight in
                    Annotation(flight.tail, coordinate: CLLocationCoordinate2D(latitude: flight.latitude!, longitude: flight.longitude!)) {
                        VStack {
                            Image(systemName: "airplane").rotationEffect(.degrees((flight.heading ?? 90) - 90))
                            Text(flight.positionStale() ? "STALE" : Format.time(flight.position_timestamp)).font(.caption2)
                        }.padding(6).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
                    }.tag(flight.tail)
                }
            }
            .mapStyle(satellite ? .imagery : .standard)
            .mapControls { MapCompass(); MapScaleView(); MapPitchToggle() }
            List {
                Text("Distance rings: 3 · 5 · 10 · 20 · 40 NM").font(.caption)
                if flights.isEmpty { Label("No reported coordinates in this feed", systemImage: "location.slash"); Text("Aircraft markers require authorized coordinates and position timestamps. Distances alone cannot locate an aircraft.").font(.caption).foregroundStyle(.secondary) }
                ForEach(flights) { flight in NavigationLink { FlightDetail(flight: flight) } label: { FlightCard(flight: flight) }.listRowBackground(selected == flight.tail ? Color.blue.opacity(0.15) : Color.clear) }
            }.frame(maxHeight: 240)
        }.navigationTitle("Aircraft map")
        .toolbar { Toggle("Satellite", isOn: $satellite) }
    }
}
struct MonitoringView: View {
    @Environment(ArrivalStore.self) private var store
    var body: some View {
        List {
            Section("Tracker") {
                Freshness()
                LabeledContent("Backend reachability", value: store.error == nil && store.lastSync != nil ? "Last request succeeded" : "Unavailable / unknown")
                LabeledContent("Last app synchronization", value: store.lastSync.map { Format.time(ISO8601DateFormatter().string(from: $0)) } ?? "Never")
                LabeledContent("ADS-B provider", value: store.snapshot?.feed?.provider ?? "Not reported")
                LabeledContent("Feed availability", value: store.snapshot?.feed?.ok.map { $0 ? "Available at publication" : "Degraded" } ?? "Unknown")
                ForEach(["Last successful poll", "Last detected gap", "Pending alerts", "Recent failures", "Last recovery", "Last calendar synchronization"], id: \.self) { LabeledContent($0, value: "Not published") }
            }
            Section("Backend shift • Toronto time") {
                LabeledContent("Monitoring", value: store.snapshot?.active.map { $0 ? "Enabled" : "Off shift" } ?? "Unknown")
                LabeledContent("Shift start", value: Format.time(store.snapshot?.shift?.start))
                LabeledContent("Shift end", value: Format.time(store.snapshot?.shift?.end))
                LabeledContent("Monitoring begins", value: Format.time(store.snapshot?.shift?.window_start))
                LabeledContent("Monitoring ends", value: Format.time(store.snapshot?.shift?.window_end))
                LabeledContent("Next window", value: Format.time(store.snapshot?.next_window?.window_start))
                Text("The backend calendar controls monitoring, including the 30-minute lead-in. Local preferences do not change its schedule.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Upcoming published arrivals") { ForEach(store.snapshot?.flights.filter { $0.scheduled == true } ?? []) { FlightCard(flight: $0) } }
        }.navigationTitle("Monitoring").refreshable { await store.refresh() }
    }
}
struct AlertsView: View {
    @Environment(\.modelContext) private var context
    @Query(sort: \StoredAlert.received, order: .reverse) private var alerts: [StoredAlert]
    @State private var error: String?
    var body: some View {
        List {
            Section { Text("Backend alerts retain their original classification and stable ID. Acknowledgement here records that you reviewed an event; it does not acknowledge Tower delivery.").font(.caption) }
            ForEach(alerts) { record in
                let event = try? JSONDecoder().decode(AlertEvent.self, from: record.data)
                VStack(alignment: .leading, spacing: 8) {
                    Label(event?.alert_type ?? "Arrival alert", systemImage: "bell.badge")
                    Text(event?.text ?? "Alert content unavailable")
                    Text(Format.time(event?.created)).font(.caption)
                    if let basis = event?.basis { Text("Evidence: \(basis)").font(.caption) }
                    Button(record.acknowledged ? "Acknowledged on this device" : "Acknowledge on this device") {
                        record.acknowledged = true
                        do { try context.save() } catch { self.error = "Could not save acknowledgement." }
                    }.disabled(record.acknowledged)
                }
            }
            if alerts.isEmpty { ContentUnavailableView("No received alerts", systemImage: "bell.slash", description: Text("Native remote alerts require Apple provisioning and a secure notification service. Live feed polling does not generate replacement arrival alerts.")) }
            if let error { Text(error) }
        }.navigationTitle("Alerts")
    }
}
struct HistoryView: View {
    @Query(sort: \HistoryRecord.observed, order: .reverse) private var records: [HistoryRecord]
    @State private var search = ""
    var visible: [HistoryRecord] { records.filter { search.isEmpty || $0.tail.localizedCaseInsensitiveContains(search) } }
    var body: some View {
        List {
            Section { Text("Local publication snapshots retained for 48 hours. Timestamps record backend publication, not actual landing. History starts after the first successful sync.").font(.caption) }
            if !visible.isEmpty {
                Chart(Array(visible.prefix(100))) { record in
                    if let flight = try? JSONDecoder().decode(Arrival.self, from: record.data), let distance = flight.dist_nm {
                        PointMark(x: .value("Publication", record.observed), y: .value("NM", distance)).foregroundStyle(by: .value("Aircraft", record.tail))
                    }
                }.frame(height: 180)
            }
            ForEach(visible) { record in
                if let flight = try? JSONDecoder().decode(Arrival.self, from: record.data) {
                    NavigationLink { FlightDetail(flight: flight) } label: {
                        VStack(alignment: .leading) { FlightCard(flight: flight); Text(record.observed, style: .date).font(.caption) }
                    }
                }
            }
            if visible.isEmpty { ContentUnavailableView("No local history", systemImage: "clock") }
        }.navigationTitle("History").searchable(text: $search, prompt: "Registration")
        .toolbar {
            ShareLink(item: HistoryExport.csv(visible.compactMap { record in
                guard let flight = try? JSONDecoder().decode(Arrival.self, from: record.data) else { return nil }
                return (record.observed, flight)
            }), preview: SharePreview("YKF local publication history")) { Label("Export", systemImage: "square.and.arrow.up") }
            .disabled(visible.isEmpty)
        }
    }
}
struct SettingsView: View {
    @AppStorage("appearance") private var appearance = "system"
    @State private var message: String?
    @AppStorage("notificationService") private var service = ""
    @AppStorage("notificationSeverity") private var severity = "all"
    @State private var credential = ""
    var body: some View {
        Form {
            Section("Secure notification service") {
                TextField("HTTPS service URL", text: $service).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("Device enrollment credential", text: $credential)
                Button("Save credential and register") {
                    Task {
                        do {
                            if !credential.isEmpty { try Keychain.set(credential, account: "notificationCredential"); credential = "" }
                            message = await NotificationRegistration.request()
                        } catch { message = "Could not save credential in Keychain." }
                    }
                }
                Button("Revoke this device") {
                    Task {
                        do {
                            if let token = Keychain.get("deviceToken") { try await NotificationRegistration.service(path: "devices", body: ["token": token], method: "DELETE") }
                            message = "Device revoked."
                        } catch { message = "Revocation failed. Check the service configuration." }
                    }
                }
                if let error = UserDefaults.standard.string(forKey: "pushError") { Text(error).font(.caption) }
            }
            Section("Notifications") {
                Picker("Alert preference", selection: $severity) {
                    Text("All backend alerts").tag("all")
                    Text("Proximity and monitoring failures").tag("important")
                }.onChange(of: severity) { _, _ in
                    Task { do { try await NotificationRegistration.syncDevice() } catch { message = "Alert preference saved locally; service synchronization failed." } }
                }
                Button("Enable arrival notifications") { Task { message = await NotificationRegistration.request() } }
                if let message { Text(message).font(.caption) }
                Text("Push delivery depends on connectivity and Apple services. It is not a guaranteed safety-critical alarm system.").font(.caption)
            }
            Section("Display") {
                Picker("Appearance", selection: $appearance) { Text("System").tag("system"); Text("Dark").tag("dark"); Text("Light").tag("light") }
            }
            Section("Data") {
                Text("Source: existing YKF tracker public live.json. Foreground refresh at most once per minute; backend publication is about every two minutes, and GitHub caching can add delay.")
                Text("No ADS-B provider calls are made by this application. Credentials and private client records are never included in shared widget data.")
            }
        }.navigationTitle("Settings")
    }
}

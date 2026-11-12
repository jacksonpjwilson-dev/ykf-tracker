import SwiftUI
import WidgetKit
import AppIntents
import ActivityKit

struct BoardConfiguration: WidgetConfigurationIntent {
    static var title: LocalizedStringResource = "Arrival board"
    @Parameter(title: "Scheduled aircraft only", default: false) var scheduledOnly: Bool
}
struct BoardEntry: TimelineEntry {
    var date: Date
    var snapshot: Snapshot?
    var scheduledOnly: Bool
}
struct BoardProvider: AppIntentTimelineProvider {
    func placeholder(in context: Context) -> BoardEntry { .init(date: .now, snapshot: nil, scheduledOnly: false) }
    func snapshot(for configuration: BoardConfiguration, in context: Context) async -> BoardEntry { .init(date: .now, snapshot: SharedCache.read(), scheduledOnly: configuration.scheduledOnly) }
    func timeline(for configuration: BoardConfiguration, in context: Context) async -> Timeline<BoardEntry> {
        let now = Date.now
        // A second entry makes active publications visibly stale without waiting for a network refresh.
        let first = BoardEntry(date: now, snapshot: SharedCache.read(), scheduledOnly: configuration.scheduledOnly)
        let stale = BoardEntry(date: now.addingTimeInterval(300), snapshot: first.snapshot, scheduledOnly: configuration.scheduledOnly)
        return Timeline(entries: [first, stale], policy: .after(now.addingTimeInterval(900)))
    }
}
struct BoardView: View {
    @Environment(\.widgetFamily) private var family
    let entry: BoardEntry
    var flights: [Arrival] {
        let rows = (entry.snapshot?.flights ?? []).filter { !entry.scheduledOnly || $0.scheduled == true }
        if family == .systemSmall, let next = entry.snapshot?.nextExpected(at: entry.date) { return [next] }
        return rows
    }
    var body: some View {
        Group {
            switch family {
            case .accessoryCircular:
                VStack { Image(systemName: "airplane.arrival"); Text("\(flights.filter { $0.status?.hasPrefix("inbound") == true || $0.status == "inside 10 NM" }.count)") }
            case .accessoryInline:
                Text("YKF · \(entry.snapshot?.health(at: entry.date) ?? "Unknown")")
            case .accessoryRectangular:
                VStack(alignment: .leading) {
                    Text("YKF · \(flights.first?.tail ?? "No arrivals")").font(.headline)
                    Text(entry.snapshot?.active == false ? "Next: \(Format.time(entry.snapshot?.next_window?.window_start))" : "\(flights.first?.distance ?? "No position")")
                    Text(entry.snapshot?.health(at: entry.date) ?? "Unknown").font(.caption2)
                }
            default:
                VStack(alignment: .leading, spacing: 8) {
                    Label("CYKF ARRIVALS", systemImage: "airplane.arrival").font(.caption.bold()).foregroundStyle(.blue)
                    Text(entry.snapshot?.health(at: entry.date) ?? "Open app to sync").font(.caption)
                    ForEach(Array(flights.prefix(family == .systemLarge ? 5 : family == .systemMedium ? 3 : 1))) { flight in
                        Link(destination: URL(string: "ykf://flight/\(flight.tail)")!) {
                            HStack {
                                VStack(alignment: .leading) { Text(flight.tail).font(.headline.monospaced()); Text("ETA \(flight.eta ?? "—")").font(.caption) }
                                Spacer(); Text(flight.distance).font(.caption.monospacedDigit())
                            }
                        }
                    }
                    if flights.isEmpty { Text("No published arrivals").font(.subheadline) }
                    Spacer(minLength: 0)
                    Text("Published \(Format.time(entry.snapshot?.generated))").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }.containerBackground(.background, for: .widget).widgetURL(URL(string: "ykf://arrivals"))
    }
}
struct ArrivalBoardWidget: Widget {
    var body: some WidgetConfiguration {
        AppIntentConfiguration(kind: "YKFBoard", intent: BoardConfiguration.self, provider: BoardProvider()) { BoardView(entry: $0) }
            .configurationDisplayName("YKF Arrivals").description("Cached arrivals and backend monitoring state. Updates are system scheduled.")
            .supportedFamilies([.systemSmall, .systemMedium, .systemLarge, .accessoryCircular, .accessoryRectangular, .accessoryInline])
    }
}
struct ArrivalLiveWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: ArrivalActivityAttributes.self) { context in
            HStack {
                VStack(alignment: .leading) { Text(context.attributes.tail).font(.headline); Text(context.attributes.aircraftType); Text(context.state.status).font(.caption) }
                Spacer()
                VStack(alignment: .trailing) { Text(context.state.distance).font(.title2.monospacedDigit()); Text(context.state.altitude); Text(context.isStale ? "STALE" : Format.time(context.state.updated)).font(.caption2) }
            }.padding().activityBackgroundTint(.black).activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) { Text(context.attributes.tail).font(.headline) }
                DynamicIslandExpandedRegion(.trailing) { Text(context.state.distance).monospacedDigit() }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack { Text(context.state.status); Text(context.isStale ? "Stale • open app to refresh" : "Updated \(Format.time(context.state.updated))").font(.caption) }
                }
            } compactLeading: { Text(context.attributes.tail) }
              compactTrailing: { Text(context.isStale ? "Old" : context.state.distance) }
              minimal: { Image(systemName: context.isStale ? "clock.badge.exclamationmark" : "airplane.arrival") }
              .widgetURL(URL(string: "ykf://flight/\(context.attributes.tail)"))
        }
    }
}
@main struct YKFWidgetBundle: WidgetBundle {
    var body: some Widget { ArrivalBoardWidget(); ArrivalLiveWidget() }
}

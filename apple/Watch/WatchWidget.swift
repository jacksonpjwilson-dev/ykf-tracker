import SwiftUI
import WidgetKit

struct WatchEntry: TimelineEntry { var date: Date; var snapshot: Snapshot? }
struct WatchProvider: TimelineProvider {
    func placeholder(in context: Context) -> WatchEntry { .init(date: .now, snapshot: nil) }
    func getSnapshot(in context: Context, completion: @escaping (WatchEntry) -> Void) { completion(.init(date: .now, snapshot: SharedCache.read())) }
    func getTimeline(in context: Context, completion: @escaping (Timeline<WatchEntry>) -> Void) {
        let now = Date.now; let snapshot = SharedCache.read()
        completion(Timeline(entries: [.init(date: now, snapshot: snapshot), .init(date: now.addingTimeInterval(300), snapshot: snapshot)], policy: .after(now.addingTimeInterval(900))))
    }
}
@main struct WatchArrivalWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "YKFWatchBoard", provider: WatchProvider()) { entry in
            VStack(alignment: .leading) {
                Text("YKF · \(entry.snapshot?.flights.first?.tail ?? "No arrivals")").font(.headline)
                Text(entry.snapshot?.flights.first?.distance ?? "No position reported").font(.caption)
                Text(entry.snapshot?.health(at: entry.date) ?? "Open iPhone app to sync").font(.caption2)
            }.containerBackground(.background, for: .widget).widgetURL(URL(string: "ykf://arrivals"))
        }.configurationDisplayName("YKF arrivals").description("Last synchronized arrival status.")
            .supportedFamilies([.accessoryRectangular, .accessoryCircular, .accessoryInline])
    }
}

import SwiftUI
import SwiftData

@main struct YKFArrivalsApp: App {
    @UIApplicationDelegateAdaptor(NotificationDelegate.self) var delegate
    @State private var store = ArrivalStore()
    var body: some Scene {
        WindowGroup {
            RootView().environment(store).tint(.blue)
                .onAppear { WatchBridge.shared.activate() }
        }
        .modelContainer(Persistence.container)
    }
}

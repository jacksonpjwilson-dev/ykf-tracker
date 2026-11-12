import Foundation
import WatchConnectivity

final class WatchBridge: NSObject, WCSessionDelegate, @unchecked Sendable {
    static let shared = WatchBridge()
    func activate() { guard WCSession.isSupported() else { return }; WCSession.default.delegate = self; WCSession.default.activate() }
    func send(_ data: Data) {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated,
              WCSession.default.isPaired, WCSession.default.isWatchAppInstalled else { return }
        do { var payload = WCSession.default.applicationContext; payload["snapshot"] = data; try WCSession.default.updateApplicationContext(payload) }
        catch { /* Last cached watch snapshot remains available and visibly timestamped. */ }
    }
    func sendAlerts(_ data: Data) {
        guard WCSession.isSupported(), WCSession.default.activationState == .activated,
              WCSession.default.isPaired, WCSession.default.isWatchAppInstalled else { return }
        var payload = WCSession.default.applicationContext; payload["alerts"] = data
        try? WCSession.default.updateApplicationContext(payload)
    }
    func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) { }
    func sessionDidBecomeInactive(_ session: WCSession) { }
    func sessionDidDeactivate(_ session: WCSession) { session.activate() }
}

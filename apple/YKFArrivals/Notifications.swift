import UIKit
import UserNotifications
import Security
import SwiftData

@MainActor enum Persistence {
    static let container: ModelContainer = {
        do { return try ModelContainer(for: CachedSnapshot.self, AircraftPreference.self, HistoryRecord.self, StoredAlert.self) }
        catch { fatalError("Unable to open YKF local storage: \(error)") }
    }()
}
enum Keychain {
    static func set(_ value: String, account: String) throws {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "YKF", kSecAttrAccount as String: account]
        SecItemDelete(query as CFDictionary)
        var item = query; item[kSecValueData as String] = Data(value.utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let result = SecItemAdd(item as CFDictionary, nil)
        guard result == errSecSuccess else { throw NSError(domain: NSOSStatusErrorDomain, code: Int(result)) }
    }
    static func get(_ account: String) -> String? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "YKF", kSecAttrAccount as String: account, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
@MainActor enum NotificationRegistration {
    static func request() async -> String {
        #if BASIC_APP
        return "Remote notifications are available in the full project after Apple push provisioning."
        #else
        do {
            let allowed = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
            guard allowed else { return "Notification permission was declined." }
            UIApplication.shared.registerForRemoteNotifications()
            return "Permission granted. Remote delivery requires a configured secure service and APNs provisioning."
        } catch { return "Notification permission could not be requested." }
        #endif
    }
    static func register(_ token: Data) async throws {
        let hex = token.map { String(format: "%02x", $0) }.joined()
        try Keychain.set(hex, account: "deviceToken")
        try await syncDevice()
    }
    static func syncDevice() async throws {
        guard let token = Keychain.get("deviceToken") else { return }
        let muted = try Persistence.container.mainContext.fetch(FetchDescriptor<AircraftPreference>()).filter { !$0.notifications }.map(\.tail)
        let important = UserDefaults.standard.string(forKey: "notificationSeverity") == "important"
        let kinds = important ? ["near-10nm", "landed-unalerted", "tracker-gap", "tracker-health"] : []
        try await service(path: "devices", body: ["token": token, "kinds": kinds, "muted_tails": muted])
    }
    static func service(path: String, body: [String: Any], method: String = "POST") async throws {
        guard let address = UserDefaults.standard.string(forKey: "notificationService"),
              let base = URL(string: address), base.scheme == "https", base.host != nil,
              let credential = Keychain.get("notificationCredential"), !credential.isEmpty else { throw URLError(.userAuthenticationRequired) }
        var request = URLRequest(url: base.appendingPathComponent(path)); request.httpMethod = method
        request.setValue("Bearer \(credential)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        request.timeoutInterval = 20
        let (_, response) = try await URLSession.shared.data(for: request)
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else { throw URLError(.badServerResponse) }
    }
}
@MainActor final class NotificationDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self; return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        Task {
            do { try await NotificationRegistration.register(deviceToken); UserDefaults.standard.removeObject(forKey: "pushError") }
            catch { UserDefaults.standard.set("Device token saved; service registration pending. Open Settings to configure and retry.", forKey: "pushError") }
        }
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        UserDefaults.standard.set("APNs registration failed. Check signing, entitlement, connectivity, and device provisioning.", forKey: "pushError")
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        await receive(notification.request.content.userInfo); return [.banner, .sound, .list]
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        await receive(response.notification.request.content.userInfo)
        if let tail = response.notification.request.content.userInfo["tail"] as? String,
           let url = URL(string: "ykf://flight/\(tail.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? "")") {
            await UIApplication.shared.open(url)
        }
    }
    private func receive(_ payload: [AnyHashable: Any]) async {
        guard let event = payload["event"] as? [String: Any],
              let data = try? JSONSerialization.data(withJSONObject: event),
              let alert = try? JSONDecoder().decode(AlertEvent.self, from: data) else { return }
        let context = Persistence.container.mainContext
        do {
            let known = try context.fetch(FetchDescriptor<StoredAlert>())
            if !known.contains(where: { $0.eventID == alert.id }) { context.insert(StoredAlert(event: alert, data: data)); try context.save() }
            if let token = Keychain.get("deviceToken") {
                try await NotificationRegistration.service(path: "receipts", body: ["alert_id": alert.id, "token": token])
            }
        } catch { UserDefaults.standard.set("Alert storage or service receipt failed.", forKey: "pushError") }
    }
}

import Foundation

// App Group must be provisioned on all Apple targets. No credentials enter this cache.
enum SharedCache {
    static let group = "group.dev.jacksonwilson.ykfarrivals"
    static var url: URL? {
        #if BASIC_APP
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?.appendingPathComponent("snapshot.json")
        #else
        FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: group)?.appendingPathComponent("snapshot.json")
        #endif
    }
    static func read() -> Snapshot? {
        guard let url, let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(Snapshot.self, from: data)
    }
    static func write(_ data: Data) throws {
        guard let url else { return }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
}

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

actor BackendClient {
    static let primary = URL(string: "https://api.github.com/repos/jacksonpjwilson-dev/ykf-tracker/contents/live.json?ref=data")!
    static let fallback = URL(string: "https://raw.githubusercontent.com/jacksonpjwilson-dev/ykf-tracker/data/live.json")!
    private let session: URLSession
    private var etag: String?
    private var previous: Data?
    init(session: URLSession = .shared) { self.session = session }
    func snapshot() async throws -> (Snapshot, Data) {
        do {
            var request = URLRequest(url: Self.primary)
            request.timeoutInterval = 20
            request.setValue("application/vnd.github.raw+json", forHTTPHeaderField: "Accept")
            request.setValue("YKF-Arrivals-iOS/1.0", forHTTPHeaderField: "User-Agent")
            if let etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
            if http.statusCode == 304, let previous { return (try JSONDecoder().decode(Snapshot.self, from: previous), previous) }
            guard http.statusCode == 200 else { throw URLError(.badServerResponse) }
            let snapshot = try JSONDecoder().decode(Snapshot.self, from: data)
            guard Format.date(snapshot.generated) != nil else { throw URLError(.cannotParseResponse) }
            previous = data; etag = http.value(forHTTPHeaderField: "ETag")
            return (snapshot, data)
        } catch is CancellationError { throw CancellationError() }
        catch {
            var request = URLRequest(url: Self.fallback); request.timeoutInterval = 20
            let (data, response) = try await session.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let snapshot = try JSONDecoder().decode(Snapshot.self, from: data)
            guard Format.date(snapshot.generated) != nil else { throw URLError(.cannotParseResponse) }
            return (snapshot, data)
        }
    }
}

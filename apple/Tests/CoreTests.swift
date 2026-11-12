import XCTest
@testable import YKFCore
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

final class CoreTests: XCTestCase {
    func decode(_ json: String) throws -> Snapshot { try JSONDecoder().decode(Snapshot.self, from: Data(json.utf8)) }
    func testPublishedPayloadAndUnknownFields() throws {
        let snapshot = try decode(#"{"generated":"2026-11-11T09:04:38-05:00","active":true,"rows":[{"tail":"C-GTST","alt":"3,200 ft","status":"expected","future":42}],"future":{}}"#)
        XCTAssertEqual(snapshot.flights.first?.alt, "3,200 ft")
        XCTAssertNil(snapshot.flights.first?.dist_nm)
        XCTAssertFalse(snapshot.flights.first!.hasPosition)
        XCTAssertEqual(try decode("{}").flights.count, 0)
    }
    func testHistoryExportQuotesUncertainStatus() throws {
        let snapshot = try decode(#"{"rows":[{"tail":"C-GTST","status":"landed?","eta":"09:00"}]}"#)
        let csv = HistoryExport.csv([(Date(timeIntervalSince1970: 0), snapshot.flights[0])])
        XCTAssertTrue(csv.contains("landing unconfirmed"))
        XCTAssertTrue(csv.contains("\"C-GTST\""))
        XCTAssertTrue(csv.hasPrefix("publication_utc,"))
    }
    func testNotificationDeduplication() throws {
        let data = Data(#"[{"id":"synthetic","text":"Synthetic"},{"id":"synthetic","text":"Retry"},{"id":"","text":"Invalid"}]"#.utf8)
        let events = try JSONDecoder().decode([AlertEvent].self, from: data)
        XCTAssertEqual(AlertMerge.unique(events).count, 1)
        XCTAssertEqual(AlertMerge.unique(events).first?.text, "Synthetic")
    }
    func testDistanceFormatting() {
        XCTAssertEqual(Format.distance(nil), "Not reported")
        XCTAssertEqual(Format.distance(-1), "Not reported")
        XCTAssertEqual(Format.distance(.infinity), "Not reported")
        XCTAssertEqual(Format.distance(9.56), "9.6 NM")
    }
    func testFreshnessAndFeedDegradation() throws {
        let snapshot = try decode(#"{"generated":"2026-11-11T14:00:00Z","active":true,"feed":{"ok":true}}"#)
        XCTAssertEqual(snapshot.health(at: Format.date("2026-11-11T14:04:00Z")!), "Monitoring healthy")
        XCTAssertEqual(snapshot.health(at: Format.date("2026-11-11T14:06:00Z")!), "Stale data")
        var degraded = snapshot; degraded.feed = Feed(provider: "test", ok: false)
        XCTAssertEqual(degraded.health(at: Format.date(snapshot.generated)!), "Provider degraded")
        XCTAssertEqual(try decode("{}").health(), "Unknown status")
    }
    func testOffShiftIsNotAutomaticallyDown() throws {
        let snapshot = try decode(#"{"generated":"2026-11-10T14:00:00Z","active":false,"next_window":{"window_start":"2026-11-12T10:00:00Z"}}"#)
        XCTAssertTrue(snapshot.health(at: Format.date("2026-11-11T14:00:00Z")!).hasPrefix("Not on shift"))
        XCTAssertEqual(snapshot.health(at: Format.date("2026-11-12T10:00:00Z")!), "Stale shift information")
    }
    func testUncertainLanding() throws {
        let snapshot = try decode(#"{"rows":[{"tail":"C-GTST","status":"landed?"}]}"#)
        XCTAssertEqual(snapshot.flights[0].displayStatus, "Contact lost • landing unconfirmed")
    }
    func testShiftBoundaryAndDST() throws {
        let snapshot = try decode(#"{"shift":{"window_start":"2026-11-01T04:30:00-05:00","window_end":"2026-11-01T15:30:00-05:00"}}"#)
        XCTAssertTrue(snapshot.shift!.contains(Format.date("2026-11-01T09:30:00Z")!))
        XCTAssertFalse(snapshot.shift!.contains(Format.date("2026-11-01T20:30:00Z")!))
        XCTAssertTrue(Format.time("2026-11-01T09:30:00Z").contains("04:30"))
        XCTAssertEqual(Format.date("2026-11-11T14:00:00.123Z")?.timeIntervalSince1970 ?? 0, 1794405600.123, accuracy: 0.001)
    }
    func testOvernightScheduledETA() throws {
        let snapshot = try decode(#"{"shift":{"start":"2026-11-11T15:00:00-05:00","window_start":"2026-11-11T14:30:00-05:00"},"rows":[{"tail":"C-GTST","eta":"00:30","scheduled":true},{"tail":"C-GOLD","eta":"18:00","scheduled":true}]}"#)
        XCTAssertEqual(Format.scheduledDate("00:30", shift: snapshot.shift), Format.date("2026-11-12T05:30:00Z"))
        XCTAssertEqual(snapshot.nextExpected(at: Format.date("2026-11-12T01:00:00Z")!)?.tail, "C-GTST")
        XCTAssertNil(Format.scheduledDate("25:00", shift: snapshot.shift))
    }
    func testCacheRoundTrip() throws {
        let snapshot = try decode(#"{"generated":"2026-11-11T14:00:00Z","rows":[{"tail":"C-GTST","status":"tracking"}]}"#)
        let data = try JSONEncoder().encode(snapshot)
        XCTAssertEqual(try JSONDecoder().decode(Snapshot.self, from: data).flights, snapshot.flights)
    }
    func testDeepLinks() {
        XCTAssertEqual(Route(url: URL(string: "ykf://flight/C-GTST")!), .flight("C-GTST"))
        XCTAssertEqual(Route(url: URL(string: "ykf://shifts")!), .shifts)
        XCTAssertNil(Route(url: URL(string: "https://example.com")!))
        XCTAssertNil(Route(url: URL(string: "ykf://flight/")!))
    }
    func testLiveActivityLifecycle() throws {
        let snapshot = try decode(#"{"rows":[{"tail":"C-GTST","last_seen":"2026-11-11T14:00:00Z","status":"inside 10 NM"}]}"#)
        let flight = snapshot.flights[0]
        XCTAssertTrue(ActivityPolicy.canStart(flight, now: Format.date("2026-11-11T14:01:00Z")!))
        XCTAssertTrue(ActivityPolicy.shouldEnd(flight, now: Format.date("2026-11-11T14:06:00Z")!))
        XCTAssertTrue(ActivityPolicy.shouldEnd(nil))
    }
    func testInvalidCoordinatesAreNotPlotted() throws {
        let snapshot = try decode(#"{"rows":[{"tail":"C-GTST","latitude":91,"longitude":0,"position_timestamp":"2026-11-11T14:00:00Z"}]}"#)
        XCTAssertFalse(snapshot.flights[0].hasPosition)
    }
}

final class MockProtocol: URLProtocol, @unchecked Sendable {
    static var handler: ((URLRequest) throws -> (Int, Data, [String: String]))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            let (code, data, headers) = try Self.handler!(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: code, httpVersion: nil, headerFields: headers)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data); client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() { }
}
final class NetworkTests: XCTestCase {
    func client() -> BackendClient {
        let config = URLSessionConfiguration.ephemeral; config.protocolClasses = [MockProtocol.self]
        return BackendClient(session: URLSession(configuration: config))
    }
    override func tearDown() { MockProtocol.handler = nil }
    func testETagAnd304PreservePublication() async throws {
        let payload = Data(#"{"generated":"2026-11-11T14:00:00Z","rows":[]}"#.utf8)
        var requests = 0
        MockProtocol.handler = { request in
            requests += 1
            if requests == 1 { return (200, payload, ["ETag": "test"]) }
            XCTAssertEqual(request.value(forHTTPHeaderField: "If-None-Match"), "test")
            return (304, Data(), [:])
        }
        let backend = client()
        let first = try await backend.snapshot(); let second = try await backend.snapshot()
        XCTAssertEqual(first.1, second.1)
    }
    func testFallbackAfterPrimaryFailure() async throws {
        MockProtocol.handler = { request in
            if request.url!.host == "api.github.com" { return (503, Data(), [:]) }
            return (200, Data(#"{"generated":"2026-11-11T14:00:00Z"}"#.utf8), [:])
        }
        let result = try await client().snapshot()
        XCTAssertNotNil(result.0.generated)
    }
    func testNetworkFailureIsThrown() async {
        MockProtocol.handler = { _ in throw URLError(.notConnectedToInternet) }
        do { _ = try await client().snapshot(); XCTFail("Expected failure") } catch { }
    }
}

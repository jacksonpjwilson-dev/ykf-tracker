// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "YKFCore",
    platforms: [.macOS(.v13), .iOS(.v17)],
    products: [.library(name: "YKFCore", targets: ["YKFCore"])],
    targets: [
        .target(name: "YKFCore", path: "Shared", exclude: ["SharedCache.swift", "ArrivalIntents.swift"], sources: ["Models.swift", "BackendClient.swift", "ArrivalActivity.swift"]),
        .testTarget(name: "YKFCoreTests", dependencies: ["YKFCore"], path: "Tests")
    ]
)

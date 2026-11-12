# YKF Arrivals — native Apple applications

This is a SwiftUI iPhone client for the existing Python tracker's actual public `live.json`, with iOS 17+, watchOS 10+, WidgetKit, ActivityKit, App Intents and SwiftData source. It is a native app, not a webpage wrapper. Apple framework builds, device behavior and signing still need verification on a Mac. See [AUDIT.md](AUDIT.md) and [BUILD-REPORT.md](BUILD-REPORT.md) for verified scope and limitations.

## First install on a MacBook Air

Start with [MACBOOK-SETUP.md](MACBOOK-SETUP.md). The checked-in `YKFBasic.xcodeproj` builds just the iPhone app without App Groups, APNs entitlements or embedded Watch/widget targets. It uses the same source and public backend, keeps its cache in the app sandbox, and disables push enrollment/Live Activities until you use the full project. No backend deployment or paid Apple services are needed for the basic simulator build. A Personal Team can be used for temporary device testing subject to Apple's signing limits.

## Full project: open and build

Open `apple/YKFArrivals.xcodeproj` in Xcode 16 or later. Choose `YKFArrivals`. All four targets and shared schemes are checked in: iPhone, iPhone widgets/Live Activity, Watch app, Watch widget/Smart Stack extension. The dependency-free `python3 apple/generate_project.py` regenerates the same project; change that generator when changing target membership or build settings.

Select your Apple development team for all targets. Register the bundle IDs rooted at `dev.jacksonwilson.ykfarrivals` (or replace them consistently in the generator and Watch Info.plist). Provision the `group.dev.jacksonwilson.ykfarrivals` App Group for the iPhone/widget pair and Watch/widget pair. WatchConnectivity transfers snapshots between devices; App Groups do not automatically sync phone files to Watch.

Enable Push Notifications on the iPhone identifier. The checked-in APNs entitlement is development; ensure archive provisioning selects the correct production entitlement before TestFlight/App Store distribution. An Apple Developer membership, signing certificates/profiles, appropriate device or simulator runtimes, and an APNs signing key are needed for device testing and remote delivery. No credentials are included.

```sh
swift test --package-path apple
xcodebuild -project apple/YKFArrivals.xcodeproj -scheme YKFArrivals \
  -configuration Debug -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

The new macOS CI workflow runs these commands when pushed. Check the feature branch Actions results for the latest remote build status.

## Using the app

The arrival board connects directly to GitHub's consolidated live publication with an ETag and raw fallback. Foreground requests are throttled to once per minute, including pull-to-refresh. SwiftData holds the last successful snapshot and per-aircraft preferences. Foreground scene activation refreshes when eligible; there is no claimed continuous background polling. Missing values stay missing. `landed?` is displayed as unconfirmed contact loss.

Tabs cover the arrival board/search/filter/favourites, MapKit, monitoring/shifts, received alerts and local history. Dark, light and system appearance are selectable. History stores publication snapshots for 48 hours during synchronization and exports the filtered public fields as quoted CSV text through the system share sheet. These are not inferred landing times.

MapKit provides native pan/zoom, standard/satellite maps, an airport marker and 3/5/10/20/40 NM rings. No aircraft are placed without both valid reported coordinates and a position timestamp. The public feed currently supplies none. Optional model keys for a future authorized interface are `latitude`, `longitude`, `position_timestamp`, `heading`, `groundspeed_kt`, `vertical_rate_fpm`, `bearing`, `source`, `confidence`. Merely adding model keys does not activate an authenticated position endpoint.

Widgets read a shared cached publication, support configurable scheduled-only boards, deep links, small/medium/large and Lock Screen families. The Watch extension provides accessory/Smart Stack families. Timelines include a staleness transition and request later refreshes; WidgetKit controls actual refresh timing. Opening the iPhone app synchronizes snapshots to the Watch opportunistically. Recent secure-service alerts are also forwarded to the Watch; optional haptics apply to freshly synchronized important events and are not a background alarm guarantee.

Live Activities require user selection of a fresh backend-reported inbound/inside-10-NM aircraft. They update during app synchronization and end if the row disappears, becomes stale, concludes, goes off shift or reaches the local one-hour cap. The system displays stale state even if the app stops running. They have no remote ActivityKit push integration yet and cannot guarantee immediate ending while suspended. Dynamic Island layouts are included.

The `Show YKF arrivals` App Intent has destination parameters for arrivals, next scheduled aircraft, backend-reported inside-10-NM aircraft, health, shifts and alerts. `Open tracked YKF aircraft` accepts a currently cached registration. Both are available to Shortcuts; an App Shortcut publishes the main arrival action. Responses identify the cached publication timestamp. Future Head Quarter/G.I.L.E.S. integration can consume the modular models and adapter health endpoint; no private client summaries are transmitted automatically.

## Remote notifications

Deploy the opt-in [native service](../native_service/README.md) separately. Enter its HTTPS URL and owner enrollment credential in the app's Settings. The credential and APNs device token are stored in Keychain. Request notification permission and register. Re-register after configuring/changing the service, and use revoke before retiring a device. Token callbacks update enrollment; failed registration is reported in Settings and can be retried there.

Settings choose all backend alerts or proximity/monitoring failures. Per-aircraft notification switches synchronize muted registrations to the service. These preferences do not change backend detection or Tower delivery. Acknowledgements are local review state. Service receipts mean the app processed an event; APNs HTTP 200 means accepted by Apple, not delivered or read. Alert-history catch-up while the app is open handles notifications received while the app was closed.

## Deliberately unavailable with today's feed

Public data has no coordinates, heading, groundspeed, vertical speed, hex, callsign, alert history, detailed poll/publication failures, provider recovery event or calendar sync timestamp. The app shows unavailable fields rather than claiming these exist. Actual scheduled-vs-unscheduled flags are honored; Skycare filtering only uses an explicit authorized backend flag. FlightBridge is the existing backend's scheduled list designation, not a separate API connection.

EventKit calendar comparison is deferred: the backend remains the sole schedule authority and no private calendar credentials were available. No independent flight-detection engine or ADS-B polling was added. Full private feed authentication, remote Live Activity updates, watch remote notifications, operational Head Quarter export, real-device accessibility/performance testing, App Store icons/signing review and end-to-end APNs delivery remain deployment work.

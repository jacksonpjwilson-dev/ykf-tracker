# First build on your MacBook Air

Use the Basic project first. It includes the real arrival board, flight details, airport map and rings, tracker/shift display, favourites, offline cache and local history/export. It reads the existing published backend; no backend installation, API key or notification service is required. The full project separately includes widgets, Watch and Live Activities.

## Download and open

1. Install Xcode 16 or newer, using a version compatible with your Mac's macOS and iPhone's installed iOS version. Launch it once, accept its license and install the iOS platform/runtime when prompted. Apple Silicon and Intel MacBook Air models both need a supported macOS/Xcode combination. iPhone deployment minimum is iOS 17.
2. In Terminal, clone the feature branch into a new folder (or fetch/switch in your existing clean clone):

   ```sh
   git clone --branch feature/native-ykf-arrivals https://github.com/jacksonpjwilson-dev/ykf-tracker.git
   cd ykf-tracker
   open apple/YKFBasic.xcodeproj
   ```

3. Select the **YKFArrivals** scheme and an installed iPhone simulator in Xcode's toolbar. Press **Command-B** to build, then **Command-R** to run. Simulator use does not require a signing team or an Apple Developer membership. Do not start the Python supervisor or native service.

The projects are checked in. No XcodeGen, CocoaPods or third-party Swift package download is required. `python3 apple/generate_project.py` regenerates both projects if necessary; it replaces generated build settings/schemes, so record any intended project setting changes in that generator before regenerating.

## Install Basic on your iPhone

1. Connect your iPhone by USB, unlock it and trust the Mac. In iPhone Settings > Privacy & Security, enable **Developer Mode** when prompted and follow the restart/confirmation steps.
2. In Xcode > Settings > Accounts, sign in with your Apple Account. A free **Personal Team** can generally sign the Basic app for personal testing; Apple's device/app limits and short provisioning period apply, so you may need to rebuild periodically. Paid membership is for advanced capabilities/distribution, not a prerequisite for reading the public board in Basic.
3. Select the **YKFArrivals target > Signing & Capabilities**, enable **Automatically manage signing**, and select your team. If the default bundle identifier cannot be registered by your team, change it to a unique identifier you own, such as `com.yourname.ykfarrivals`. Basic has only one target and no App Group or push entitlement to provision.
4. Choose your connected iPhone as the run destination, then press **Command-R**. Follow any developer-certificate trust prompts in iPhone Settings > General > VPN & Device Management.
5. Grant no notification permissions for the first board test. Basic explicitly reports that push and Live Activities require the full project; they are not needed to use the board.

The Basic and full projects use the same default bundle ID, so installing one replaces the other rather than creating two separate apps. Keep a consistent bundle ID if you want to retain sandbox history between builds. Changing it creates a different app/sandbox.

## Verify the board

- On internet, launch the app and confirm the **Published** timestamp and tracker status. A successful HTTP request does not make an old publication fresh. Off-shift status and an empty arrival list can be legitimate backend results.
- Open a published aircraft, if one exists, and inspect schedule ETA, status, distance, altitude and last-seen evidence. Missing fields must remain unavailable. `landed?` is shown as landing unconfirmed.
- Favourite an aircraft and verify the favourite filter. Change appearance between System, Light and Dark.
- Open Monitoring and verify times are shown in America/Toronto. An old/unknown timestamp must not be treated as a fresh backend heartbeat.
- After one successful sync, close the app, enable Airplane Mode, and relaunch. Cached information should remain readable with its original publication timestamp and an appropriate sync error. Restore connectivity and wait at least 60 seconds since the previous attempt before testing pull-to-refresh again.
- Map should show CYKF and distance rings. **Aircraft markers are currently unavailable because the public feed publishes no coordinates.** This is not an Xcode or MapKit setup error.
- Local history begins with the first sync. Its export contains publication evidence, not actual landing times.

Do not create operational alerts to test the interface.

## Automated tests on the Mac

From the repository root:

```sh
xcodebuild -version
swift test --package-path apple
xcodebuild -project apple/YKFBasic.xcodeproj -scheme YKFArrivals \
  -configuration Debug -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

The Swift tests belong to the standalone **YKFCore package**, not the application's run scheme. To run them using Xcode's Test navigator/Command-U, open `apple/Package.swift` in a separate Xcode window, choose the YKFCore package scheme and My Mac, then run tests. There is no application UI test target yet. Portable core tests do not verify SwiftData persistence, MapKit, notification permissions or device lifecycle behavior; use the manual checks above for those.

The unchanged tracker tests and optional service tests can also run in a separate virtual environment without starting any backend:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install pytest icalendar recurring-ical-events -r native_service/requirements.txt
.venv/bin/python -m pytest -q tests native_service/test_api.py
```

Do not commit `.venv/`. On a development Mac it is simplest to create this environment outside the checkout instead if you do not have a local ignore rule.

## Essential now versus later

| Requirement | Needed for Basic on iPhone? | When to configure |
| --- | --- | --- |
| Compatible Mac/macOS, Xcode and iOS 17+ iPhone | Yes | Before building |
| iOS platform support, trusted device and Developer Mode | Yes | First device run |
| Apple Account, signing team, unique bundle ID | Yes on device; not simulator | First device run |
| Internet for initial public-feed sync | Yes for current data | First launch; cache works afterward |
| A current backend publication | Needed for current operational information | Existing tracker supplies it; app cannot repair a stale source |
| Paid Developer membership | Not for Basic Personal Team testing | Advanced entitlements and distribution |
| App Groups and widget provisioning | No | Full project widgets and shared cache |
| Watch pairing, watchOS runtime and Watch signing | No | Watch companion |
| APNs key, push entitlement, HTTPS delivery service | No | Background arrival notifications and alert-history catch-up |
| Authenticated coordinate-bearing backend feed | No for board; yes for aircraft markers | Later privacy-reviewed integration |
| Remote Live Activity pushes, EventKit comparison, Head Quarter/G.I.L.E.S. | No | Later integration |

## Full project later

Open `apple/YKFArrivals.xcodeproj`, select YKFArrivals, install the watchOS platform if required, and sign all four targets with a suitable team. Each extension's bundle ID must be prefixed by its containing app's ID; if you change the phone ID, update all dependent IDs plus `WKCompanionAppBundleIdentifier` in `Watch/Info.plist`. Provision the same App Group named in the entitlement files and `Shared/SharedCache.swift` for each app/widget pair. Use [README.md](README.md) for full capability setup and [../native_service/README.md](../native_service/README.md) for future notification deployment. No backend deployment is part of this publication.

Build the full unsigned simulator configuration with:

```sh
xcodebuild -project apple/YKFArrivals.xcodeproj -scheme YKFArrivals \
  -configuration Debug -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO build
```

If Xcode reports a missing SDK/runtime, install that platform in Xcode Settings. Signing/provisioning errors are separate from source compiler errors; use the Basic simulator project to isolate the latter. This cloud workspace has no local Apple SDKs, so consult the feature branch GitHub Actions results and your Mac build log for the actual Apple compile outcome.

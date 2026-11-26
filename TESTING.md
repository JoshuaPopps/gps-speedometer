# Testing — GPS Speedometer

## Node tests for the lean-angle maths (`test.js`)

The app is a single-file PWA with no Node test harness for the UI — but
the lean-angle maths is pure functions, so `test.js` covers them in
plain Node with a `check(name, cond)` pattern. It extracts the shipped
functions (`wrapDeg`, `gravityFromOrientation`, `leanFromGravity`,
`smoothLean`, `nextMaxLean`, `fmtLean`) straight out of `index.html` by
name and evaluates them, so the test runs the code that ships, not a
copy that could drift. It also cross-checks the gravity formula against
a rotation matrix and round-trips real leans through
`Rz(alpha) Rx(beta) Ry(gamma)` — a 30° roll reads back as 30.00°.

The route-planning maths (v1.4.0) is covered the same way:
`mergeLegRoutes` (multi-leg stitching — joint point deduped, maneuver
positions and indices offset, intermediate arrive maneuvers become
continues), `planFuelStops` (one stop every range-minus-margin miles,
no stops without a usable range), and `fuelStopLabel` (nearest named
waypoint or turn street at or before the stop, inside 15 miles) are
extracted from `index.html` and tested on synthetic legs and routes.
The planned-route GPX export (v1.5.0) is covered the same way:
`buildPlanGpx` (waypoints for Here, each stop, and the destination;
the track follows the routed geometry), `routePointAtM` (nearest
routed point at or past an along-route distance, clamped to the last
point), and `planGpxFileName` (plan-<destination slug>-<timestamp>.gpx)
are extracted from `index.html` and tested on a synthetic merged
route. The file also asserts the Plan a route section and its library
citations are in the shipped page, and that the service-worker cache
is `gps-speedometer-v3`.

Run (from this directory):

```sh
node test.js
```

## Playwright UI tests (`test_ui.py`)

The app is a single-file PWA with no Node test harness: the tests drive the
real page in Chromium and fake the GPS, because the VM has no real GPS and
the app is still ride-unverified on a real phone (Precise location in
Chrome + a real ride are the test).

Setup (once):

```sh
python3 -m venv /tmp/pw2
/tmp/pw2/bin/pip install playwright
/tmp/pw2/bin/playwright install chromium
```

Run (from this directory):

```sh
/tmp/pw2/bin/python test_ui.py
```

What it covers:

1. Page loads, speed reads 0, Start Ride is present.
2. Start a ride with a fake GPS moving at 15 m/s: fixes arrive, trip
   distance and speed rise, the in-progress ride card appears.
3. Stop: the ride is saved to history with distance, duration, top
   speed, average and climb.
4. GPX export: the download is a valid GPX 1.1 file with the recorded
   track points (lat/lon/time/speed).
5. GPX import: a small GPX file lands in history as an imported ride.
6. Delete: removing rides one by one returns the list to empty.
7. Persistence: after a page reload the saved ride is still in history.
8. Existing behaviour intact: Simple view opens/closes, HUD mirror
   toggle flips, brightness slider applies, session max shows.
9. Navigation (mocked Valhalla / Nominatim / OSRM — the tests intercept
   those hosts and return canned routes, so no network is needed):
   - Place search → confirm → route: the arrow screen opens with the
     right-turn arrow, the street name, and the next step.
   - The distance to the next turn counts down as the fake GPS advances
     along the route.
   - Jumping past the corner changes the arrow to the destination.
   - The compass fallback note shows when no compass events arrive.
   - Jumping off the route shows the off-route state; Re-route fetches
     a new route and clears it.
   - OSRM fallback: with Valhalla returning 500, the route still loads
     via OSRM.
   - Cached route: after a reload with the servers unreachable, Resume
     opens the arrow screen and guidance still counts down, with no new
     route request.
   - GPX follow: an imported track with a real turn follows with the
     same arrow screen, fully offline.
   - Endpoint settings: pointing Valhalla at a custom URL sends the
     route request there; Reset restores the public defaults.
10. Lean angle (fake `devicemotion` / `deviceorientation` events — the
    VM has no motion sensor, and a real phone ride is still the test):
    - Calibrate waits for the first sensor event, then the tile reads
      live: upright 0°, a 30° right lean reads "30° R", a left lean
      reads "° L" once smoothing catches up.
    - Calibration survives a reload; Clear returns to uncalibrated.
    - Orientation fallback: calibrated at beta=60, the (48.59, 40.89)
      pair a real 30° lean produces reads back as "30° R".
    - Permission denied shows "Motion permission denied"; no sensor
      APIs at all shows "Not available on this device" — never a fake
      reading either way.
    - A ride with a 30° lean mid-ride saves "Lean 30°" in its history
      row (max lean only counts while rolling, 5 mph+).
11. Route planning (mocked Valhalla multi-leg + Nominatim — the
    geocode mock answers "Stop Town" and "Test Place" with different
    names so the legs can be told apart):
    - Add a stop, then a destination: the stop lands in the stop list,
      the destination line shows the place.
    - Plan with 2 mi per tank and a 0.5 mi early margin on the ~4.5 mi
      canned route: the summary shows the total (~4.5 mi), both legs
      with the stop and destination named, and two fuel stops — the
      first near the north leg's street, the second near the waypoint.
    - Navigate this plan opens the same arrow screen on the merged
      route.
    - The plan restores from the on-device cache after a reload with
      no new route request; Clear plan wipes the summary and the stop
      list.

The fake GPS is installed with `add_init_script` before page scripts run:
it replaces `navigator.geolocation.watchPosition` with an emitter that
walks a straight path north at 15 m/s, 700 ms per fix, with speed,
heading, altitude and accuracy on every fix. A pocket-dial start (under
5 track points and under 50 m) is not saved — the test records ~25 s so
the ride clears both bars. The navigation tests use a second fake GPS
that walks a scripted path (north 600 m, then east 470 m) with a
`window.__fakeGeoJump(lat, lon)` hook so the tests can teleport the
rider to the corner, past it, or off the route.

## Screenshots

`test_ui.py` also writes two 390 px phone screenshots — read them back
before calling the feature done:

- `gps-speedometer-rides-v1.png` — ride history with a recorded ride.
- `gps-speedometer-nav-v1.png` — the turn-by-turn arrow screen mid-route.

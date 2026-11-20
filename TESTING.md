# Testing — GPS Speedometer

## Playwright UI tests (`test_ui.py`)

The app is a single-file PWA with no Node test harness: the tests drive the
real page in Chromium and fake the GPS, because the VM has no real GPS and
the app is still ride-unverified on a real phone (Precise location in
Chrome + a real ride are the test).

Setup (once):

```sh
python3 -m venv /tmp/pw-render
/tmp/pw-render/bin/pip install playwright
/tmp/pw-render/bin/playwright install chromium
```

Run (from this directory):

```sh
/tmp/pw-render/bin/python test_ui.py
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

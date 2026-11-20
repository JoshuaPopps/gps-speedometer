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

The fake GPS is installed with `add_init_script` before page scripts run:
it replaces `navigator.geolocation.watchPosition` with an emitter that
walks a straight path north at 15 m/s, 700 ms per fix, with speed,
heading, altitude and accuracy on every fix. A pocket-dial start (under
5 track points and under 50 m) is not saved — the test records ~25 s so
the ride clears both bars.

## Screenshot

`test_ui.py` also writes `gps-speedometer-rides-v1.png` (390 px phone
width) showing the ride history with a recorded ride. Read it back
before calling the feature done.

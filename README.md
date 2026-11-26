# GPS Speedometer

A phone speedometer for the bike: big GPS speed readout, MPH/KM/H toggle, trip distance, max speed, moving average, ride time, heading + compass, altitude, GPS accuracy, and a Simple view that shows only the current speed. Works offline, no account.

**Ride recording (v1.1.0):** tap Start Ride and the route is recorded on the device — distance, duration, top speed, average, elevation gain — and saved to a ride history list when you tap Stop. Export any ride as a GPX file, or import a GPX track from another app. A ride cut short by a closed tab or a dead phone is recovered on next load. No account, no cloud, nothing leaves the phone.

**Turn-by-turn navigation (v1.2.0):** type a place in the Navigate section and the speedo guides you Beeline-style — one big arrow, the distance to the next turn counting down, the street name, what comes after, distance remaining, and ETA. The route comes from Valhalla (motorcycle profile) on OpenStreetMap data, with the OSRM demo server as fallback, and places are found with Nominatim. The fetched route is cached on the device, so guidance keeps working offline from the cache; re-routing needs signal. You can also follow any saved or imported GPX track with the same arrow screen — the turns are read out of the track geometry, fully offline. No compass mode, no crash detection.

Lean angle is intentionally not included: it needs the phone fixed to the bike in a known orientation plus a calibration step, and without that it reads nonsense. No crash detection, by design.

**Lean angle (v1.3.0):** the phone's motion sensor reads lean relative to a calibration you take with the bike straight up — tap the speed face → Settings → Lean angle → Calibrate upright. The Lean tile shows the live angle (right/left) and the max lean this ride, and saved rides keep their max lean. It only works if the phone stays fixed in one spot on the bike, same mount, same angle, every ride — move the phone and the reading is nonsense until you calibrate again. Where the sensor is missing or motion permission is denied, the tile says so plainly instead of faking a number. Max lean only counts while rolling (5 mph+). Still ride-unverified on a real phone.

**Route planning (v1.4.0):** the route half of the old route+fuel planner. In the Plan a route section, add stops between here and your destination (up to 8, in order), pick the destination, and plan: total distance, each leg with its distance and ride time, and where the fuel stops fall. Fuel stops come from your own miles per tank, minus a stop-early margin you set — the app never guesses a range for your bike. The whole plan (stops, destination, range, fetched route) is saved on the device, so it survives a reload and works offline from the cache; Navigate this plan hands the merged route to the same turn-by-turn arrow screen. The note under the planner cites published tank sizes from the garage library (1990 Softail FLSTC / 1995 Fat Boy FLSTF: 4.2 gal total, 0.75 gal reserve — Clymer Softail 1984–1999, Table 4, aftermarket; factory 1997–98 manual #99482-98 lists the same 4.2 gal total) so you can sanity-check your miles-per-tank number. Fuel logging itself is not here — that is the Dec 2 standalone push.

**Planned-route GPX export (v1.5.0):** once a plan has a route, Export GPX downloads the plan as a GPX 1.1 file — the track follows the routed geometry, and the waypoints are Here, each stop, and the destination, so another app or a GPS can follow the same plan. Built from the route the planner already holds; no new network calls.

**Send a ride to the Bike Log (v1.6.0):** any saved ride — or a planned route — can go to the Bike Maintenance Log's Rides tab as one entry: route, miles (one decimal), date, and the figures the ride actually recorded. Tap Send to Bike Log on the ride (or on the plan), pick which of the three roster bikes it was on — the speedo keeps no bike on a ride, so it asks every time and never guesses — and the Bike Log shows its own confirm card before writing anything. A planned route is labelled not-ridden-yet. Rides never move the odometer, on either side: the handoff carries no odometer figure at all.

## Privacy

What leaves the phone is only the route request itself — your position and the destination coordinates — sent to whichever routing server is configured. Nothing else leaves: no account, no identifier, no location history, no ride data. There is no analytics and no telemetry of any kind.

The routing servers are a setting (tap the speed face → Settings → Routing): the Valhalla URL, the OSRM fallback URL, and the Nominatim URL are each configurable, so the app can point at a self-hosted Valhalla with no code change. Defaults are the free public servers (Valhalla, then OSRM; Nominatim for place search). A self-hosted server removes the third party entirely.

## Honest limits

- The Valhalla and OSRM public servers are free community infrastructure — about one request a second, no uptime guarantee. A route fetch needs signal; guidance from a cached route does not; re-routing needs signal.
- Heading comes from the GPS course while rolling and the phone's compass when slow or stopped. If the browser won't give the compass, the app says so on the arrow screen and falls back to the last GPS course rather than showing a wrong arrow.
- The solid endgame is a self-hosted Valhalla on the Umbrel (Arizona map extract, Valhalla container, the speedo pointed at it over Tailscale) — that is a later job, not this one. Geocoding is separate: Valhalla routes coordinates only, so a self-hosted setup still needs a place search (Photon on the same box, or saved places as coordinates).

Live: https://joshuapopps.github.io/gps-speedometer/

Part of [Pocket Garage](https://joshuapopps.github.io/popps-garage/) — free, open source, built in the garage.

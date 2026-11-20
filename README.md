# GPS Speedometer

A phone speedometer for the bike: big GPS speed readout, MPH/KM/H toggle, trip distance, max speed, moving average, ride time, heading + compass, altitude, GPS accuracy, and a Simple view that shows only the current speed. Works offline, no account.

**Ride recording (v1.1.0):** tap Start Ride and the route is recorded on the device — distance, duration, top speed, average, elevation gain — and saved to a ride history list when you tap Stop. Export any ride as a GPX file, or import a GPX track from another app. A ride cut short by a closed tab or a dead phone is recovered on next load. No account, no cloud, nothing leaves the phone.

**Turn-by-turn navigation (v1.2.0):** type a place in the Navigate section and the speedo guides you Beeline-style — one big arrow, the distance to the next turn counting down, the street name, what comes after, distance remaining, and ETA. The route comes from Valhalla (motorcycle profile) on OpenStreetMap data, with the OSRM demo server as fallback, and places are found with Nominatim. The fetched route is cached on the device, so guidance keeps working offline from the cache; re-routing needs signal. You can also follow any saved or imported GPX track with the same arrow screen — the turns are read out of the track geometry, fully offline. No compass mode, no crash detection.

Lean angle is intentionally not included: it needs the phone fixed to the bike in a known orientation plus a calibration step, and without that it reads nonsense. No crash detection, by design.

## Privacy

What leaves the phone is only the route request itself — your position and the destination coordinates — sent to whichever routing server is configured. Nothing else leaves: no account, no identifier, no location history, no ride data. There is no analytics and no telemetry of any kind.

The routing servers are a setting (tap the speed face → Settings → Routing): the Valhalla URL, the OSRM fallback URL, and the Nominatim URL are each configurable, so the app can point at a self-hosted Valhalla with no code change. Defaults are the free public servers (Valhalla, then OSRM; Nominatim for place search). A self-hosted server removes the third party entirely.

## Honest limits

- The Valhalla and OSRM public servers are free community infrastructure — about one request a second, no uptime guarantee. A route fetch needs signal; guidance from a cached route does not; re-routing needs signal.
- Heading comes from the GPS course while rolling and the phone's compass when slow or stopped. If the browser won't give the compass, the app says so on the arrow screen and falls back to the last GPS course rather than showing a wrong arrow.
- The solid endgame is a self-hosted Valhalla on the Umbrel (Arizona map extract, Valhalla container, the speedo pointed at it over Tailscale) — that is a later job, not this one. Geocoding is separate: Valhalla routes coordinates only, so a self-hosted setup still needs a place search (Photon on the same box, or saved places as coordinates).

Live: https://joshuapopps.github.io/gps-speedometer/

Part of [Pocket Garage](https://joshuapopps.github.io/popps-garage/) — free, open source, built in the garage.

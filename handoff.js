/* Pocket Garage handoff — sending side (HANDOFF-FORMAT.md v1.3).
   Pure module: builds the envelope + URL for kind "ride-to-ride"
   (a speedo ride — recorded or planned — -> a ride entry on a bike
   in the Bike Maintenance Log's Rides tab). No DOM, no navigation —
   the app opens the URL.
   The speedo has no bike picker and a ride carries no bike, so the
   sender asks which bike at send time: buildEnvelope throws unless
   the caller passes one of the three roster bikes the user just
   picked, and the UI never pre-selects one. Never guess the bike
   from the route (a Tortilla Flat loop could be on any of them).
   The payload carries ONLY what the speedo actually recorded or
   planned — route, miles, date, notes — never invented data. Miles
   keep one decimal, the Rides tab convention. The receiver must not
   move the odometer either: a ride never moves it (deliberate —
   the logbook has no way to know whether those miles were on that
   bike's clock), and this sender sends no odometer figure at all. */
(function (root) {
  "use strict";
  /* Shared roster (HANDOFF-FORMAT.md section 5) — the same three
   * bikes as the Evo Diagnostic and the Dyno, same names, so the
   * receiver's name-match fallback sees the names it expects. */
  var ROSTER = [
    { rosterKey: "b1", name: "1995 Fat Boy Bobber", carb: "S&S Super E" },
    { rosterKey: "b2", name: "1990 Softail", carb: "Keihin CV40" },
    { rosterKey: "b3", name: "1947 Knucklehead", carb: "Linkert M74B" }
  ];
  var BIKE_LOG_URL = "https://joshuapopps.github.io/bike-maintenance-log-pwa/";
  var MI_M = 1609.344; /* metres in a mile */
  var MPH_PER_MS = 2.23694;

  function base64url(str) {
    var b64;
    if (typeof Buffer !== "undefined") b64 = Buffer.from(str, "utf8").toString("base64");
    else b64 = btoa(unescape(encodeURIComponent(str)));
    return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }
  function bikeForKey(rosterKey) {
    for (var i = 0; i < ROSTER.length; i++) {
      if (ROSTER[i].rosterKey === rosterKey) return ROSTER[i];
    }
    return null;
  }
  /* Miles, one decimal — the Rides tab convention. */
  function milesFromMeters(m) {
    return Math.round((m / MI_M) * 10) / 10;
  }
  /* Local calendar date (YYYY-MM-DD) for a timestamp — the same
   * local-time convention the app's GPX filenames use, so a ride
   * recorded at 11 PM in Phoenix is dated Nov 25, not Nov 26. */
  function dateISOFromMs(ms) {
    var d = new Date(ms);
    function p2(n) { return String(n).padStart(2, "0"); }
    return d.getFullYear() + "-" + p2(d.getMonth() + 1) + "-" + p2(d.getDate());
  }
  function fmtRideDuration(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h > 0) return h + "h " + (m < 10 ? "0" : "") + m + "m";
    return m + "m";
  }
  /* The route line the Rides tab shows for a recorded ride: an
   * imported ride keeps its own name; a recorded ride is named by
   * its day, the same "Ride Nov 25, 2026" shape the history list
   * already shows. */
  function rideLabel(ride) {
    if (ride.imported && ride.name) return ride.name;
    var d = new Date(ride.start);
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return "Ride " + months[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
  }
  /* Notes for a recorded ride: only figures the ride actually has.
   * Top speed and ride time always (a saved ride has both); climb
   * and max lean only when they are above zero, so a flat
   * no-lean ride does not get a "0 ft climb · 0° lean" line. */
  function recordedNotes(ride) {
    var bits = ["Recorded ride"];
    if (typeof ride.maxMs === "number" && ride.maxMs > 0) {
      bits.push("top " + Math.round(ride.maxMs * MPH_PER_MS) + " mph");
    }
    if (typeof ride.durationMs === "number" && ride.durationMs > 0) {
      bits.push(fmtRideDuration(ride.durationMs) + " riding");
    }
    if (typeof ride.gainM === "number" && ride.gainM > 0) {
      bits.push(Math.round(ride.gainM * 3.28084) + " ft climb");
    }
    if (typeof ride.maxLeanDeg === "number" && ride.maxLeanDeg > 0) {
      bits.push("max lean " + Math.round(ride.maxLeanDeg) + "°");
    }
    return bits.join(" · ");
  }
  /* The ride must be a finished recorded ride — the shape the app
   * saves in gpsSpeedo.rides.v1 (see finalizeRide in index.html).
   * Anything short of that is refused here, before a link is ever
   * built, so a bad record can never reach the receiver. */
  function validRecordedRide(ride) {
    if (!ride || typeof ride !== "object" || Array.isArray(ride)) return "that ride has no record in it";
    if (typeof ride.start !== "number" || isNaN(ride.start)) return "that ride has no start time";
    if (typeof ride.distanceM !== "number" || isNaN(ride.distanceM) || ride.distanceM < 0) return "that ride has no distance in it";
    if (milesFromMeters(ride.distanceM) <= 0) return "that ride has no miles in it";
    return null;
  }
  /* A finished recorded ride -> the ride payload the receiver takes.
   * Route is the ride's own name; miles are the GPS distance in
   * miles, one decimal; date is the day the ride started. */
  function recordedRidePayload(ride) {
    var problem = validRecordedRide(ride);
    if (problem) throw new Error("cannot send this ride: " + problem + ".");
    return {
      date: dateISOFromMs(ride.start),
      route: rideLabel(ride),
      miles: milesFromMeters(ride.distanceM),
      notes: recordedNotes(ride),
      kind: "recorded"
    };
  }
  /* A planned route -> the ride payload the receiver takes. The
   * route string is the stops in order, Here first, destination
   * last — the same names the planner shows. The date is the day
   * the plan was fetched: the ride has not happened yet, and the
   * notes say so plainly, so the log entry never reads as a ride
   * already ridden. Miles are the planned total, one decimal. */
  function plannedRidePayload(route) {
    if (!route || typeof route !== "object" || Array.isArray(route)) throw new Error("cannot send this plan: that plan has no route in it.");
    if (!route.points || !Array.isArray(route.points) || route.points.length < 2) throw new Error("cannot send this plan: that plan has no routed line in it.");
    if (typeof route.totalM !== "number" || isNaN(route.totalM) || route.totalM < 0) throw new Error("cannot send this plan: that plan has no distance in it.");
    var miles = milesFromMeters(route.totalM);
    if (miles <= 0) throw new Error("cannot send this plan: that plan has no miles in it.");
    var names = ["Here"];
    (route.waypoints || []).forEach(function (w) { if (w && w.name) names.push(w.name); });
    names.push(route.destName || "Destination");
    var when = (typeof route.fetchedAt === "number" && !isNaN(route.fetchedAt)) ? route.fetchedAt : Date.now();
    return {
      date: dateISOFromMs(when),
      route: "Planned: " + names.join(" → "),
      miles: miles,
      notes: "Planned route — not ridden yet.",
      kind: "planned"
    };
  }
  /* The payload the receiver validates: only what the speedo
   * recorded or planned, nothing invented. Route is capped at 200
   * characters and notes at 500 so a long GPX name can never push
   * the link past the 8,000-character limit. */
  function validRidePayload(p) {
    if (!p || typeof p !== "object" || Array.isArray(p)) return "that ride has no record in it";
    if (typeof p.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(p.date)) return "that ride has no usable date";
    if (typeof p.route !== "string" || !p.route.trim()) return "that ride names no route";
    if (p.route.length > 200) return "that ride's route is too long to send";
    if (typeof p.miles !== "number" || isNaN(p.miles) || !(p.miles > 0)) return "that ride has no miles in it";
    if (typeof p.notes !== "string") return "that ride has bad notes";
    if (p.notes.length > 500) return "that ride's notes are too long to send";
    if (p.kind !== "recorded" && p.kind !== "planned") return "that ride is neither recorded nor planned";
    return null;
  }
  function buildEnvelope(ridePayload, bike) {
    var problem = validRidePayload(ridePayload);
    if (problem) throw new Error("cannot send this ride: " + problem + ".");
    /* No bike, no send. The speedo stores no bike on a ride and
     * has no default, no "most recent", and no route inference —
     * the caller must pass a roster bike the user just picked. */
    var rosterBike = bike && bike.rosterKey ? bikeForKey(bike.rosterKey) : null;
    if (!rosterBike) {
      throw new Error("Pick which bike this ride is for first — the speedo does not know which bike a ride belongs to.");
    }
    return {
      format: "pocket-garage-handoff", version: 1, kind: "ride-to-ride",
      sentAt: new Date().toISOString(), from: "gps-speedometer",
      bike: { rosterKey: rosterBike.rosterKey, name: rosterBike.name },
      payload: {
        ride: {
          date: ridePayload.date,
          route: ridePayload.route.trim(),
          miles: ridePayload.miles,
          notes: ridePayload.notes,
          kind: ridePayload.kind
        }
      }
    };
  }
  function buildUrl(ridePayload, bike) {
    var env = buildEnvelope(ridePayload, bike);
    var json = JSON.stringify(env);
    if (json.length > 8000) throw new Error("handoff payload over 8,000 characters");
    return BIKE_LOG_URL + "?handoff=" + base64url(json);
  }
  var api = {
    ROSTER: ROSTER, BIKE_LOG_URL: BIKE_LOG_URL,
    base64url: base64url, bikeForKey: bikeForKey,
    milesFromMeters: milesFromMeters, dateISOFromMs: dateISOFromMs,
    rideLabel: rideLabel, recordedNotes: recordedNotes,
    validRecordedRide: validRecordedRide,
    recordedRidePayload: recordedRidePayload,
    plannedRidePayload: plannedRidePayload,
    validRidePayload: validRidePayload,
    buildEnvelope: buildEnvelope, buildUrl: buildUrl
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Speedo_Handoff = api;
})(typeof window !== "undefined" ? window : globalThis);

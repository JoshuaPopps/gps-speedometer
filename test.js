#!/usr/bin/env node
/* Node tests for the GPS Speedometer lean-angle maths (v1.3.0), the
 * route-planning maths (v1.4.0: multi-leg merge, fuel-stop planning),
 * and the planned-route GPX export (v1.5.0).
 *
 * The app is a single-file PWA with no Node harness of its own — the UI
 * behaviour lives in test_ui.py (Playwright). This file covers the pure
 * logic, and it tests the SHIPPED code: the functions are extracted
 * from index.html by name and evaluated, so a drift between the app and
 * the test fails loudly instead of silently passing on a copy.
 *
 * Run from this directory:  node test.js
 */
'use strict';
var fs = require('fs');
var path = require('path');

var results = [];
function check(name, cond, detail) {
  results.push([name, !!cond]);
  console.log((cond ? 'PASS ' : 'FAIL ') + name + (detail !== undefined ? ' — ' + detail : ''));
  return !!cond;
}
function approx(a, b, tol) { return Math.abs(a - b) <= tol; }

/* ---- Extract a top-level `function NAME(...) {...}` from index.html ---- */
function extractFn(src, name) {
  var start = src.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('function ' + name + ' not found in index.html');
  var brace = src.indexOf('{', start);
  var i = brace, depth = 0;
  var inStr = null, inLineComment = false, inBlockComment = false;
  while (i < src.length) {
    var c = src[i], n = src[i + 1];
    if (inLineComment) {
      if (c === '\n') inLineComment = false;
    } else if (inBlockComment) {
      if (c === '*' && n === '/') { inBlockComment = false; i++; }
    } else if (inStr) {
      if (c === '\\') i++;
      else if (c === inStr) inStr = null;
    } else if (c === '/' && n === '/') inLineComment = true;
    else if (c === '/' && n === '*') inBlockComment = true;
    else if (c === "'" || c === '"' || c === '`') inStr = c;
    else if (c === '{') depth++;
    else if (c === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
    i++;
  }
  throw new Error('unbalanced braces in ' + name);
}

var html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
var fns = ['wrapDeg', 'gravityFromOrientation', 'leanFromGravity', 'smoothLean', 'nextMaxLean', 'fmtLean',
  'haversine', 'mergeLegRoutes', 'planFuelStops', 'fuelStopLabel',
  'escXml', 'isoTime', 'routePointAtM', 'buildPlanGpx', 'planGpxFileName']
  .map(function (n) { return extractFn(html, n); })
  .join('\n');
// new Function (not eval): strict-mode eval would scope the declarations
// away, this returns them as usable bindings instead.
var leanFns = new Function(fns + '\nreturn { wrapDeg: wrapDeg, gravityFromOrientation: gravityFromOrientation, leanFromGravity: leanFromGravity, smoothLean: smoothLean, nextMaxLean: nextMaxLean, fmtLean: fmtLean, haversine: haversine, mergeLegRoutes: mergeLegRoutes, planFuelStops: planFuelStops, fuelStopLabel: fuelStopLabel, escXml: escXml, isoTime: isoTime, routePointAtM: routePointAtM, buildPlanGpx: buildPlanGpx, planGpxFileName: planGpxFileName };')();
var wrapDeg = leanFns.wrapDeg, gravityFromOrientation = leanFns.gravityFromOrientation,
  leanFromGravity = leanFns.leanFromGravity, smoothLean = leanFns.smoothLean,
  nextMaxLean = leanFns.nextMaxLean, fmtLean = leanFns.fmtLean,
  haversine = leanFns.haversine, mergeLegRoutes = leanFns.mergeLegRoutes,
  planFuelStops = leanFns.planFuelStops, fuelStopLabel = leanFns.fuelStopLabel,
  escXml = leanFns.escXml, isoTime = leanFns.isoTime,
  routePointAtM = leanFns.routePointAtM, buildPlanGpx = leanFns.buildPlanGpx,
  planGpxFileName = leanFns.planGpxFileName;

/* ---- wrapDeg ---- */
check('wrapDeg 370 -> 10', wrapDeg(370) === 10);
check('wrapDeg -190 -> 170', wrapDeg(-190) === 170);
check('wrapDeg 180 stays 180', wrapDeg(180) === 180);
check('wrapDeg -180 stays -180', wrapDeg(-180) === -180);
check('wrapDeg 540 -> 180', wrapDeg(540) === 180);
check('wrapDeg 0 -> 0', wrapDeg(0) === 0);

/* ---- gravityFromOrientation: known device poses (unit gravity) ---- */
function gClose(got, x, y, z, tol) {
  return approx(got.x, x, tol) && approx(got.y, y, tol) && approx(got.z, z, tol);
}
var SQ = Math.SQRT1_2; // 0.7071
check('flat face-up beta=0 gamma=0 -> gravity straight down the screen',
  gClose(gravityFromOrientation(0, 0), 0, 0, -1, 1e-9));
check('upright portrait beta=90 gamma=0 -> gravity down the screen',
  gClose(gravityFromOrientation(90, 0), 0, -1, 0, 1e-9));
check('upside-down beta=-90 -> gravity up the screen',
  gClose(gravityFromOrientation(-90, 0), 0, 1, 0, 1e-9));
check('on its right side gamma=90 -> gravity across the screen',
  gClose(gravityFromOrientation(0, 90), 1, 0, 0, 1e-9));
check('on its left side gamma=-90',
  gClose(gravityFromOrientation(0, -90), -1, 0, 0, 1e-9));
check('tilted back 45deg beta=45 gamma=0',
  gClose(gravityFromOrientation(45, 0), 0, -SQ, -SQ, 1e-9));
check('alpha never changes gravity (compass cannot skew lean)',
  gClose(gravityFromOrientation(0, 0), 0, 0, -1, 1e-9) &&
  (function () {
    // gravityFromOrientation takes no alpha by design; the formula's
    // third row of R has no alpha term — assert the signature has 2 params.
    return gravityFromOrientation.length === 2;
  })());

/* ---- leanFromGravity: exact angles from direct gravity vectors ---- */
function vec(deg) { // gravity vector for a lean of `deg` off upright (0,-1)
  var r = deg * Math.PI / 180;
  return { x: Math.sin(r), y: -Math.cos(r), z: 0 };
}
var REF = { x: 0, y: -1, z: 0 };
check('upright vs upright -> 0', leanFromGravity(REF, REF) === 0);
check('30deg right -> +30', approx(leanFromGravity(vec(30), REF), 30, 1e-9), leanFromGravity(vec(30), REF));
check('30deg left -> -30', approx(leanFromGravity(vec(-30), REF), -30, 1e-9));
check('90deg right -> +90', approx(leanFromGravity(vec(90), REF), 90, 1e-9));
check('90deg left -> -90', approx(leanFromGravity(vec(-90), REF), -90, 1e-9));
check('upside down -> 180 either way', Math.abs(leanFromGravity(vec(180), REF)) === 180);
check('small lean 5deg reads 5', approx(leanFromGravity(vec(5), REF), 5, 1e-9));
check('lean is relative: ref itself leaned 20, live 50 -> 30', (function () {
  return approx(leanFromGravity(vec(50), vec(20)), 30, 1e-9);
})());
check('flat phone (no screen-plane projection) -> null, never a fake number',
  leanFromGravity({ x: 0, y: 0, z: -1 }, REF) === null);
check('flat reference -> null', leanFromGravity(REF, { x: 0, y: 0, z: -1 }) === null);
check('near-flat projection under the floor -> null',
  leanFromGravity({ x: 0.1, y: 0.1, z: -1 }, REF) === null);

/* ---- smoothLean / nextMaxLean / fmtLean ---- */
check('smoothLean first sample passes through', smoothLean(null, 30, 0.35) === 30);
check('smoothLean null next keeps previous', smoothLean(30, null, 0.35) === 30);
check('smoothLean 0 -> 30 at k=0.5 is 15', smoothLean(0, 30, 0.5) === 15);
check('smoothLean 10 -> 20 at k=0.35 is 13.5', approx(smoothLean(10, 20, 0.35), 13.5, 1e-9));
check('max lean counts while rolling', nextMaxLean(0, 30, 20) === 30);
check('max lean keeps the bigger one', nextMaxLean(30, 20, 20) === 30);
check('max lean ignores parked wiggling (under 8 km/h)', nextMaxLean(0, 30, 5) === 0);
check('max lean uses magnitude, left counts too', nextMaxLean(0, -25, 30) === 25);
check('max lean ignores null', nextMaxLean(10, null, 30) === 10);
check('fmtLean 30 -> 30deg R', fmtLean(30) === '30° R');
check('fmtLean -12 -> 12deg L', fmtLean(-12) === '12° L');
check('fmtLean 0 -> 0deg', fmtLean(0) === '0°');
check('fmtLean null -> dash', fmtLean(null) === '—');

/* ---- Rotation-matrix cross-checks ----
 * Build R = Rz(alpha) Rx(beta) Ry(gamma) directly and check the gravity
 * formula against the matrix, then round-trip a real lean: start at a
 * known orientation, roll the device about its forward axis by theta,
 * extract the new beta/gamma, and require the lean functions to read
 * back theta. This proves the DeviceOrientationEvent fallback maths is
 * exact, not just plausible — the only unverified part left is what a
 * real phone's sensor reports, which needs a ride. */
function matMul(A, B) {
  var C = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (var i = 0; i < 3; i++) for (var j = 0; j < 3; j++)
    for (var k = 0; k < 3; k++) C[i][j] += A[i][k] * B[k][j];
  return C;
}
function rotZ(a) {
  var c = Math.cos(a), s = Math.sin(a);
  return [[c, -s, 0], [s, c, 0], [0, 0, 1]];
}
function rotX(a) {
  var c = Math.cos(a), s = Math.sin(a);
  return [[1, 0, 0], [0, c, -s], [0, s, c]];
}
function rotY(a) {
  var c = Math.cos(a), s = Math.sin(a);
  return [[c, 0, s], [0, 1, 0], [-s, 0, c]];
}
function R_fromABG(alpha, beta, gamma) {
  var r = Math.PI / 180;
  return matMul(matMul(rotZ(alpha * r), rotX(beta * r)), rotY(gamma * r));
}
function gravityFromMatrix(R) { // -1 times the third row of R
  return { x: -R[2][0], y: -R[2][1], z: -R[2][2] };
}
function betaGammaFromMatrix(R) {
  return {
    beta: Math.asin(Math.max(-1, Math.min(1, R[2][1]))) * 180 / Math.PI,
    gamma: Math.atan2(-R[2][0], R[2][2]) * 180 / Math.PI
  };
}
var D = Math.PI / 180;
[[0, 0, 0], [40, 60, 0], [120, 45, -20], [300, 75, 30]].forEach(function (t) {
  var R = R_fromABG(t[0], t[1], t[2]);
  var fromMatrix = gravityFromMatrix(R);
  var fromFn = gravityFromOrientation(t[1], t[2]);
  check('gravity formula matches rotation matrix at alpha=' + t[0] + ' beta=' + t[1] + ' gamma=' + t[2],
    gClose(fromFn, fromMatrix.x, fromMatrix.y, fromMatrix.z, 1e-9));
});
[[0, 60, 0, 30], [30, 60, 0, 30], [0, 45, 0, -20], [120, 70, 10, 45],
 [0, 60, 0, -45], [200, 50, -15, 15], [0, 80, 0, 25]].forEach(function (t) {
  var a0 = t[0], b0 = t[1], g0 = t[2], theta = t[3];
  var R0 = R_fromABG(a0, b0, g0);
  // Lean right by theta: device rolls about its own forward axis (-z).
  var R1 = matMul(R0, rotZ(-theta * D));
  var bg1 = betaGammaFromMatrix(R1);
  var gRef = gravityFromOrientation(b0, g0);
  var gLive = gravityFromOrientation(bg1.beta, bg1.gamma);
  var got = leanFromGravity(gLive, gRef);
  check('orientation round-trip reads back a ' + theta + 'deg lean (from beta=' + b0 + ' gamma=' + g0 + ')',
    approx(got, theta, 0.5), 'got ' + (got == null ? 'null' : got.toFixed(2)));
});
[[0, 60, 0, 30], [90, 70, -10, -35]].forEach(function (t) {
  // Same round-trip, but lean from the matrix gravity directly — isolates
  // the lean maths from beta/gamma extraction.
  var R0 = R_fromABG(t[0], t[1], t[2]);
  var R1 = matMul(R0, rotZ(-t[3] * D));
  var got = leanFromGravity(gravityFromMatrix(R1), gravityFromMatrix(R0));
  check('matrix-gravity round-trip reads back ' + t[3] + 'deg',
    approx(got, t[3], 1e-9), 'got ' + (got == null ? 'null' : got.toFixed(4)));
});

/* ---- Route planning: multi-leg merge + fuel stops (v1.4.0) ---- */
var MI = 1609.344;
function legPts(lat0, lat1) {
  // Two points 0.001 deg apart (~111 m), cum built from haversine.
  var pts = [{ lat: lat0, lon: -112.0 }, { lat: lat1, lon: -112.0 }];
  var d = haversine(lat0, -112.0, lat1, -112.0);
  return { points: pts, cum: [0, d],
    maneuvers: [{ kind: 'straight', street: 'North Rd', atM: 0, atIdx: 0 },
                { kind: 'arrive', street: '', atM: d, atIdx: 1 }],
    totalM: d, totalS: 60 };
}
(function () {
  var leg1 = legPts(33.0, 33.001), leg2 = legPts(33.001, 33.002);
  leg2.maneuvers[0].street = 'East Rd';
  var merged = mergeLegRoutes([leg1, leg2]);
  var d = leg1.totalM;
  check('mergeLegRoutes dedups the joint point', merged.points.length === 3);
  check('mergeLegRoutes total is both legs', approx(merged.totalM, 2 * d, 0.01), merged.totalM.toFixed(2));
  check('mergeLegRoutes sums ride time', merged.totalS === 120);
  check('mergeLegRoutes keeps both boundaries', merged.boundaries.length === 1 && approx(merged.boundaries[0], d, 0.01));
  check('mergeLegRoutes drops the second leg depart, keeps its arrive',
    merged.maneuvers.length === 3 &&
    merged.maneuvers[0].kind === 'straight' && merged.maneuvers[0].street === 'North Rd' &&
    merged.maneuvers[1].kind === 'straight' && // intermediate arrive -> continue
    merged.maneuvers[2].kind === 'arrive',
    merged.maneuvers.map(function (m) { return m.kind; }).join(','));
  check('mergeLegRoutes offsets maneuver distances onto the combined line',
    approx(merged.maneuvers[2].atM, 2 * d, 0.01) && approx(merged.maneuvers[1].atM, d, 0.01));
  check('mergeLegRoutes offsets maneuver indices onto the combined line',
    merged.maneuvers[2].atIdx === 2 && merged.maneuvers[1].atIdx === 1);
  check('mergeLegRoutes single leg passes through',
    mergeLegRoutes([leg1]).points.length === 2 && mergeLegRoutes([leg1]).maneuvers[1].kind === 'arrive');
  check('mergeLegRoutes throws on an empty route', (function () {
    try { mergeLegRoutes([]); return false; } catch (e) { return true; }
  })());
})();

/* ---- planFuelStops: one stop every (range - margin) miles ---- */
check('fuel stops every 130 mi on a 500 mi route (150 range, 20 margin)',
  (function () {
    var s = planFuelStops(500 * MI, 150, 20);
    return s.length === 3 && approx(s[0], 130 * MI, 1) && approx(s[1], 260 * MI, 1) && approx(s[2], 390 * MI, 1);
  })(), JSON.stringify(planFuelStops(500 * MI, 150, 20).map(function (m) { return Math.round(m / MI); })));
check('fuel stops: route shorter than the range needs none', planFuelStops(100 * MI, 150, 20).length === 0);
check('fuel stops: a stop exactly at the destination is not a stop',
  planFuelStops(260 * MI, 150, 20).length === 1, JSON.stringify(planFuelStops(260 * MI, 150, 20).map(function (m) { return Math.round(m / MI); })));
check('fuel stops: no range entered means no stops', planFuelStops(500 * MI, '', 20).length === 0);
check('fuel stops: zero range means no stops', planFuelStops(500 * MI, 0, 20).length === 0);
check('fuel stops: margin that eats the whole range means no stops',
  planFuelStops(500 * MI, 150, 150).length === 0 && planFuelStops(500 * MI, 150, 200).length === 0);
check('fuel stops: no margin means stops at the full range',
  (function () {
    var s = planFuelStops(500 * MI, 150, 0);
    return s.length === 3 && approx(s[0], 150 * MI, 1);
  })());
check('fuel stops: margin defaults sensibly when blank',
  (function () {
    var s = planFuelStops(500 * MI, 150, '');
    return s.length === 3 && approx(s[0], 150 * MI, 1);
  })());

/* ---- fuelStopLabel: nearest named point at or before the stop ---- */
(function () {
  var route = {
    waypoints: [{ name: 'Wickenburg', atM: 100 * MI }],
    maneuvers: [
      { kind: 'straight', street: 'US-60', atM: 50 * MI },
      { kind: 'right', street: 'Main St', atM: 120 * MI },
      { kind: 'arrive', street: '', atM: 200 * MI }
    ]
  };
  check('fuel stop label picks the closest named point before the stop',
    fuelStopLabel(route, 130 * MI) === 'Main St', fuelStopLabel(route, 130 * MI));
  check('fuel stop label names the waypoint when it is the closest',
    fuelStopLabel(route, 105 * MI) === 'Wickenburg', fuelStopLabel(route, 105 * MI));
  check('fuel stop label is null when nothing is named before the stop',
    fuelStopLabel(route, 10 * MI) === null);
  check('fuel stop label ignores names more than 15 mi back',
    fuelStopLabel(route, 180 * MI) === null, fuelStopLabel(route, 180 * MI));
  check('fuel stop label skips unnamed maneuvers',
    fuelStopLabel({ waypoints: [], maneuvers: [{ kind: 'straight', street: '', atM: 5 * MI }] }, 10 * MI) === null);
})();

/* ---- Planned-route GPX export (v1.5.0) ---- */
(function () {
  // A merged two-leg route, built the way the planner builds one:
  // leg 1 north ~2.5 mi, leg 2 east ~2.0 mi, one waypoint at the joint.
  var leg1 = legPts(33.0, 33.001), leg2 = legPts(33.001, 33.002);
  leg2.maneuvers[0].street = 'East Rd';
  var route = mergeLegRoutes([leg1, leg2]);
  route.destName = 'Test Place, Glendale, AZ';
  route.source = 'valhalla';
  route.fetchedAt = Date.UTC(2026, 10, 25, 12, 30);
  route.legs = [{ name: 'Stop Town', distM: leg1.totalM, durS: 300 },
                { name: 'Test Place, Glendale, AZ', distM: leg2.totalM, durS: 300 }];
  route.waypoints = [{ name: 'Stop Town', atM: leg1.totalM }];
  route.totalM = leg1.totalM + leg2.totalM;
  var d = leg1.totalM;
  check('routePointAtM finds the joint point at the boundary',
    (function () {
      var p = routePointAtM(route, d);
      return p && approx(p.lat, 33.001, 1e-9) && approx(p.lon, -112.0, 1e-9);
    })());
  check('routePointAtM clamps past-the-end distances to the last point',
    (function () {
      var p = routePointAtM(route, 1e9);
      return p && approx(p.lat, 33.002, 1e-9);
    })());
  check('routePointAtM returns the first point at distance 0',
    (function () {
      var p = routePointAtM(route, 0);
      return p && approx(p.lat, 33.0, 1e-9);
    })());
  var gpx = buildPlanGpx(route);
  check('plan GPX is valid GPX 1.1 from this app',
    gpx.indexOf('<?xml version="1.0" encoding="UTF-8"?>') === 0 &&
    gpx.indexOf('<gpx version="1.1" creator="GPS Speedometer"') !== -1);
  check('plan GPX names the route after the destination',
    gpx.indexOf('<name>Planned route to Test Place, Glendale, AZ</name>') !== -1);
  check('plan GPX carries the fetched time in metadata',
    gpx.indexOf('<time>2026-11-25T12:30:00.000Z</time>') !== -1);
  check('plan GPX has all three waypoints: Here, stop, destination',
    (function () {
      var wpts = gpx.match(/<wpt /g) || [];
      return wpts.length === 3 &&
        gpx.indexOf('<name>Here</name>') !== -1 &&
        gpx.indexOf('<name>Stop Town</name>') !== -1 &&
        gpx.indexOf('<name>Test Place, Glendale, AZ</name>') !== -1;
    })(), (gpx.match(/<wpt /g) || []).length + ' wpts');
  check('plan GPX stop waypoint sits at the leg joint, not the geocode guess',
    gpx.indexOf('<wpt lat="33.001000" lon="-112.000000"><name>Stop Town</name></wpt>') !== -1);
  check('plan GPX destination waypoint is the last routed point',
    gpx.indexOf('<wpt lat="33.002000" lon="-112.000000"><name>Test Place, Glendale, AZ</name></wpt>') !== -1);
  check('plan GPX track follows the routed geometry, every point',
    (gpx.match(/<trkpt /g) || []).length === route.points.length,
    (gpx.match(/<trkpt /g) || []).length + ' trkpts, route has ' + route.points.length);
  check('plan GPX escapes waypoint names with XML specials',
    (function () {
      var r2 = { points: [{ lat: 33.0, lon: -112.0 }, { lat: 33.001, lon: -112.0 }],
        cum: [0, 111], waypoints: [{ name: 'Tom & Jerry\'s <Cafe>', atM: 111 }],
        destName: 'A & B', fetchedAt: Date.UTC(2026, 10, 25) };
      var g2 = buildPlanGpx(r2);
      return g2.indexOf('Tom &amp; Jerry') !== -1 && g2.indexOf('A &amp; B') !== -1 &&
        g2.indexOf('<Cafe>') === -1;
    })());
  check('plan GPX with no intermediate stops has just Here + destination',
    (function () {
      var r3 = { points: [{ lat: 33.0, lon: -112.0 }, { lat: 33.001, lon: -112.0 }],
        cum: [0, 111], waypoints: [], destName: 'Solo', fetchedAt: Date.UTC(2026, 10, 25) };
      var g3 = buildPlanGpx(r3);
      return (g3.match(/<wpt /g) || []).length === 2;
    })());
  // Filename timestamps are local time (the app formats with local getters),
  // so the expected values must be built the same way — hardcoding UTC
  // strings fails in any non-UTC timezone (caught Nov 25: 100/100 in UTC,
  // 98/100 in America/Phoenix).
  function stamp(ms){ var d = new Date(ms); function p2(n){ return String(n).padStart(2, '0'); } return d.getFullYear() + p2(d.getMonth() + 1) + p2(d.getDate()) + '-' + p2(d.getHours()) + p2(d.getMinutes()); }
  check('plan GPX filename slugs the destination and timestamps it',
    planGpxFileName(route) === 'plan-test-place-glendale-az-' + stamp(route.fetchedAt) + '.gpx',
    planGpxFileName(route));
  check('plan GPX filename falls back to "route" with no destination',
    planGpxFileName({ fetchedAt: Date.UTC(2026, 10, 25, 9, 5) }) === 'plan-route-' + stamp(Date.UTC(2026, 10, 25, 9, 5)) + '.gpx',
    planGpxFileName({ fetchedAt: Date.UTC(2026, 10, 25, 9, 5) }));
})();

/* ---- Route planning is in the shipped page, with cited sources ---- */
check('index.html has the Plan a route section', html.indexOf('id="planRouteBtn"') !== -1 && html.indexOf('Plan a route') !== -1);
check('index.html has the Export GPX button in the planner', html.indexOf('id="planGpxBtn"') !== -1 && html.indexOf('Export GPX') !== -1);
check('index.html cites the Clymer tank table (labeled aftermarket)', html.indexOf('Clymer Softail 1984') !== -1 && html.indexOf('aftermarket, not factory') !== -1);
check('index.html cites the factory Softail manual 99482-98', html.indexOf('#99482-98') !== -1);
check('index.html cites the factory Sportster manual 99484-98', html.indexOf('#99484-98') !== -1);
check('index.html never hardcodes a tank size into the planner', html.indexOf('planRangeMi') !== -1 && html.indexOf('Miles per tank') !== -1);
check('APP_VERSION is 1.5.0', html.indexOf("APP_VERSION = '1.5.0'") !== -1);

/* --- Service-worker update button (wired Nov 25, 2026) --- */
(function () {
  var swSrc = fs.readFileSync(path.join(__dirname, 'sw.js'), 'utf8');
  var appjs = html; // single-file app: the inline script is the app
  check('sw.js cache is gps-speedometer-v3', swSrc.indexOf('gps-speedometer-v3') !== -1);
  check('sw.js precaches sw-update.js', swSrc.indexOf('sw-update.js') !== -1);
  check('sw.js has the SKIP_WAITING message handler', swSrc.indexOf("addEventListener('message'") !== -1 && swSrc.indexOf('SKIP_WAITING') !== -1);
  check('sw.js skipWaiting runs only from the message handler, never on install', swSrc.split('skipWaiting()').length - 1 === 1);
  check('index.html loads sw-update.js before the inline main script', html.indexOf('<script src="sw-update.js"></script>') !== -1 && html.indexOf('sw-update.js') < html.indexOf('<script>\n(function(){'));
  check('PG_SWUpdate.init is called at startup', appjs.indexOf("PG_SWUpdate.init({ container: document.querySelector('.topbar') })") !== -1);
  check('vendored sw-update.js is byte-identical to canonical', fs.readFileSync(path.join(__dirname, 'sw-update.js'), 'utf8') === fs.readFileSync(path.join(__dirname, '..', '..', 'sw-update.js'), 'utf8'));
})();

var failed = results.filter(function (r) { return !r[1]; });
console.log('\n' + (results.length - failed.length) + ' passed, ' + failed.length + ' failed.');
process.exit(failed.length ? 1 : 0);

#!/usr/bin/env python3
"""Playwright UI tests for GPS Speedometer (ride recording + GPX + navigation).

The VM has no real GPS, so the tests install a fake navigator.geolocation
before page scripts run: it walks a straight path north at 15 m/s, one fix
every 700 ms, with speed / heading / altitude / accuracy on every fix.

Navigation tests intercept Nominatim / Valhalla / OSRM and return canned
routes, so no network is needed; a second fake GPS walks a scripted path
(north 600 m, then east 470 m) with a window.__fakeGeoJump(lat, lon) hook.
Route-planning tests use a canned two-leg Valhalla route (north ~2.5 mi,
then east ~2.0 mi) and check the plan summary, the legs, and the fuel stops.

Run from this directory:
    /tmp/pw-render/bin/python test_ui.py

Also writes ~/workspace/gps-speedometer-rides-v1.png (ride history) and
~/workspace/gps-speedometer-nav-v1.png (arrow screen mid-route).
Read both back before calling the feature done.
"""
import http.server
import json
import math
import os
import re
import socketserver
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.path.expanduser("~/workspace")
SHOT = os.path.join(WS, "gps-speedometer-rides-v1.png")
IMPORT_GPX = "/tmp/speedo-import-test.gpx"

FAKE_GEO = """
Object.defineProperty(navigator, 'geolocation', { value: {
  _watchers: {}, _nextId: 1, _timer: null,
  _start: function(){
    var self = this;
    if (self._timer) return;
    var lat = 33.5658, lon = -112.1646, alt = 300, i = 0;
    var step = 15 * 0.7 / 111320; // ~10.5 m per 700 ms fix
    self._timer = setInterval(function(){
      i++; lat += step; alt += 0.3;
      var pos = { coords: { latitude: lat, longitude: lon, altitude: alt,
        accuracy: 5, altitudeAccuracy: 8, heading: 0, speed: 15 },
        timestamp: Date.now() };
      Object.keys(self._watchers).forEach(function(k){ self._watchers[k](pos); });
    }, 700);
  },
  watchPosition: function(success, error, options){
    var id = this._nextId++;
    this._watchers[id] = success;
    this._start();
    return id;
  },
  clearWatch: function(id){ delete this._watchers[id]; },
  getCurrentPosition: function(success){
    success({ coords: { latitude: 33.5658, longitude: -112.1646, altitude: 300,
      accuracy: 5, altitudeAccuracy: 8, heading: 0, speed: 0 }, timestamp: Date.now() });
  }
}});
"""

IMPORT_GPX_TEXT = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <metadata><name>Test import ride</name><time>2026-11-20T10:00:00Z</time></metadata>
  <trk><name>Test import ride</name><trkseg>
    <trkpt lat="33.500000" lon="-112.100000"><ele>300</ele><time>2026-11-20T10:00:00Z</time><speed>10</speed></trkpt>
    <trkpt lat="33.501000" lon="-112.100000"><ele>302</ele><time>2026-11-20T10:00:30Z</time><speed>12</speed></trkpt>
    <trkpt lat="33.502000" lon="-112.100000"><ele>305</ele><time>2026-11-20T10:01:00Z</time><speed>11</speed></trkpt>
    <trkpt lat="33.503000" lon="-112.100000"><ele>304</ele><time>2026-11-20T10:01:30Z</time><speed>13</speed></trkpt>
    <trkpt lat="33.504000" lon="-112.100000"><ele>308</ele><time>2026-11-20T10:02:00Z</time><speed>10</speed></trkpt>
  </trkseg></trk>
</gpx>
"""

results = []


# ---- Navigation test helpers (canned routes, scripted-path fake GPS) ----
NAV_SHOT = os.path.join(WS, "gps-speedometer-nav-v1.png")
NAV_TURN_GPX = "/tmp/speedo-nav-turn-test.gpx"

FAKE_GEO_NAV = """
Object.defineProperty(navigator, 'geolocation', { value: {
  _watchers: {}, _nextId: 1, _timer: null,
  _wps: [[33.5658,-112.1646],[33.5712,-112.1646],[33.5712,-112.1596]],
  _lat: 33.5658, _lon: -112.1646, _wp: 1,
  _step: function(){
    var self = this;
    var t = self._wps[self._wp];
    var dLat = t[0]-self._lat, dLon = t[1]-self._lon;
    var dist = Math.sqrt(Math.pow(dLat*110540,2) + Math.pow(dLon*111320*Math.cos(self._lat*Math.PI/180),2));
    var stepM = 15*0.7;
    if (dist <= stepM){ self._lat = t[0]; self._lon = t[1]; if (self._wp < self._wps.length-1) self._wp++; }
    else { var k = stepM/dist; self._lat += dLat*k; self._lon += dLon*k; }
    var t2 = self._wps[self._wp];
    var hdg = (Math.atan2(t2[1]-self._lon, t2[0]-self._lat)*180/Math.PI + 360) % 360;
    var pos = { coords: { latitude: self._lat, longitude: self._lon, altitude: 300,
      accuracy: 5, altitudeAccuracy: 8, heading: hdg, speed: 15 }, timestamp: Date.now() };
    Object.keys(self._watchers).forEach(function(k){ self._watchers[k](pos); });
  },
  _start: function(){ var self=this; if (self._timer) return; self._timer = setInterval(function(){ self._step(); }, 700); },
  watchPosition: function(success, error, options){ var id = this._nextId++; this._watchers[id]=success; this._start(); return id; },
  clearWatch: function(id){ delete this._watchers[id]; },
  getCurrentPosition: function(success){ success({ coords:{latitude:this._lat,longitude:this._lon,altitude:300,accuracy:5,altitudeAccuracy:8,heading:0,speed:0}, timestamp: Date.now() }); }
}});
window.__fakeGeoJump = function(lat, lon){
  var g = navigator.geolocation;
  g._lat = lat; g._lon = lon;
  var best = 0, bestD = 1e18;
  g._wps.forEach(function(w, i){ var d = Math.pow((w[0]-lat)*110540,2)+Math.pow((w[1]-lon)*111320,2); if (d < bestD){ bestD = d; best = i; } });
  g._wp = Math.min(best+1, g._wps.length-1);
  if (g._wp === 0) g._wp = 1;
};
"""


FAKE_MOTION = """
window.__fakeMotion = function(gx, gy, gz){
  var e = new Event('devicemotion');
  e.accelerationIncludingGravity = { x: gx, y: gy, z: gz };
  window.dispatchEvent(e);
};
window.__fakeOrient = function(beta, gamma){
  var e = new Event('deviceorientation');
  e.beta = beta; e.gamma = gamma; e.alpha = 0;
  window.dispatchEvent(e);
};
"""

FAKE_MOTION_DENIED = FAKE_MOTION + """
try {
  Object.defineProperty(window, 'DeviceMotionEvent', { value: { requestPermission: function(){ return Promise.resolve('denied'); } }, configurable: true });
} catch (e) {}
"""

FAKE_NO_SENSOR = """
try { delete window.DeviceMotionEvent; } catch (e) {}
try { delete window.DeviceOrientationEvent; } catch (e) {}
"""


def encode_polyline(points, precision=6):
    def enc(n):
        n = (n << 1) if n >= 0 else (~(n << 1))
        s = ""
        while n >= 0x20:
            s += chr((0x20 | (n & 0x1f)) + 63)
            n >>= 5
        s += chr(n + 63)
        return s
    out, plat, plon = "", 0, 0
    for lat, lon in points:
        ilat = int(round(lat * 10 ** precision))
        ilon = int(round(lon * 10 ** precision))
        out += enc(ilat - plat) + enc(ilon - plon)
        plat, plon = ilat, ilon
    return out


def hav_m(a, b):
    R = 6371000
    dlat = math.radians(b[0] - a[0])
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def scripted_points():
    """North 600 m, then east 470 m — the path FAKE_GEO_NAV walks."""
    pts = []
    lat, lon = 33.5658, -112.1646
    for i in range(61):
        pts.append((lat + i * 10.0 / 110540, lon))
    clat = pts[-1][0]
    step_lon = 10.0 / (111320 * math.cos(math.radians(clat)))
    for i in range(1, 47):
        pts.append((clat, lon + i * step_lon))
    return pts


def _leg_mi(points, i0, i1):
    return sum(hav_m(points[i], points[i + 1]) for i in range(i0, i1)) / 1609.344


def valhalla_route(points, corner_idx=60):
    total_mi = sum(hav_m(points[i], points[i + 1]) for i in range(len(points) - 1)) / 1609.344
    return {
        "trip": {
            "units": "miles",
            "summary": {"length": round(total_mi, 4), "time": 200},
            "legs": [{
                "shape": encode_polyline(points),
                "maneuvers": [
                    {"type": 1, "instruction": "Drive north on North Test Road.",
                     "street_names": ["North Test Road"], "length": round(_leg_mi(points, 0, corner_idx), 4),
                     "begin_shape_index": 0, "end_shape_index": 2},
                    {"type": 10, "instruction": "Turn right onto East Test Road.",
                     "street_names": ["East Test Road"], "length": round(_leg_mi(points, corner_idx, len(points) - 1), 4),
                     "begin_shape_index": corner_idx, "end_shape_index": corner_idx},
                    {"type": 4, "instruction": "Your destination is on the right.",
                     "street_names": [], "length": 0,
                     "begin_shape_index": len(points) - 1, "end_shape_index": len(points) - 1},
                ],
            }],
        }
    }


def valhalla_straight_route(start, dest):
    pts = []
    for i in range(12):
        k = i / 11
        pts.append((start[0] + (dest[0] - start[0]) * k, start[1] + (dest[1] - start[1]) * k))
    total_mi = sum(hav_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) / 1609.344
    return {"trip": {"units": "miles", "summary": {"length": round(total_mi, 4), "time": 120},
                     "legs": [{"shape": encode_polyline(pts), "maneuvers": [
                         {"type": 1, "instruction": "Drive.",
                          "street_names": ["Test Road"], "length": round(total_mi, 4),
                          "begin_shape_index": 0, "end_shape_index": 1},
                         {"type": 4, "instruction": "Your destination is on the right.",
                          "street_names": [], "length": 0,
                          "begin_shape_index": len(pts) - 1, "end_shape_index": len(pts) - 1},
                     ]}]}}


def osrm_route(points, corner_idx=60):
    """Step geometries partition the route the way the real OSRM API does:
    consecutive steps share their boundary coordinate, so the concatenated
    geometry is the full route with no gaps or repeats."""
    coords = [[lon, lat] for lat, lon in points]
    total = sum(hav_m(points[i], points[i + 1]) for i in range(len(points) - 1))
    return {"code": "Ok", "routes": [{"distance": total, "duration": 200, "legs": [{"steps": [
        {"maneuver": {"type": "depart", "modifier": "right", "bearing_after": 0},
         "name": "North Test Road", "distance": _leg_mi(points, 0, corner_idx) * 1609.344,
         "duration": 100, "geometry": {"type": "LineString", "coordinates": coords[:corner_idx + 1]}},
        {"maneuver": {"type": "turn", "modifier": "right", "bearing_after": 90},
         "name": "East Test Road", "distance": _leg_mi(points, corner_idx, len(points) - 1) * 1609.344,
         "duration": 100, "geometry": {"type": "LineString", "coordinates": coords[corner_idx:]}},
        {"maneuver": {"type": "arrive"}, "name": "", "distance": 0, "duration": 0,
         "geometry": {"type": "LineString", "coordinates": coords[-1:]}},
    ]}]}]}


def turn_gpx_text():
    """A GPX track with a real turn: north ~550 m, then east ~410 m."""
    pts = []
    for i in range(11):
        pts.append((33.5658 + i * 0.0005, -112.1646))
    for j in range(1, 9):
        pts.append((33.5708, -112.1646 + j * 0.00055))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">',
             '  <metadata><name>Test turn track</name><time>2026-11-20T10:00:00Z</time></metadata>',
             '  <trk><name>Test turn track</name><trkseg>']
    for i, (lat, lon) in enumerate(pts):
        lines.append('    <trkpt lat="%.6f" lon="%.6f"><ele>300</ele><time>2026-11-20T10:%02d:00Z</time><speed>10</speed></trkpt>'
                     % (lat, lon, i))
    lines += ['  </trkseg></trk>', '</gpx>']
    return "\n".join(lines)


def mock_geocode(route):
    # The planner test searches "Stop Town" for its waypoint and
    # "Test Place" for its destination — answer each with its own name
    # so the plan summary can tell the legs apart.
    q = (route.request.url or "").lower()
    if "stop" in q:
        name = "Stop Town, AZ"
    else:
        name = "Test Place, Glendale, AZ"
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps([{"display_name": name,
                                    "lat": "33.5712", "lon": "-112.1596"}]))


def valhalla_multi_route():
    """Two-leg canned route for the planner: north ~2.5 mi, then east
    ~2.0 mi. Leg 1 ends at the waypoint; leg 2 ends at the destination."""
    leg1 = []
    lat, lon = 33.5658, -112.1646
    for i in range(41):
        leg1.append((lat + i * 100.0 / 110540, lon))
    clat = leg1[-1][0]
    step_lon = 100.0 / (111320 * math.cos(math.radians(clat)))
    leg2 = []
    for i in range(33):
        leg2.append((clat, lon + i * step_lon))

    def leg_dict(pts, street):
        total_mi = sum(hav_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) / 1609.344
        return {"shape": encode_polyline(pts),
                "maneuvers": [
                    {"type": 1, "instruction": "Drive.",
                     "street_names": [street], "length": round(total_mi, 4),
                     "begin_shape_index": 0, "end_shape_index": 1},
                    {"type": 4, "instruction": "Your destination is on the right.",
                     "street_names": [], "length": 0,
                     "begin_shape_index": len(pts) - 1, "end_shape_index": len(pts) - 1},
                ],
                "summary": {"length": round(total_mi, 4), "time": 300}}

    total_mi = (sum(hav_m(leg1[i], leg1[i + 1]) for i in range(len(leg1) - 1)) +
                sum(hav_m(leg2[i], leg2[i + 1]) for i in range(len(leg2) - 1))) / 1609.344
    return {"trip": {"units": "miles",
                     "summary": {"length": round(total_mi, 4), "time": 600},
                     "legs": [leg_dict(leg1, "North Test Road"),
                              leg_dict(leg2, "East Test Road")]}}


def make_valhalla_multi_mock():
    calls = {"n": 0}

    def handler(route):
        calls["n"] += 1
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(valhalla_multi_route()))

    handler.calls = calls
    return handler


def make_valhalla_mock():
    """First call: the scripted route. Later calls: a straight route from
    the requested start to the fixed destination (re-route)."""
    calls = {"n": 0}
    pts = scripted_points()

    def handler(route):
        calls["n"] += 1
        if calls["n"] == 1:
            body = valhalla_route(pts)
        else:
            start = (33.5712, -112.1680)
            try:
                data = json.loads(route.request.post_data or "{}")
                locs = data.get("locations") or []
                if locs:
                    start = (locs[0]["lat"], locs[0]["lon"])
            except Exception:
                pass
            body = valhalla_straight_route(start, (33.5712, -112.1596))
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    handler.calls = calls
    return handler


def mock_osrm(route):
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps(osrm_route(scripted_points())))


def turn_dist_m(page):
    t = page.locator("#navTurnDist").inner_text().strip()
    if t.endswith("ft"):
        return float(t.replace("ft", "").replace(",", "").strip()) / 3.28084
    return float(t.replace("mi", "").strip()) * 1609.344


def route_to_test_place(page):
    """Search 'Test Place', confirm it, start GPS, route. Assumes the
    geocode + route hosts are already mocked for this page."""
    page.fill("#destInput", "Test Place")
    page.click("#destSearchBtn")
    page.wait_for_timeout(800)
    check("place search shows a result", page.locator("#destResults button").count() == 1)
    page.locator("#destResults button").first.click()
    page.wait_for_timeout(300)
    check("confirm panel shows the place",
          page.locator("#destConfirm").is_visible()
          and "Test Place" in page.locator("#destConfirmName").inner_text())
    page.click("#mainBtn")  # Start Ride so GPS fixes flow
    page.wait_for_timeout(3000)
    page.click("#destRouteBtn")
    page.wait_for_timeout(1200)
    check("arrow screen opens on route", page.locator("#navView").is_visible())


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ((" — " + str(detail)) if detail else ""))
    return cond


def serve(directory):
    """Serve a directory on an ephemeral localhost port; returns (httpd, port)."""
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=directory, **k)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def build_font_copy():
    """Temp copy of staging with Google Fonts downloaded locally, so the
    screenshot renders in the real typefaces. Never touches staging."""
    tmp = "/tmp/speedo-shot"
    subprocess.run(["rm", "-rf", tmp], check=False)
    subprocess.run(["cp", "-r", HERE, tmp], check=True)
    try:
        req = urllib.request.Request(
            "https://fonts.googleapis.com/css2?family=Archivo+Narrow:wght@400;600;700&family=UnifrakturCook:wght@700&display=swap",
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"})
        css = urllib.request.urlopen(req, timeout=30).read().decode()
        urls = sorted(set(re.findall(r"url\((https://[^)]+\.woff2)\)", css)))
        rules = []
        for i, u in enumerate(urls):
            path = os.path.join(tmp, "font-%d.woff2" % i)
            urllib.request.urlretrieve(u, path)
            # keep the family/weight from the matching @font-face block
            m = re.search(r"/\*\s*([a-z-]+)\s*\*/\s*@font-face\s*{[^}]*font-family:\s*'([^']+)';[^}]*font-weight:\s*(\d+);[^}]*url\(%s\)" % re.escape(u), css)
            fam = m.group(2) if m else "Archivo Narrow"
            wt = m.group(3) if m else "400"
            rules.append("@font-face{font-family:'%s';font-style:normal;font-weight:%s;font-display:swap;src:url(font-%d.woff2) format('woff2');}" % (fam, wt, i))
        open(os.path.join(tmp, "fonts.css"), "w").write("\n".join(rules))
        html = open(os.path.join(tmp, "index.html")).read()
        html = html.replace('</head>', '<link rel="stylesheet" href="fonts.css">\n</head>', 1)
        open(os.path.join(tmp, "index.html"), "w").write(html)
        print("font copy: %d woff2 inlined" % len(urls))
    except Exception as e:
        print("font copy failed (%s) — screenshot will use fallback fonts" % e)
    return tmp


def main():
    from playwright.sync_api import sync_playwright

    with open(IMPORT_GPX, "w") as f:
        f.write(IMPORT_GPX_TEXT)

    _, port_stage = serve(HERE)
    shot_dir = build_font_copy()
    _, port_shot = serve(shot_dir)

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---- Screenshot run (temp font copy): record a real ride, shoot history
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  geolocation={"latitude": 33.5658, "longitude": -112.1646},
                                  permissions=["geolocation"])
        ctx.add_init_script(FAKE_GEO)
        ctx.add_init_script(FAKE_MOTION)
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_shot)
        page.wait_for_timeout(1200)
        check("loads with speed 0", page.locator(".js-speed").first.inner_text() == "0")
        # Lean: calibrate upright, then lean 30deg right mid-ride so the
        # max lean lands in the saved ride's history row.
        page.click("#stage")
        page.wait_for_timeout(500)
        page.click("#leanCalBtn")
        page.wait_for_timeout(300)
        page.evaluate("window.__fakeMotion(0, -9.8, 0)")
        page.wait_for_timeout(400)
        check("lean calibrates in Settings", "Live" in page.locator("#leanSensorState").inner_text())
        page.click("#panelCloseBtn")
        page.wait_for_timeout(300)
        page.click("#mainBtn")
        page.wait_for_timeout(5000)
        for _ in range(3):
            page.evaluate("window.__fakeMotion(4.9, -8.487, 0)")
            page.wait_for_timeout(200)
        page.wait_for_timeout(20400)
        check("recording card appears while riding",
              page.locator(".ride-item.recording").count() == 1)
        page.click("#mainBtn")  # Stop
        page.wait_for_timeout(800)
        saved = page.locator("#rideList .ride-item:not(.recording)")
        check("ride saved to history after Stop", saved.count() == 1)
        hist_text = saved.first.inner_text()
        print("  history row: " + hist_text.replace("\n", " | "))
        check("saved ride shows distance", re.search(r"Distance\s+[\d.]+", hist_text) is not None)
        check("saved ride keeps its max lean", "Lean" in hist_text and "30°" in hist_text, hist_text.replace("\n", " | "))
        page.locator(".rides").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.screenshot(path=SHOT)
        print("  screenshot: " + SHOT)
        ctx.close()

        # ---- Navigation screenshot (temp font copy): arrow screen mid-route
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  geolocation={"latitude": 33.5658, "longitude": -112.1646},
                                  permissions=["geolocation"])
        ctx.add_init_script(FAKE_GEO_NAV)
        ctx.route("**nominatim.openstreetmap.org/**", mock_geocode)
        ctx.route("**valhalla1.openstreetmap.de/**", make_valhalla_mock())
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_shot)
        page.wait_for_timeout(1200)
        route_to_test_place(page)
        page.evaluate("window.__fakeGeoJump(33.5685, -112.1646)")
        page.wait_for_timeout(3000)
        page.screenshot(path=NAV_SHOT)
        print("  nav screenshot: " + NAV_SHOT)
        ctx.close()

        # ---- Functional run (real staging): full behaviour
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  geolocation={"latitude": 33.5658, "longitude": -112.1646},
                                  permissions=["geolocation"])
        ctx.add_init_script(FAKE_GEO)
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        check("version line shows v1.4.0", "v1.4.0" in page.locator("#versionLine").inner_text())
        check("history starts empty", page.locator("#ridesEmpty").is_visible())

        page.click("#mainBtn")
        page.wait_for_timeout(26000)
        fixes = int(page.locator("#fixValue").inner_text())
        trip = page.locator("#tripValue").inner_text()
        check("GPS fixes arrive", fixes > 10, "fixes=%d" % fixes)
        check("trip distance grows", float(trip) > 0.1, "trip=%s mi" % trip)
        check("max speed recorded", int(page.locator("#maxValue").inner_text()) > 20)
        page.click("#mainBtn")  # Stop
        page.wait_for_timeout(800)
        saved = page.locator("#rideList .ride-item:not(.recording)")
        check("history lists the saved ride", saved.count() == 1)

        # GPX export — valid file with the recorded track points
        with page.expect_download() as dl_info:
            saved.first.locator(".ride-actions .ghost-btn", has_text="↓ GPX").first.click()
        dl = dl_info.value
        gpx_path = "/tmp/speedo-export-test.gpx"
        dl.save_as(gpx_path)
        gpx = open(gpx_path).read()
        pts = len(re.findall(r"<trkpt ", gpx))
        check("GPX export is valid GPX 1.1", gpx.startswith("<?xml") and '<gpx version="1.1"' in gpx, "trkpts=%d" % pts)
        check("GPX has the recorded track points", pts >= 4, "trkpts=%d" % pts)
        check("GPX carries times and speeds", "<time>" in gpx and "<speed>" in gpx)
        check("GPX filename looks right", dl.suggested_filename.startswith("ride-") and dl.suggested_filename.endswith(".gpx"), dl.suggested_filename)

        # Persistence across reload
        page.reload()
        page.wait_for_timeout(1000)
        check("saved ride survives reload", page.locator("#rideList .ride-item:not(.recording)").count() == 1)

        # GPX import
        page.set_input_files("#importGpxFile", IMPORT_GPX)
        page.wait_for_timeout(800)
        items = page.locator("#rideList .ride-item:not(.recording)")
        check("GPX import lands in history", items.count() == 2)
        print("  first row after import: " + items.first.inner_text().replace("\n", " | "))
        check("imported ride is labelled", "IMPORTED" in items.first.inner_text())  # badge is uppercase via CSS text-transform

        # Delete both rides
        items.first.locator(".ride-actions .ghost-btn", has_text="Delete").click()
        page.wait_for_timeout(500)
        check("delete removes one ride", page.locator("#rideList .ride-item:not(.recording)").count() == 1)
        page.locator("#rideList .ride-item:not(.recording)").first.locator(".ride-actions .ghost-btn", has_text="Delete").click()
        page.wait_for_timeout(500)
        check("history returns to empty", page.locator("#rideList .ride-item:not(.recording)").count() == 0
              and page.locator("#ridesEmpty").is_visible())

        # Existing behaviour intact: Simple view, HUD mirror, brightness
        page.click("#simpleBtn")
        page.wait_for_timeout(500)
        check("Simple view opens", page.locator("#simpleView").is_visible())
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        check("Simple view closes on Escape", not page.locator("#simpleView").is_visible())
        page.click("#stage")  # tap = settings panel
        page.wait_for_timeout(500)
        check("settings panel opens on tap", page.locator("#panelScrim").is_visible())
        page.click("#swMirror")
        check("HUD mirror toggle flips", page.locator("#swMirror").get_attribute("aria-checked") == "true")
        page.fill("#rngBright", "50")
        check("brightness slider applies", page.locator("#valBright").inner_text() == "50%")
        page.click("#panelCloseBtn")
        ctx.close()

        # ---- Navigation tests (mocked Valhalla / Nominatim / OSRM) ----
        valhalla_mock = make_valhalla_mock()
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  geolocation={"latitude": 33.5658, "longitude": -112.1646},
                                  permissions=["geolocation"])
        ctx.add_init_script(FAKE_GEO_NAV)
        ctx.route("**nominatim.openstreetmap.org/**", mock_geocode)
        ctx.route("**valhalla1.openstreetmap.de/**", valhalla_mock)
        ctx.route("**router.project-osrm.org/**", mock_osrm)
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        route_to_test_place(page)
        check("first arrow is the right turn",
              page.locator("#navArrowRight").evaluate("el => el.style.display") != "none")
        # The label is uppercased by CSS, so inner_text() comes back
        # "TURN RIGHT IN" — compare case-insensitively.
        check("turn label names the turn",
              "turn right in" in page.locator("#navTurnLabel").inner_text().lower())
        check("street shown", "East Test Road" in page.locator("#navStreet").inner_text())
        check("next step shown", "arrive" in page.locator("#navNext").inner_text().lower())
        d0 = turn_dist_m(page)
        # The distance readout is 0.1 mi steps above 400 m, so the rider
        # must cover more than one 161 m band to guarantee a visible flip:
        # 14 s at 15 m/s is ~210 m of progress.
        page.wait_for_timeout(14000)
        d1 = turn_dist_m(page)
        check("distance to the turn counts down", d1 < d0 - 50, "%.0f m -> %.0f m" % (d0, d1))
        check("compass fallback note shows with no compass",
              page.locator("#navCompassNote").is_visible())
        # Jump past the corner: the arrow should change to the destination
        page.evaluate("window.__fakeGeoJump(33.5712, -112.164278)")
        page.wait_for_timeout(3000)
        check("arrow changes to arrive after the turn",
              page.locator("#navArrowArrive").evaluate("el => el.style.display") != "none"
              and "destination in" in page.locator("#navTurnLabel").inner_text().lower())
        # Off-route: jump well west of the line, then re-route
        page.evaluate("window.__fakeGeoJump(33.5712, -112.1680)")
        page.wait_for_timeout(3000)
        check("off-route state appears", page.locator("#navOffRoute").is_visible())
        page.click("#navRerouteBtn")
        page.wait_for_timeout(1500)
        check("re-route clears off-route", not page.locator("#navOffRoute").is_visible())
        check("re-route fetched a new route", valhalla_mock.calls["n"] >= 2,
              "valhalla calls=%d" % valhalla_mock.calls["n"])
        # Cached route survives a reload with the servers unreachable
        page.click("#navEndBtn")
        page.wait_for_timeout(400)
        ctx.route("**valhalla1.openstreetmap.de/**", lambda route: route.abort())
        ctx.route("**nominatim.openstreetmap.org/**", lambda route: route.abort())
        page.reload()
        page.wait_for_timeout(1200)
        check("cached route offers Resume after reload", page.locator("#navResumeBtn").is_visible())
        hits_before = valhalla_mock.calls["n"]
        page.click("#navResumeBtn")
        page.wait_for_timeout(2500)
        check("resume opens the arrow screen offline", page.locator("#navView").is_visible())
        check("resume made no new route request", valhalla_mock.calls["n"] == hits_before)
        # The resumed route is the re-routed straight line east at
        # lat 33.5712; the walker restarts at the original start and walks
        # north, which does not advance along that line — so drop the
        # rider onto the route first (~170 m short of the destination),
        # then the eastward walk counts the distance down.
        page.evaluate("window.__fakeGeoJump(33.5712, -112.1615)")
        page.wait_for_timeout(1500)
        d2 = turn_dist_m(page)
        page.wait_for_timeout(6000)
        d3 = turn_dist_m(page)
        check("cached route still guides offline", d3 < d2 - 50, "%.0f m -> %.0f m" % (d2, d3))
        page.click("#navEndBtn")
        page.wait_for_timeout(300)
        # GPX follow: an imported track with a real turn, same arrow screen
        with open(NAV_TURN_GPX, "w") as f:
            f.write(turn_gpx_text())
        page.set_input_files("#importGpxFile", NAV_TURN_GPX)
        page.wait_for_timeout(800)
        items = page.locator("#rideList .ride-item:not(.recording)")
        check("turn track imported", items.count() >= 1)
        follow_btns = items.first.locator(".ride-actions .ghost-btn", has_text="Follow")
        check("imported ride has a Follow button", follow_btns.count() == 1)
        # Put the rider on the track before following it: the resume test
        # above left the walker parked at the destination, past the end of
        # this track, so without a jump the follow would start beyond the
        # track's last point (arrow already on Arrive, distance 0).
        # (33.56806, -112.1646) is ~250 m up the track's north leg, so the
        # turn ahead is ~300 m out — inside the feet readout range, where
        # the countdown is visible at 1 ft granularity.
        page.evaluate("window.__fakeGeoJump(33.56806, -112.1646)")
        page.wait_for_timeout(500)
        follow_btns.first.click()
        page.wait_for_timeout(1500)
        check("GPX follow opens the arrow screen", page.locator("#navView").is_visible())
        check("GPX follow shows the track turn",
              page.locator("#navArrowRight").evaluate("el => el.style.display") != "none")
        d4 = turn_dist_m(page)
        page.wait_for_timeout(6000)
        d5 = turn_dist_m(page)
        check("GPX follow distance counts down", d5 < d4, "%.0f m -> %.0f m" % (d4, d5))
        page.click("#navEndBtn")
        ctx.close()

        # ---- OSRM fallback: Valhalla 500s, route still loads via OSRM ----
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  geolocation={"latitude": 33.5658, "longitude": -112.1646},
                                  permissions=["geolocation"])
        ctx.add_init_script(FAKE_GEO_NAV)
        ctx.route("**nominatim.openstreetmap.org/**", mock_geocode)
        ctx.route("**valhalla1.openstreetmap.de/**",
                  lambda route: route.fulfill(status=500, body="down"))
        ctx.route("**router.project-osrm.org/**", mock_osrm)
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        route_to_test_place(page)
        check("OSRM fallback opens the arrow screen", page.locator("#navView").is_visible())
        check("status names OSRM as the source", "OSRM" in page.locator("#navStatus").inner_text())
        check("OSRM route shows the turn street",
              "East Test Road" in page.locator("#navStreet").inner_text())
        page.click("#navEndBtn")
        ctx.close()

        # ---- Endpoint settings: a custom Valhalla URL is used, Reset restores defaults ----
        custom_hit = {"n": 0}

        def custom_valhalla(route):
            custom_hit["n"] += 1
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(valhalla_route(scripted_points())))

        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  geolocation={"latitude": 33.5658, "longitude": -112.1646},
                                  permissions=["geolocation"])
        ctx.add_init_script(FAKE_GEO_NAV)
        ctx.route("**nominatim.openstreetmap.org/**", mock_geocode)
        ctx.route("**valhalla.custom.test/**", custom_valhalla)
        ctx.route("**valhalla1.openstreetmap.de/**", make_valhalla_mock())
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        page.click("#stage")  # tap = settings panel
        page.wait_for_timeout(500)
        check("settings panel opens", page.locator("#panelScrim").is_visible())
        page.fill("#cfgValhalla", "https://valhalla.custom.test/route")
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
        page.click("#panelCloseBtn")
        page.wait_for_timeout(300)
        route_to_test_place(page)
        check("custom Valhalla endpoint was used", custom_hit["n"] == 1, "hits=%d" % custom_hit["n"])
        page.click("#navEndBtn")
        page.wait_for_timeout(300)
        page.click("#stage")
        page.wait_for_timeout(500)
        page.click("#cfgEndpointsReset")
        page.wait_for_timeout(300)
        check("Reset restores the public Valhalla URL",
              page.locator("#cfgValhalla").input_value() == "https://valhalla1.openstreetmap.de/route")
        page.click("#panelCloseBtn")
        ctx.close()

        # ---- Route planning: waypoints, distance, fuel stops ----
        plan_valhalla = make_valhalla_multi_mock()
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  geolocation={"latitude": 33.5658, "longitude": -112.1646},
                                  permissions=["geolocation"])
        ctx.add_init_script(FAKE_GEO_NAV)
        ctx.route("**nominatim.openstreetmap.org/**", mock_geocode)
        ctx.route("**valhalla1.openstreetmap.de/**", plan_valhalla)
        ctx.route("**router.project-osrm.org/**", mock_osrm)
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        page.click("#mainBtn")  # Start Ride so GPS fixes flow (plan needs a fix)
        page.wait_for_timeout(3000)
        # Add a waypoint stop, then the destination
        page.fill("#planStopInput", "Stop Town")
        page.click("#planStopSearchBtn")
        page.wait_for_timeout(800)
        check("plan stop search shows a result", page.locator("#planStopResults button").count() == 1)
        page.locator("#planStopResults button").first.click()
        page.wait_for_timeout(300)
        check("plan stop lands in the stop list",
              "Stop Town" in page.locator("#planStopList").inner_text())
        page.fill("#planDestInput", "Test Place")
        page.click("#planDestSearchBtn")
        page.wait_for_timeout(800)
        page.locator("#planDestResults button").first.click()
        page.wait_for_timeout(300)
        check("plan destination line shows the place",
              page.locator("#planDestLine").is_visible()
              and "Test Place" in page.locator("#planDestLine").inner_text())
        # 2 mi per tank, stop 0.5 mi early: on a ~4.5 mi route the stops
        # fall at 1.5 mi and 3.0 mi — one on each leg.
        page.fill("#planRangeMi", "2")
        page.fill("#planMarginMi", "0.5")
        page.click("#planRouteBtn")
        page.wait_for_timeout(1500)
        check("plan summary appears", page.locator("#planSummary").is_visible())
        total_text = page.locator("#planTotal").inner_text()
        print("  plan total: " + total_text.replace("\n", " | "))
        check("plan total is about 4.5 mi", "4.4" in total_text or "4.5" in total_text, total_text)
        legs = page.locator("#planLegs li")
        check("plan shows both legs", legs.count() == 2, "legs=%d" % legs.count())
        check("first leg names the stop", "Stop Town" in legs.nth(0).inner_text())
        check("second leg ends at the destination", "Test Place" in legs.nth(1).inner_text())
        fuel = page.locator("#planFuelList li")
        check("plan shows two fuel stops", fuel.count() == 2, "fuel=%d" % fuel.count())
        fuel_text = page.locator("#planFuelList").inner_text()
        print("  fuel stops: " + fuel_text.replace("\n", " | "))
        check("first fuel stop is near the north leg street",
              "Fuel stop 1" in fuel_text and "North Test Road" in fuel_text, fuel_text)
        check("second fuel stop is near the waypoint",
              "Fuel stop 2" in fuel_text and "Stop Town" in fuel_text, fuel_text)
        # The plan drives the existing guidance: Navigate this plan opens
        # the arrow screen on the merged route.
        check("Navigate this plan is offered", page.locator("#planNavBtn").is_visible())
        page.click("#planNavBtn")
        page.wait_for_timeout(1200)
        check("planned route opens the arrow screen", page.locator("#navView").is_visible())
        page.click("#navEndBtn")
        page.wait_for_timeout(300)
        # The plan survives a reload from the on-device cache, with no
        # new route request; Clear plan wipes it.
        hits_before = plan_valhalla.calls["n"]
        page.reload()
        page.wait_for_timeout(1200)
        check("plan restores from cache after reload", page.locator("#planSummary").is_visible())
        check("restore made no new route request", plan_valhalla.calls["n"] == hits_before)
        check("plan stop list restores too", "Stop Town" in page.locator("#planStopList").inner_text())
        page.click("#planClearBtn")
        page.wait_for_timeout(400)
        check("Clear plan wipes the summary", not page.locator("#planSummary").is_visible())
        check("Clear plan wipes the stop list", page.locator("#planStopList li").count() == 0)
        ctx.close()

        # ---- Lean angle (fake motion + orientation events) ----
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        ctx.add_init_script(FAKE_MOTION)
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        check("lean tile starts by checking the sensor",
              "Checking sensor" in page.locator("#leanSub").inner_text())
        page.click("#stage")
        page.wait_for_timeout(500)
        check("lean sensor state shows in Settings",
              "Checking" in page.locator("#leanSensorState").inner_text())
        page.click("#leanCalBtn")
        page.wait_for_timeout(300)
        check("calibrate waits for the first sensor event",
              "Waiting for sensor" in page.locator("#leanSensorState").inner_text())
        page.evaluate("window.__fakeMotion(0, -9.8, 0)")
        page.wait_for_timeout(400)
        check("calibration captures the upright reference",
              "Live" in page.locator("#leanSensorState").inner_text())
        page.click("#panelCloseBtn")
        page.wait_for_timeout(300)
        check("upright reads 0 lean", page.locator("#leanValue").inner_text() == "0°")
        page.evaluate("window.__fakeMotion(4.9, -8.487, 0)")
        page.wait_for_timeout(400)
        for _ in range(5):
            page.evaluate("window.__fakeMotion(4.9, -8.487, 0)")
            page.wait_for_timeout(150)
        lean_txt = page.locator("#leanValue").inner_text()
        m = re.match(r"(\d+)° R", lean_txt)
        check("30deg right lean reads ~30 R once smoothing catches up",
              m is not None and 25 <= int(m.group(1)) <= 35, lean_txt)
        for _ in range(6):
            page.evaluate("window.__fakeMotion(-4.9, -8.487, 0)")
            page.wait_for_timeout(150)
        lean_txt = page.locator("#leanValue").inner_text()
        m = re.match(r"(\d+)° L", lean_txt)
        check("left lean reads L once smoothing catches up",
              m is not None and 20 <= int(m.group(1)) <= 35, lean_txt)
        # Calibration survives a reload — no re-calibrate prompt
        page.reload()
        page.wait_for_timeout(1200)
        check("calibration survives reload",
              "Calibrate" not in page.locator("#leanSub").inner_text())
        page.evaluate("window.__fakeMotion(0, -9.8, 0)")
        page.wait_for_timeout(400)
        check("upright still reads 0 after reload",
              page.locator("#leanValue").inner_text() == "0°")
        # Clearing the calibration returns to uncalibrated
        page.click("#stage")
        page.wait_for_timeout(500)
        page.click("#leanResetBtn")
        page.wait_for_timeout(300)
        check("clearing calibration returns to uncalibrated",
              "Not calibrated" in page.locator("#leanSensorState").inner_text())
        page.click("#panelCloseBtn")
        ctx.close()

        # Orientation fallback: calibrate at beta=60, then the (beta,
        # gamma) pair a real 30deg lean produces from there (48.59, 40.89
        # — from the rotation-matrix round-trip in test.js).
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        ctx.add_init_script(FAKE_MOTION)
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        page.click("#stage")
        page.wait_for_timeout(500)
        page.click("#leanCalBtn")
        page.wait_for_timeout(300)
        page.evaluate("window.__fakeOrient(60, 0)")
        page.wait_for_timeout(400)
        check("orientation event calibrates the reference",
              "Live" in page.locator("#leanSensorState").inner_text())
        page.click("#panelCloseBtn")
        page.wait_for_timeout(300)
        for _ in range(5):
            page.evaluate("window.__fakeOrient(48.59, 40.89)")
            page.wait_for_timeout(150)
        lean_txt = page.locator("#leanValue").inner_text()
        m = re.match(r"(\d+)° R", lean_txt)
        check("orientation fallback reads the ~30deg lean",
              m is not None and 25 <= int(m.group(1)) <= 35, lean_txt)
        ctx.close()

        # Permission denied: the tile says so, it never fakes a reading
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        ctx.add_init_script(FAKE_MOTION_DENIED)
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(1200)
        page.click("#stage")
        page.wait_for_timeout(500)
        page.click("#leanCalBtn")
        page.wait_for_timeout(2200)
        check("denied permission shows as denied, never a fake reading",
              "Motion permission denied" in page.locator("#leanSensorState").inner_text()
              and "Motion permission denied" in page.locator("#leanSub").inner_text())
        ctx.close()

        # No sensor APIs at all: plain "not available on this device"
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        ctx.add_init_script(FAKE_NO_SENSOR)
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_stage)
        page.wait_for_timeout(7500)
        check("no sensor shows not-available, never a fake reading",
              "Not available on this device" in page.locator("#leanSub").inner_text())
        ctx.close()

        browser.close()

    failed = [r for r in results if not r[1]]
    print("\n%d passed, %d failed." % (len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

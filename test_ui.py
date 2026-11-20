#!/usr/bin/env python3
"""Playwright UI tests for GPS Speedometer (ride recording + GPX).

The VM has no real GPS, so the tests install a fake navigator.geolocation
before page scripts run: it walks a straight path north at 15 m/s, one fix
every 700 ms, with speed / heading / altitude / accuracy on every fix.

Run from this directory:
    /tmp/pw-render/bin/python test_ui.py

Also writes ~/workspace/gps-speedometer-rides-v1.png — a 390 px phone
screenshot of the ride history with a recorded ride. Read it back before
calling the feature done.
"""
import http.server
import json
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
        page = ctx.new_page()
        page.goto("http://127.0.0.1:%d/index.html" % port_shot)
        page.wait_for_timeout(1200)
        check("loads with speed 0", page.locator(".js-speed").first.inner_text() == "0")
        page.click("#mainBtn")
        page.wait_for_timeout(26000)
        check("recording card appears while riding",
              page.locator(".ride-item.recording").count() == 1)
        page.click("#mainBtn")  # Stop
        page.wait_for_timeout(800)
        saved = page.locator("#rideList .ride-item:not(.recording)")
        check("ride saved to history after Stop", saved.count() == 1)
        hist_text = saved.first.inner_text()
        print("  history row: " + hist_text.replace("\n", " | "))
        check("saved ride shows distance", re.search(r"Distance\s+[\d.]+", hist_text) is not None)
        page.locator(".rides").scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        page.screenshot(path=SHOT)
        print("  screenshot: " + SHOT)
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
        check("version line shows v1.1.0", "v1.1.0" in page.locator("#versionLine").inner_text())
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
            saved.first.locator(".ride-actions .ghost-btn").first.click()
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
        items.first.locator(".ride-actions .ghost-btn").nth(1).click()
        page.wait_for_timeout(500)
        check("delete removes one ride", page.locator("#rideList .ride-item:not(.recording)").count() == 1)
        page.locator("#rideList .ride-item:not(.recording)").first.locator(".ride-actions .ghost-btn").nth(1).click()
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
        browser.close()

    failed = [r for r in results if not r[1]]
    print("\n%d passed, %d failed." % (len(results) - len(failed), len(failed)))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

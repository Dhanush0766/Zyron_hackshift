"""Browser end-to-end check against a RUNNING server (not part of pytest).

  python tests/e2e_ui.py http://127.0.0.1:8000
Requires: pip install playwright && playwright install chromium
"""
import json
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"


def get(path):
    return json.load(urllib.request.urlopen(BASE + path))


def wait_idle():
    for _ in range(60):
        if not get("/api/sim")["status"].get("running"):
            return
        time.sleep(0.5)
    raise SystemExit("scenario did not finish")


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1360, "height": 900})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text))

    req = urllib.request.Request(BASE + "/api/medications/1/refill", data=b'{"count": 30}',
                                 headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req)                      # make sure box 1 is not empty from earlier scenarios
    pg.goto(BASE + "/#sim")
    pg.click("[data-run=abnormal_vitals]")
    wait_idle()
    dose_id = get("/api/sim")["status"]["dose_id"]
    assert get(f"/api/doses/{dose_id}")["dose"]["state"] == "HELD"

    pg.goto(BASE + "/#review")
    pg.wait_for_selector(f"[data-act=approve][data-id='{dose_id}']")
    pg.click(f"[data-act=approve][data-id='{dose_id}']")          # no name yet
    assert "reviewer name" in pg.inner_text("#rverr").lower()
    pg.fill("#rv", "Dr. Demo")
    pg.click(f"[data-act=approve][data-id='{dose_id}']")
    time.sleep(1)
    d = get(f"/api/doses/{dose_id}")
    assert d["dose"]["state"] == "DISPENSE_SENT", d["dose"]["state"]
    assert d["dose"]["reviewer"] == "Dr. Demo"
    assert len(d["commands"]) == 1

    for view in ("overview", "patient", "doctor", "caretaker", "doses", "review", "vitals", "alerts", "meds", "devices", "audit", "sim"):
        pg.goto(f"{BASE}/#{view}")
        pg.wait_for_timeout(700)
        pg.screenshot(path=f"ui-{view}.png", full_page=True)
    b.close()

assert not errors, errors
print(f"E2E UI OK: dose {dose_id} held by gate, approved in browser by named reviewer, one command issued; "
      f"12 views rendered without JS errors")

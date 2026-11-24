# Traffic worker on one Windows PC

Service: `D:\traffic-worker\linux-pilot`, Python environment: `D:\traffic-worker\venv`.
Run `python -m traffic_service.server --root D:\traffic-worker\linux-pilot` from that directory.
The installed Task Scheduler task `KamDispatch Traffic Service` starts it at logon
and checks once a minute. A process lock prevents duplicates. `KamDispatch Traffic
Browser` separately maintains the dedicated visible Yandex profile; sign-in to
Windows is required for its interactive browser session. Neither task modifies
the personal browser profile or existing City Sim containers.

## Inputs and outputs

- `data/graph_v3.json`: unchanged graph; SHA is frozen in `data/plan.json`.
- `data/plan.json`: finite measured 11-frame zoom15 / 4096×4096 plan, 12% overlap.
- `data/traffic.sqlite`: durable queue, leases, cancellation, settings, run history.
- `data/latest.json`: atomic current result; previous publication in `previous.json`.
- `spool/<run>/<tile>-a<attempt>/`: off/on PNG, controls, metadata, analysis, ribbons.
- PNG files expire after **10800 seconds**, checked once a minute. JSON audit remains.
- Disk reserve: 5 GiB. Low space pauses capture rather than deleting fresh inputs.

API binds **127.0.0.1:8765 only**. External port 3003 remains SSH, not this API.
GET `/health`, `/api/status`, `/api/latest`, `/api/graph`.
POST `/api/run`, `/api/pause`, `/api/resume`, `/api/cancel`, `/api/rollback`, with
`Content-Type: application/json` and body `{}`. Host and Origin are checked.
There is no arbitrary shell, URL, Docker, or filesystem execution API.

PowerShell example:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/status
Invoke-RestMethod http://127.0.0.1:8765/api/resume -Method Post -ContentType application/json -Body '{}'
Invoke-RestMethod http://127.0.0.1:8765/api/pause -Method Post -ContentType application/json -Body '{}'
```

The optional diagnostic HTML is served at `/`; user requested no further interface
work. Consume the API/files in an existing map. A desktop shortcut was created
while diagnosing the initial installation; it is not needed for the worker.

## Collection and recovery

One browser slot obtains a pair in the existing Yandex Maps tab; it never closes
the browser. Calibration is measured once per run, not per frame: the map URL gives
the authoritative center, the `.map-container` box gives the pixel anchor, and one
public UI control point (right click -> "Что здесь?" -> Enter) confirms it within
2 px. Measured 24.11.2026: the anchor equals the container center on X and
`height/2 + 10` on Y (a 20 px top inset), reproduced on three viewports to 0.02 px
and on four real control points to 0.9 px. The one-point anchor then serves every
tile, because zoom and viewport stay fixed and only the center changes.

The menu item must be activated with `Enter`. A synthetic DOM `click()` does not
run Yandex's handler and was the cause of the earlier calibration timeouts. The
service still requires a fresh `whatshere[point]` value and a closed prior card.
Screenshots use raw CDP `Page.captureScreenshot`: Playwright's animation/font waits
took 6-20 s per frame on the target PC, while CDP takes 0.1 s for a clipped
readiness frame and about 3 s for a 4096x4096 PNG. No private map APIs are accessed.

Two offline analysis processes run independently of capture. The observed display
palette is separate from the 2GIS palette. Ambiguous/no-colour graph intervals stay
unknown; source direction and speed are not inferred. Source raster and graph
assignment are separate results. Geographic coverage is not measurement coverage.
Per-frame timestamps and source hashes are retained; provider update time is unknown.

The scheduler requests a finite run every 300 seconds and never overlaps runs.
Missed ticks are skipped. A warm restart resumes completed artifacts; after a long
interruption (>10 minutes), the old run is marked interrupted rather than mixing
old/new observations. Stale lease results cannot overwrite a new attempt. On
provider 403/429/CAPTCHA, collection pauses immediately without bypass. Repeated
errors pause collection. Pause/cancel stops further jobs; already running offline
analysis may finish but is not published as a cancelled run.

Latest results publish atomically. Partial runs are identified; missing intervals
are unknown. Last complete publication survives cancellation/failure. Restoring a
previous version requires no active run. Data older than 600 seconds is visually
stale; exact capture age is available. This is a colour observation product, not
independently validated speed or winter-road passability data.

## Dependencies and provenance

Python 3.10+, playwright==1.64.1 (CDP attach; no browser install), numpy==2.2.6,
Pillow==11.3.0, opencv-python-headless==4.11.0.86. SQLite is bundled with Python.
Leaflet 1.9.4 is vendored with its BSD license for the diagnostic HTML; basemap
© OpenStreetMap contributors, source observations © Yandex. See container SOURCES.md
for the optional Linux smoke image; production capture uses Windows Yandex.

Projection reference: https://yandex.ru/dev/jsapi-v2-1/doc/en/v2-1/theory/
Source UI control: https://www.yandex.ru/support/maps/ru/concept/stoppers

## Checks

`python -m pytest tests/test_traffic_product.py tests/test_traffic_queue.py tests/test_traffic_server.py`
plus the existing graph/capture regression tests. Real capture is kept out of CI.
Calibration unit tests replay the real 23.11.2026 control points.
Verified 24.11.2026 on the target PC: a full city pass accepted 11/11 frames in
110.7 s, footprint coverage 11689/11689 corridors, and the 300 s scheduler started
the next run without overlap. Retention was exercised with a four-hour-old PNG:
the image was deleted, a fresh PNG and the audit JSON were kept.

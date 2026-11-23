# GRAPH-002 · offline corridor pilot

23.11.2026, Asia/Kamchatka. ФАКТ/РАСЧЁТ, experimental_unvalidated.
Base main: 123927c54bd43604478ff817f806d61b4992a4bc.
Source analyzer, capture CLI, evaluation and 17 tests imported selectively from
PR #7 head 37fe2cac1b6cac8275f0b4cb4424039c41d8244f (open, not merged).
Only analyzer change: normalize OpenCV HoughLinesP output to N×4, supporting
both returned array shapes encountered in local runtime.

## Verified

Actual graph_v3: 23172 directed edges → 11689 physical corridors. Pairing requires
reciprocal reverse_edge_id, swapped endpoints, exactly reversed geometry and equal
length. No grouping by osm_way or proximity. Source graph remains byte-identical.
Separate segments.json and observations.json retain graph/source SHA256, timestamps,
provider_updated_at (null when unknown), source line IDs and rejection reasons.
Intervals use declared graph length and normalized projected polyline arclength;
this is a pilot approximation, not a surveyed metric projection. Reverse intervals
are mirrored. Colors stay display classes, direction unresolved, scope road_corridor.
No speeds, travel time or dispatch integration. Unknown never becomes free.

25 offline tests passed (17 upstream + 8 adapter); deterministic synthetic paired PNG
smoke passed. Parallel corridors compete even with the same OSM identity. Mixed colors,
missing color, outside viewport and stale/future captures remain unknown. Tolerance
3 px and freshness 900 s are experimental configuration, not validated thresholds.
Coverage is interval fraction of corridor length, not confidence. No empirical
precision, recall or citywide completeness claim. Manual three-section development /
holdout evaluation remains not_verified until real paired PNG and annotations exist.

## Reproduce

Use Python with numpy, Pillow, OpenCV; optional capture needs Playwright and installed
Chromium browser. requirements-traffic.txt comes from upstream; local verification
used system image dependencies with a separate venv for pytest/Playwright.

```
python -m pytest tests/test_traffic_graph.py tests/test_traffic_image.py tests/test_traffic_lines.py tests/test_traffic_validation.py tests/test_traffic_evaluation.py -q
python scripts/graph_traffic.py snegoplan/mapprep/out/graph_v3.json /tmp/graph-export
python scripts/graph_traffic_smoke.py /tmp/graph-synthetic
python scripts/graph_traffic.py snegoplan/mapprep/out/graph_v3.json /tmp/graph-review --capture /path/to/saved-pair --now 2026-11-23T10:01:00+12:00
```

Open generated review.html locally. Four independent controls show graph, source
lines, assignments and unknown. Click interval for provenance/rejection details.
Canvas uses calibrated screenshot coordinates; no online basemap dependency.
Keep provider PNG local. Before recurring capture or redistribution, verify the
specific provider data terms. This branch makes no recurring requests.

## Capture and visual result

One manual capture attempt on 23.11.2026 17:52 Asia/Kamchatka through isolated
Yandex Chromium stopped: Locator.click timeout 20000 ms, UI text «Что здесь?»
not found. No paired real PNG obtained, no retries or bypass. Local failure record:
/private/tmp/graph-2gis-manual-capture/capture-failure.json. Real-data validation
and three manually annotated sections remain not_verified.

Synthetic review opened in headless Yandex: four checkboxes toggled, zero JS errors;
canvas screenshot visually inspected: localized red interval and gray unknown.
Local image versions: OpenCV 5.0.0, numpy 2.4.4, Pillow 12.2.0.
Graph Git blob remains edf56d39621c06956f48fb5ca85374ed18143812.
Next: repair capture against current UI with a separately authorized bounded attempt,
then manual development/holdout annotations and empirical assignment metrics.

## 23.11.2026 · real capture repair

Capture startup race repaired: wait 4 s after UI readiness, allow one bounded retry
of context-menu opening (3 s + 1.5 s). Four UI points succeeded; independent calibration
error rounded to 0 px. Real capture at 2026-11-23T05:57:58.566510Z (17:57:58 Kamchatka).
Local-only data: research/traffic-2gis-2026-11-23 in source workspace. Source lines:
392 free, 38 slow, 4 congested. Graph review covers 169 corridors; at capture time
1773 free, 156 slow, 11208 unknown directed sample intervals. Counts are samples,
not independent roads or validation accuracy. No congested interval assigned.
Browser assignments-only view confirms 896 free and 78 slow physical sample bins;
clicked slow interval shows edge ID, source IDs and reason. Zero JS errors.
Basemap now uses the paired reference screenshot; current age is recomputed when
opening review. Historical reproduction uses --now equal to captured_at; a stale
warning still appears based on actual viewer time. This is a saved snapshot, not live.
Full three-section manual empirical annotation remains not_verified.

## 23.11.2026 · whole-graph snapshot

User requested all graph sections. A bounded four-tile 4096×4096 capture at zoom 14
was planned from measured calibration footprint, with 10% overlap. All four actual
captures passed four-point UI calibration; complete polyline clipping against their
rectangle union verifies 11689 / 11689 corridors covered, no missing polyline.
Source graph SHA256 c29d4675e8f230015cde370b0d6e13b9d13f7a93f65516bf7f889c34b8162c76.
Images stay local: research/traffic-2gis-city-2026-11-23/tiles in source workspace.
Snapshot times 18:31:33–18:34:00 Asia/Kamchatka (06:31:33–06:34:00 UTC).
Evidence with input hashes, calibration controls and footprint check:
[CITY_TRAFFIC_EVIDENCE.json](CITY_TRAFFIC_EVIDENCE.json).

Whole-graph city.html contains every corridor, pan/zoom/reset, unknown/observations
switches and per-interval provenance. Latest timestamp wins in overlap, including
unknown; outside_capture cannot overwrite another tile's data. Gaps remain unknown.
Intervals are physical corridor display observations; no directional speed inference.
Known length: free 68948.898 m, slow 2948.008 m, congested 579.283 m;
unknown 1183304.111 m. Full geographic image coverage does NOT mean full traffic
measurement: lower zoom, missing provider ribbons and conservative geometry rejection
leave most graph length unknown. Assignment accuracy remains unvalidated; no three-section
manual holdout study was performed. No citywide accuracy claim.

Browser: 11689 roads rendered; assignments-only contains 704 free, 96 slow and 25
congested merged intervals. Congested card on улица Академика Королёва opened with
source hash, timestamp and interval. Toggles, zoom and reset checked; zero JS errors;
full city screenshot visually inspected. 30 offline tests passed.

Reproduce bounded run (existing verified captures are reused, failures stop the run):

```
python scripts/traffic_city.py plan snegoplan/mapprep/out/graph_v3.json /tmp/city-plan.json /path/to/large-probe
python scripts/capture_traffic_city.py snegoplan/mapprep/out/graph_v3.json /tmp/city-plan.json /tmp/city-tiles --chrome /path/to/chromium
```

Raw screenshots and generated maps are local only, not added to public Git. No
recurring/background collection or automatic dispatch connection was configured.

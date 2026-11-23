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

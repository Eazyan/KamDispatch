from dataclasses import replace
from datetime import timedelta

from monitoring.bus import Confirmation, baselines, measure_passages, observations
from monitoring.bus_demo import START, dashboard_demo, demo_inputs, demo_sections, evaluate_demo, last_position_at, trip


def test_dispatch_demo_distinguishes_delays_from_a_single_stop_and_stale_data():
    demo = dashboard_demo()
    assert demo["source"] == "simulation"
    scenarios = {scenario["id"]: scenario["rows"] for scenario in demo["scenarios"]}
    for scenario_id, reason, vehicles in (("camera", "camera", 1), ("two-buses", "buses", 2)):
        active = [row for row in scenarios[scenario_id] if row["status"] == "check"]
        assert len(active) == 1
        assert (active[0]["reason"], active[0]["vehicle_count"]) == (reason, vehicles)
        assert active[0]["observed_seconds"] == 240
    assert all(row["status"] != "check" for row in scenarios["single-stop"])
    assert all(row["status"] == "insufficient" for row in scenarios["signal-expired"])


def test_measurement_excludes_boarding_and_rejects_detours_missing_points_and_old_data():
    sections = demo_sections()
    section = sections[0]
    boarding = list(trip(section, "boarding", START))
    waiting = replace(boarding[0], measured_at=START - timedelta(seconds=30),
                      received_at=START - timedelta(seconds=27))
    reverse = list(trip(section, "reverse", START, reverse=True))
    missing = list(trip(section, "gap", START))
    passages = list(measure_passages(sections, [
        waiting, *boarding, *boarding, *reverse, *missing[:8], *missing[21:],
        *trip(section, "detour", START, detour=True), *trip(section, "old", START, late=True),
    ]))
    assert {(item.vehicle, item.direction, round(item.seconds)) for item in passages} == {
        ("boarding", "forward", 120), ("reverse", "reverse", 120),
    }
    assert len(passages) == 2


def test_baseline_requires_three_local_dates_and_detects_delays_per_direction():
    sections = demo_sections()
    reference, _, _ = demo_inputs(sections)
    passages = list(measure_passages(sections, reference))
    assert len(baselines(passages)) == 4
    assert not baselines([item for item in passages if item.entered_at >= START - timedelta(days=2)])
    section = sections[0]
    positions = list(trip(section, "slow", START, 240))
    current = list(measure_passages(sections, positions))
    now = START + timedelta(minutes=5)
    baseline = baselines(passages)

    def row(confirmations, available_at=None):
        return observations(sections, current, baseline, confirmations, now, available_at or now)[0]

    assert row([Confirmation(section.id, "forecast", now)])["status"] == "insufficient"
    assert row([Confirmation(section.id, "report", now, "reverse")])["status"] == "insufficient"
    assert row([Confirmation(sections[1].id, "report", now)])["status"] == "insufficient"
    assert row([Confirmation(section.id, "report", now - timedelta(minutes=5))])["status"] == "insufficient"
    assert row([Confirmation(section.id, "report", now)])["reason"] == "report"
    assert row([Confirmation(section.id, "report", now)], now - timedelta(seconds=91))["status"] == "insufficient"
    early = current[0].ended_at
    assert observations(sections, current, baseline, [], early, last_position_at(positions, early))[0]["vehicle_count"] == 0


def test_simulation_comparison_reports_delays_misses_and_false_hints():
    results = evaluate_demo()
    headways = {(item["scenario"], item["window"]): item for item in results}
    assert headways["headway-5", 10]["delay_seconds"] > 5 * 60
    assert headways["headway-10", 10]["missed"] == 1
    assert headways["headway-10", 20]["delay_seconds"] > 10 * 60
    assert all(headways["headway-40", window]["missed"] == 1 for window in (5, 10, 20))
    assert all(item["false_hints"] == 0 for item in results)


def test_rnis_public_response_shape_uses_measurement_time_and_hides_provider_ids():
    import json
    from datetime import datetime, timezone
    from pathlib import Path
    from monitoring.bus_worker import parse_positions

    path = Path(__file__).with_name("fixtures") / "rnis_bus_samples.json"
    first, second = json.loads(path.read_text())
    captures = []
    for response in (first, second):
        newest = max(row["DeviceTime"] for row in response["payload"]["vehicles"])
        response["payload"]["vehicles"].append({
            **response["payload"]["vehicles"][0], "VehicleUUID": "other-vehicle", "VehicleType": "",
        })
        at = datetime.fromtimestamp(newest + 1, timezone.utc)
        captures.append({point.vehicle: point for point in parse_positions(response, at)})
        assert parse_positions(response, at + timedelta(minutes=2)) == []
    previous, current = captures
    assert previous.keys() == current.keys() and len(current) == 2
    for key, point in current.items():
        assert len(key) == 64
        assert point.measured_at > previous[key].measured_at
        assert (point.lon, point.lat) != (previous[key].lon, previous[key].lat)

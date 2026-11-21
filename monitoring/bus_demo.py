"""Build and evaluate synthetic bus tracks on the bundled Kamchatka roads."""
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import lru_cache
import json
from pathlib import Path
from random import Random
from statistics import median

from .bus import Confirmation, Position, Section, baselines, measure_passages, observations, usable_position

ROADS = Path(__file__).with_name("snow_segments.json")
SECTION_IDS = ("osm-94577830", "osm-94577832")
START = datetime.fromisoformat("2026-11-20T03:02:00+12:00")
HEADWAYS = (5, 10, 20, 40)
WINDOWS = (5, 10, 20)


def demo_sections():
    roads = {road["id"]: road for road in json.loads(ROADS.read_text(encoding="utf-8"))}
    return [
        Section(section_id, roads[section_id]["name"], roads[section_id]["coordinates"], .15, .85)
        for section_id in SECTION_IDS
    ]


def location(section, distance):
    for (ax, ay), (bx, by), length in zip(section.vertices, section.vertices[1:], section.lengths):
        if 0 < length and distance <= length:
            fraction = distance / length
            x, y = ax + fraction * (bx - ax), ay + fraction * (by - ay)
            return section.origin[0] + x / section.lon_scale, section.origin[1] + y / 111_195
        distance -= length
    x, y = section.vertices[-1]
    return section.origin[0] + x / section.lon_scale, section.origin[1] + y / 111_195


def trip(section, vehicle, starts, duration=120, *, reverse=False, pause=0, detour=False, late=False):
    entry_distance = section.entry
    inside_distance = section.exit - section.entry
    for elapsed in range(0, duration + pause + 41, 5):
        # Leave 20 seconds on either side of the measurement section.
        if elapsed < 20:
            distance = entry_distance * elapsed / 20
        elif elapsed > 20 + duration + pause:
            distance = section.exit + (section.length - section.exit) * (elapsed - 20 - duration - pause) / 20
        else:
            moving = elapsed - 20
            if moving >= duration / 2:
                moving = max(duration / 2, moving - pause)
            distance = section.entry + inside_distance * moving / duration
        lon, lat = location(section, section.length - distance if reverse else distance)
        if detour and .35 * section.length < distance < .65 * section.length:
            lon += .003
        at = starts + timedelta(seconds=elapsed)
        yield Position(vehicle, lon, lat, at, at + timedelta(seconds=120 if late else 3))


def demo_inputs(sections):
    rng = Random(2097)
    reference = [
        point
        for section in sections
        for day in (1, 2, 3)
        for reverse in (False, True)
        for index in range(4)
        for point in trip(section, f"ref-{section.id}-{day}-{reverse}-{index}",
                          START + timedelta(days=-day, minutes=index * 7),
                          120 + rng.choice((-10, -5, 0, 5, 10)), reverse=reverse)
    ]

    def make_trip(section_index, vehicle, minute=0, duration=120, **kwargs):
        return list(trip(sections[section_index], vehicle, START + timedelta(minutes=minute), duration, **kwargs))

    camera = Confirmation(sections[0].id, "camera", START + timedelta(minutes=5), "forward")
    scenarios = {
        "camera": {
            "title": "Автобус и камера", "minutes": 6, "positions": make_trip(0, "cam-bus", duration=240),
            "confirmations": [camera],
        },
        "two-buses": {
            "title": "Два автобуса", "minutes": 11,
            "positions": make_trip(1, "bus-a", duration=240) + make_trip(1, "bus-b", 5, 240),
            "confirmations": [],
        },
        "single-stop": {
            "title": "Остановка одной машины", "minutes": 9,
            "positions": make_trip(1, "paused", pause=300) + make_trip(1, "normal", 4),
            "confirmations": [],
        },
        "signal-expired": {
            "title": "Данные устарели", "minutes": 22,
            "positions": make_trip(1, "bus-a", duration=240) + make_trip(1, "bus-b", 5, 240)
            + make_trip(1, "late", 18, late=True),
            "confirmations": [],
        },
    }
    truth = {
        "camera": (sections[0].id, START, "Замедление с подтверждением камеры"),
        "two-buses": (sections[1].id, START, "Устойчивое замедление"),
        "single-stop": (None, START, "Пятиминутная стоянка одной машины"),
        "signal-expired": (sections[1].id, START, "Устойчивое замедление до потери данных"),
    }

    for interval in HEADWAYS:
        scenario_id = f"headway-{interval}"
        scenarios[scenario_id] = {
            "title": f"Рейсы каждые {interval} мин", "minutes": 50,
            "positions": make_trip(1, "first", duration=240) + make_trip(1, "following", interval, 240),
            "confirmations": [],
        }
        truth[scenario_id] = (sections[1].id, START, "Устойчивое замедление")

    boarding = make_trip(0, "boarding")
    # Waiting before the entry boundary does not belong to the measured passage.
    depot = boarding[0]
    waiting = [
        Position(depot.vehicle, depot.lon, depot.lat, depot.measured_at - timedelta(seconds=seconds),
                 depot.received_at - timedelta(seconds=seconds))
        for seconds in range(90, 0, -5)
    ]
    scenarios["normal"] = {
        "title": "Посадка, объезд и встречный рейс", "minutes": 12,
        "positions": waiting + boarding + boarding + make_trip(0, "detour", 3, 240, detour=True)
        + make_trip(0, "forward", duration=240) + make_trip(0, "reverse", 5, 240, reverse=True),
        "confirmations": [],
    }
    truth["normal"] = (None, START, "Посадка, объезд, дубли GPS и встречный рейс")
    return reference, scenarios, truth


def last_position_at(positions, now):
    return max((item.measured_at for item in positions if item.received_at <= now and usable_position(item)),
               default=None)


@lru_cache(maxsize=1)
def dashboard_demo():
    sections = demo_sections()
    reference, scenarios, _ = demo_inputs(sections)
    baseline = baselines(measure_passages(sections, reference))
    snapshots = []
    for scenario_id in ("two-buses", "camera", "single-stop", "signal-expired"):
        scenario = scenarios[scenario_id]
        now = START + timedelta(minutes=scenario["minutes"])
        positions = [point for point in scenario["positions"] if point.received_at <= now]
        rows = observations(sections, measure_passages(sections, positions), baseline,
                            scenario["confirmations"], now, last_position_at(positions, now))
        snapshots.append({"id": scenario_id, "title": scenario["title"], "as_of": now, "rows": rows})
    return {"source": "simulation", "scenarios": snapshots}


def evaluate_demo():
    sections = demo_sections()
    reference, scenarios, truth = demo_inputs(sections)
    baseline = baselines(measure_passages(sections, reference))
    results = []
    for scenario_id, scenario in scenarios.items():
        passages = list(measure_passages(sections, scenario["positions"]))
        section_id, onset, _ = truth[scenario_id]
        stop = START + timedelta(minutes=scenario["minutes"])
        checkpoints = sorted({START, *(item.received_at for item in passages if item.received_at <= stop),
                              *(item.observed_at for item in scenario["confirmations"] if item.observed_at <= stop)})
        for window in WINDOWS:
            first = None
            false_hints = 0
            active = set()
            expiry = {
                *(item.ended_at + timedelta(minutes=window) for item in passages),
                *(item.observed_at + timedelta(minutes=5) for item in scenario["confirmations"]),
            }
            for now in sorted(set(checkpoints) | {at for at in expiry if START <= at <= stop}):
                rows = observations(sections, passages, baseline, scenario["confirmations"], now,
                                    last_position_at(scenario["positions"], now), window)
                hints = {(row["section_id"], row["direction"]) for row in rows if row["status"] == "check"}
                correct = (section_id, "forward")
                if correct in hints and first is None:
                    first = now
                false_hints += len(hints - active - {correct})
                active = hints
            results.append({
                "scenario": scenario_id, "window": window, "missed": int(section_id is not None and first is None),
                "false_hints": false_hints, "delay_seconds": (first - onset).total_seconds() if first else None,
            })
    return results


def write_fixtures(directory):
    reference, scenarios, truth = demo_inputs(demo_sections())
    inputs = {
        "reference": [asdict(point) for point in reference],
        "scenarios": {
            key: {**scenario, "positions": [asdict(point) for point in scenario["positions"]],
                  "confirmations": [asdict(item) for item in scenario["confirmations"]]}
            for key, scenario in scenarios.items()
        },
    }
    for filename, data in (("bus-input.json", inputs), ("bus-truth.json", truth)):
        (directory / filename).write_text(json.dumps(data, default=datetime.isoformat, ensure_ascii=False),
                                          encoding="utf-8")


def report():
    results = evaluate_demo()
    print("| Окно, мин | Медиана задержки, мин | Пропуски | Ложные подсказки |")
    print("|---:|---:|---:|---:|")
    for window in WINDOWS:
        rows = [row for row in results if row["window"] == window]
        delays = [row["delay_seconds"] / 60 for row in rows if row["delay_seconds"] is not None]
        delay = f"{median(delays):.1f}" if delays else "—"
        print(f"| {window} | {delay} | {sum(row['missed'] for row in rows)} | {sum(row['false_hints'] for row in rows)} |")
    print("\nЗадержка на Карьерной при разных интервалах, минуты")
    print("\n| Интервал рейсов | Окно 5 мин | Окно 10 мин | Окно 20 мин |")
    print("|---:|---:|---:|---:|")
    for interval in HEADWAYS:
        rows = [row for row in results if row["scenario"] == f"headway-{interval}"]
        values = ["пропуск" if row["missed"] else f"{row['delay_seconds'] / 60:.1f}" for row in rows]
        print(f"| {interval} | {' | '.join(values)} |")


if __name__ == "__main__":
    report()

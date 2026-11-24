from io import BytesIO
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from fixtures.prepare_demo import build_manifest
from monitoring.adapters import Media, forecast
from monitoring.api import create_app
from monitoring.config import Settings
from monitoring.city_worker import update_city
from monitoring.db import connect, migrate, prepare_run
from monitoring.operations import enqueue, save_forecast, save_summary, store_frame
from monitoring.replay import load_replay
from monitoring.scene import event_signals
from monitoring.snow import demo_forecast
from monitoring.worker import analysis_tick, claim_job, mail_tick


def model_fixture_rows():
    return build_manifest()["frames"][:4]


def test_four_control_labels():
    first, second, heavy, path = [sample["scene"] for sample in model_fixture_rows()]
    at = datetime.now(timezone.utc)

    def observed(scene, count=3, step=20):
        return [{"observed_at": at - timedelta(seconds=i * step), "scene": scene} for i in range(count)]

    assert event_signals(observed(first), True, 90) == {"snowfall": "end", "coverage": "end", "drift": "end"}
    assert event_signals(observed(second), False, 90) == {"snowfall": "end"}
    assert event_signals(observed(heavy, 1), True, 90) == {}
    assert event_signals(observed(heavy, 2), True, 90) == {"snowfall": "urgent"}
    assert event_signals(observed(heavy, 2, 91), True, 90) == {}
    assert event_signals(observed(path), False, 90) == {"snowfall": "watch"}
    assert event_signals(observed(heavy), False, 90) == {"snowfall": "watch"}
    hidden_road = {**first, "falling_snow": "no", "snow_on_ground": "yes",
                   "road_coverage": "mostly_covered", "large_snowdrifts": "yes",
                   "details": {"road_view": "unusable"}}
    assert event_signals(observed(hidden_road), True, 90) == {"snowfall": "end"}
    invalid = {**heavy, "format_valid": False}
    assert event_signals([*observed(heavy, 1), *observed(invalid, 1)], True, 90) == {}


def test_openweather_response():
    at = 1795194000
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
        "hourly": [{"dt": at, "temp": -1, "pop": .8, "weather": [{"id": 601}], "snow": {"1h": 1.4}}],
        "daily": [{"dt": at, "temp": {"day": 0}, "weather": [{"id": 803}]}],
    }))) as client:
        hour, day = forecast(client, "test", 53, 158)
    assert hour["snow"] is True and hour["snow_mm"] == 1.4
    assert day["snow"] is False and day["probability"] is None and day["snow_mm"] is None


@pytest.fixture
def settings(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    result = Settings(database=database, media_dir=tmp_path / "media", mode="replay", freshness=90)
    migrate(result)
    return result


def test_map_and_resident_form_use_the_same_address_point(settings):
    from dataclasses import replace

    settings = replace(settings, mode="demo")
    with connect(settings) as conn:
        prepare_run(conn, "demo", "demo", ())
    with TestClient(create_app(settings)) as client:
        query = {"q": "ул. Ленинская, дом 8"}
        found = client.get("/api/v1/map/search", params=query)
        resident = client.get("/api/v1/citizens/address-search", params=query)
        assert found.status_code == resident.status_code == 200
        hit = found.json()[0]
        point = resident.json()["matches"][0]
        assert hit["coordinates"] == [point["longitude"], point["latitude"]]
        assert point["address"].startswith(hit["name"])


def test_city_snapshot_keeps_geometry_out_of_dashboard(settings):
    from dataclasses import replace

    city = replace(settings, mode="preview", snow_city_url="http://city.test", city_map_url="http://127.0.0.1:8008/")
    with connect(city) as conn:
        prepare_run(conn, city.mode, city.mode, ())
    now = datetime.now(timezone.utc)
    fixture = demo_forecast()
    payload = {**fixture, "start": now.isoformat(), "end": (now + timedelta(hours=3)).isoformat(),
               "scope": "city", "counts": {"watch": 3, "no_signal": 2, "unavailable": 0}, "roads_at": None}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as simulator:
        update_city(city, simulator)
    with TestClient(create_app(city)) as client:
        dashboard = client.get("/api/v1/dashboard").json()
        assert dashboard["city_map_url"] == "http://127.0.0.1:8008/"
        snapshot = dashboard["snow_forecast"]
        assert snapshot["scope"] == "city" and snapshot["segments"] == []
        assert snapshot["counts"] == payload["counts"]
        params = {"calculated_at": snapshot["calculated_at"]}
        assert client.get("/api/v1/snow-roads", params=params).json() == fixture["segments"]
        assert client.get("/api/v1/snow-roads", params={**params, "run": "demo"}).status_code == 404
        segment = next(row for row in fixture["segments"] if row["status"] == "watch")
        raster = client.get(f"/api/v1/snow-raster/{segment['id']}", params=params)
        assert raster.status_code == 200 and raster.json()[0]["png"].startswith("iVBOR")


def test_replay_api_actions_and_isolation(settings, tmp_path):
    run = "test-" + uuid4().hex
    manifest = build_manifest()
    manifest["frames"] = manifest["frames"][:4] + [{
        **manifest["frames"][2], "at": "2026-11-21T03:18:00+12:00", "file": "5.jpg",
    }]
    for i in range(1, 6):
        Image.new("RGB", (40, 25), (i * 20, 40, 80)).save(tmp_path / f"{i}.jpg")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    load_replay(settings, run, path)
    load_replay(settings, run, path)
    with TestClient(create_app(settings)) as client:
        query = {"run": run}
        response = client.get("/api/v1/dashboard", params=query)
        assert response.status_code == 200, response.text
        dashboard = response.json()
        event, = dashboard["events"]
        assert event["severity"] == "urgent"
        assert len(dashboard["cameras"]) == 4
        assert {item["kind"] for item in dashboard["deliveries"]} == {"event", "forecast", "summary"}
        assert {item["stage"] for item in dashboard["deliveries"]} == {"logged"}
        image = client.get(f'/api/v1/frames/{event["latest_frame"]}', params=query)
        assert image.status_code == 200 and image.content.startswith(b"\xff\xd8")
        assert client.get("/api/v1/dashboard", params={"run": "live"}).status_code == 404
        endpoint = f'/api/v1/events/{event["id"]}/actions'
        action = {"request_id": str(uuid4()), "expected_version": event["version"], "state": "en_route"}
        assert client.post(endpoint, params=query, json=action).json() == {"state": "en_route", "version": 2}
        assert client.post(endpoint, params=query, json=action).status_code == 200
        assert client.post(endpoint, params=query, json={**action, "request_id": str(uuid4())}).status_code == 409
        assert len(client.get(endpoint, params=query).json()) == 1


def test_forecast_update_withdrawal_and_summary_schedule(settings):
    run = "test-" + uuid4().hex
    now = datetime.fromisoformat("2026-11-21T07:22:00+12:00")
    period = build_manifest()["forecast"][0]["periods"][0]
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", [])
        save_forecast(conn, settings, run, "Елизово", [period], now)
        save_forecast(conn, settings, run, "Елизово", [period], now + timedelta(minutes=30))
        shifted = {**period, "at": (datetime.fromisoformat(period["at"]) + timedelta(hours=3)).isoformat()}
        save_forecast(conn, settings, run, "Елизово", [shifted], now + timedelta(hours=1))
        save_forecast(conn, settings, run, "Елизово", [{**shifted, "snow": False}], now + timedelta(hours=2))
        save_summary(conn, settings, run, now - timedelta(hours=1))
        assert conn.execute("SELECT count(*) AS n FROM summaries WHERE run_id=%s", (run,)).fetchone()["n"] == 0
        save_summary(conn, settings, run, now)
        save_summary(conn, settings, run, now + timedelta(minutes=10))
        rows = conn.execute("SELECT kind,subject FROM outbox WHERE run_id=%s ORDER BY id", (run,)).fetchall()
    assert len(rows) == 4
    assert [row["subject"].split(" · ")[0] for row in rows] == [
        "Предупреждение о снегопаде", "Уточнение прогноза", "Предупреждение отозвано", "Утренняя сводка",
    ]


def test_worker_processes_frame_and_retryable_model_failure(settings):
    run = "test-" + uuid4().hex
    sample = model_fixture_rows()[2]["scene"]
    at = datetime.now(timezone.utc)
    media = Media(settings)
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", [dict(id="street", name="Улица", area="Демо", road=True)])
        frame_id = store_frame(conn, run, "street", (b"frame bytes", 3, 2, "digest", None, at), media)
        assert store_frame(conn, run, "street", (b"frame bytes", 3, 2, "digest", None, at), media) is None

    requests = []

    def model(req):
        requests.append(req)
        assert req.url.path == "/v1/scene"
        assert b'name="image"' in req.read()
        return httpx.Response(503) if len(requests) == 1 else httpx.Response(200, json=sample)

    from dataclasses import replace
    config = replace(settings, model_url="https://model.invalid")
    with httpx.Client(transport=httpx.MockTransport(model)) as client:
        assert analysis_tick(config, run, media, client)
        with connect(settings) as conn:
            assert conn.execute("SELECT stage FROM jobs WHERE frame_id=%s", (frame_id,)).fetchone()["stage"] == "pending"
            conn.execute("UPDATE jobs SET available_at=now() WHERE frame_id=%s", (frame_id,))
        assert analysis_tick(config, run, media, client)
    with connect(settings) as conn:
        observation = conn.execute("SELECT scene FROM observations WHERE frame_id=%s", (frame_id,)).fetchone()
    assert observation["scene"]["intensity"] == "heavy"


def test_camera_interval_keeps_slow_source_in_analysis_queue(settings):
    run = "test-" + uuid4().hex
    at = datetime.now(timezone.utc) - timedelta(minutes=3)
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", [
            dict(id="slow", name="Панорама", area="Тест", road=False, interval_seconds=600),
            dict(id="fast", name="Улица", area="Тест", road=True, interval_seconds=25),
        ])
        media = Media(settings)
        slow = store_frame(conn, run, "slow", (b"slow", 1, 1, "slow", None, at), media)
        fast = store_frame(conn, run, "fast", (b"fast", 1, 1, "fast", None, at), media)
        assert claim_job(conn, run, settings.freshness)["id"] == slow
        assert conn.execute("SELECT stage,error FROM jobs WHERE frame_id=%s", (fast,)).fetchone() == {
            "stage": "skipped", "error": "frame_expired",
        }


def test_smtp_with_attachment_and_safe_retry_policy(settings):
    from dataclasses import replace
    from email import policy
    from email.parser import BytesParser
    from monitoring.adapters import AmbiguousDelivery

    messages = []

    class Sink:
        async def handle_DATA(self, server, session, envelope):
            messages.append(BytesParser(policy=policy.default).parsebytes(envelope.original_content))
            return "250 OK"

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    controller = Controller(Sink(), hostname="127.0.0.1", port=port)
    controller.start()
    try:
        config = replace(settings, mode="live", recipients=("operator@example.test",), mail_host="127.0.0.1",
                         mail_port=port, mail_security="none")
        media = Media(settings)
        run = "test-" + uuid4().hex
        key = media.put(run, b"attachment")
        with connect(settings) as conn:
            prepare_run(conn, run, "replay", [])
            enqueue(conn, settings, run, "a", "event", "Снегопад", "Тело", key)
            enqueue(conn, settings, run, "a", "event", "Снегопад", "Тело", key)
            enqueue(conn, settings, run, "b", "forecast", "Прогноз", "Тело")
            conn.execute("UPDATE outbox SET stage='queued',recipient=%s WHERE run_id=%s", (config.recipients[0], run))
        assert mail_tick(config, run, media)
        assert len(messages) == 1 and messages[0]["Subject"] == "Снегопад"
        assert next(messages[0].iter_attachments()).get_payload(decode=True) == b"attachment"

        def ambiguous(*args):
            raise AmbiguousDelivery

        assert mail_tick(config, run, media, transport=ambiguous)
        assert not mail_tick(config, run, media)
        with connect(settings) as conn:
            rows = conn.execute("SELECT stage FROM outbox WHERE run_id=%s ORDER BY id", (run,)).fetchall()
        assert [row["stage"] for row in rows] == ["sent", "ambiguous"]
    finally:
        controller.stop()


def test_oidc_validates_signature_and_roles(settings, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    config = replace(settings, mode="live", model_url="https://gpu.example.test",
                     base_url="https://portal.example.test", oidc_issuer="https://login.example.test",
                     oidc_jwks="https://login.example.test/keys", oidc_audience="monitor",
                     operators=("operator",), viewers=("viewer",), crew_subjects={"crew-1": "Бригада 1", "crew-2": "Бригада 2"})
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt",
                        lambda self, token: SimpleNamespace(key=private_key.public_key()))

    def token(subject, audience="monitor"):
        return jwt.encode({"sub": subject, "aud": audience, "iss": config.oidc_issuer,
                           "exp": datetime.now(timezone.utc) + timedelta(minutes=2)},
                          private_key, algorithm="RS256")

    with TestClient(create_app(config)) as client:
        def headers(subject, audience="monitor"):
            return {"Authorization": "Bearer " + token(subject, audience)}

        first, second = uuid4(), uuid4()
        with connect(settings) as conn:
            prepare_run(conn, "live", "live", ())
            for task, name in ((first, "Бригада 1"), (second, "Бригада 2")):
                conn.execute(
                    """INSERT INTO dispatch_tasks (run_id,id,location_key,district,address,kind,work_kind,created_by,assignee,stage,crew_token)
                       VALUES ('live',%s,%s,'Тест','Лесная, 10','drift','inspect','operator',%s,'sent',%s)""",
                    (task, str(task), name, uuid4().hex),
                )
        assert client.get("/api/v1/dashboard", headers=headers("crew-1")).status_code == 403
        assert client.get("/api/v1/dispatch/crew-inbox", headers=headers("viewer")).status_code == 403
        inbox = client.get("/api/v1/dispatch/crew-inbox", headers=headers("crew-1"))
        assert inbox.status_code == 200
        assert {item["id"] for item in inbox.json()} == {str(first)}

        assert client.get("/api/v1/dashboard").status_code == 401
        assert client.get("/api/v1/dashboard", headers=headers("viewer", "another-app")).status_code == 401
        assert client.get("/api/v1/dashboard", headers=headers("unknown")).status_code == 403
        assert client.get("/api/v1/dashboard", headers=headers("operator")).json()["role"] == "operator"
        result = client.post(f"/api/v1/events/{uuid4()}/actions", headers=headers("viewer"), json={
            "request_id": str(uuid4()), "state": "en_route", "expected_version": 1,
        })
        assert result.status_code == 403
        assert client.post("/api/v1/dispatch/reports", headers=headers("viewer"), json={
            "id": str(uuid4()), "district": "Южный", "address": "Лесная, 10", "kind": "drift",
            "description": "Снежный вал", "channel": "phone",
        }).status_code == 403
        assert client.post("/api/v1/dispatch/tasks", headers=headers("viewer"), json={
            "location_key": "section:crossing", "kind": "drift", "work_kind": "inspect",
        }).status_code == 403


def test_capture_jpeg_and_hls(tmp_path):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import shutil
    import subprocess
    from threading import Thread

    from monitoring.adapters import fetch_camera

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg is required for the HLS fixture")
    Image.new("RGB", (80, 60), (90, 110, 150)).save(tmp_path / "still.jpg")
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=red:size=80x60:rate=5",
                    "-t", "1", "-c:v", "libx264", "-f", "hls", str(tmp_path / "stream.m3u8")], check=True)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    handler = partial(QuietHandler, directory=tmp_path)
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            jpeg = {"id": "a", "kind": "jpeg", "url": base + "/still.jpg"}
            status, data = fetch_camera(jpeg, ffmpeg, None)
            assert status == "saved" and data[1:3] == (80, 60)
            assert data[4] is not None
            assert fetch_camera(jpeg, ffmpeg, data[3]) == ("unchanged", None)
            status, data = fetch_camera({"id": "b", "kind": "hls", "url": base + "/stream.m3u8"}, ffmpeg, None)
            assert status == "saved" and data[1:3] == (80, 60)
        finally:
            server.shutdown()
            thread.join()


def test_legacy_model_leaves_severity_unknown(settings):
    from dataclasses import replace
    from monitoring.adapters import analyze

    config = replace(settings, mode="preview", model_url="http://model.example.test", model_contract="snow")

    def reply(request):
        assert request.url.path == "/v1/snow"
        return httpx.Response(200, json={"falling_snow": "yes", "format_valid": True, "evidence": "Visible flakes."})

    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        scene = analyze(client, config, b"frame")
    assert scene["falling_snow"] == "yes"
    assert scene["intensity"] == scene["visibility"] == scene["road_coverage"] == "uncertain"


def test_preview_run_and_demo_media_are_isolated(settings):
    from dataclasses import replace

    config = replace(settings, mode="preview")
    media = Media(settings)
    at = datetime.now(timezone.utc)
    camera = {"id": "scope-test", "name": "Контроль изоляции", "area": "Тест", "road": False}
    with connect(settings) as conn:
        prepare_run(conn, "preview", "preview", [camera])
        frame_id = store_frame(conn, "preview", camera["id"], (b"test", 10, 10, uuid4().hex, at, at),
                               media, analyze=False)
    with TestClient(create_app(config)) as client:
        real = client.get("/api/v1/dashboard").json()
        demo = client.get("/api/v1/dashboard?run=demo").json()
        assert real["mode"] == "preview" and demo["mode"] == "demo"
        assert any(item["frame_id"] == str(frame_id) for item in real["cameras"])
        assert all(item["frame_id"] != str(frame_id) for item in demo["cameras"])
        assert client.get(f"/api/v1/frames/{frame_id}").content == b"test"
        assert client.get(f"/api/v1/frames/{frame_id}?run=demo").status_code == 404


def test_v2_russian_conclusion_and_selective_snow_fallback():
    from monitoring.adapters import analyze

    config = Settings(model_url="http://model.example.test", model_contract="scene_v2")
    result = dict(schema_version="2", model="Weather VLM", prompt_version="snow-scene-5", format_valid=True,
                  falling_snow="yes", snowfall_intensity="heavy", blowing_snow="uncertain", snow_on_ground="yes",
                  large_snowdrifts="uncertain", snowdrift_on_road="uncertain", slush_on_road="uncertain",
                  road_coverage="uncertain", road_obstruction="uncertain", air_view="limited", ground_view="limited",
                  road_view="limited", roadside_view="limited", evidence="Dense flakes are visible.")
    calls = []
    legacy_result = None

    def mock(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/snow":
            return httpx.Response(200, json={"falling_snow": "no", "format_valid": True})
        if request.url.path == "/v1/scene":
            return httpx.Response(200, json=legacy_result) if legacy_result else httpx.Response(404)
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(mock)) as client:
        full = analyze(client, config, b"frame")
        assert calls == ["/v2/scene"]
        assert full["intensity"] == "heavy" and full["visibility"] == "uncertain"
        assert full["evidence"] == "Виден сильный снегопад. На земле виден снег. Обзор в кадре ограничен."
        now = datetime.now(timezone.utc)
        observations = [{"scene": full, "observed_at": now - timedelta(seconds=s)} for s in (0, 20)]
        assert event_signals(observations, True, 90) == {"snowfall": "urgent"}
        assert event_signals(observations, False, 90) == {}

        calls.clear()
        result.update(falling_snow="uncertain", snowfall_intensity="uncertain", road_coverage="clear")
        partial = analyze(client, config, b"frame")
        assert calls == ["/v2/scene", "/v1/snow"]
        assert partial["falling_snow"] == "no" and partial["intensity"] == "none"
        assert partial["road_coverage"] == "clear"
        assert partial["snow_source"] == "snow"
        assert partial["visibility"] == "uncertain"

        result["format_valid"] = False
        calls.clear()
        fallback = analyze(client, config, b"frame")
        assert calls == ["/v2/scene", "/v1/scene", "/v1/snow"]
        assert fallback["details"] is None and fallback["road_coverage"] == "uncertain"
        assert fallback["evidence"] == "Падающего снега не видно."

        calls.clear()
        legacy_result = {**model_fixture_rows()[2]["scene"], "road_coverage": "mostly_covered"}
        compatible = analyze(client, config, b"frame")
        assert calls == ["/v2/scene", "/v1/scene"]
        assert compatible["intensity"] == "heavy"
        assert compatible["road_coverage"] == "mostly_covered"
        assert compatible["snow_source"] == "scene"

        calls.clear()
        legacy_result = {key: value for key, value in legacy_result.items()
                         if key not in {"intensity", "visibility", "road_coverage"}}
        sparse = analyze(client, config, b"frame")
        assert calls == ["/v2/scene", "/v1/scene"]
        assert sparse["falling_snow"] == "yes"
        assert sparse["intensity"] == sparse["road_coverage"] == "uncertain"

        calls.clear()
        result.update(format_valid=True, air_view="unusable", ground_view="unusable", road_view="unusable", roadside_view="unusable")
        blank = analyze(client, config, b"frame")
        assert calls == ["/v2/scene"]
        assert blank["view_quality"] == "unusable" and blank["falling_snow"] == "uncertain"


def test_preview_keeps_previous_analysis_while_processing_new_frame(settings):
    from dataclasses import replace
    from monitoring.operations import record_observation

    config = replace(settings, mode="preview")
    camera_id = "test-" + uuid4().hex
    camera = {"id": camera_id, "name": "Кадры", "area": "Тест", "road": True}
    at = datetime.now(timezone.utc)
    with connect(settings) as conn:
        prepare_run(conn, "preview", "preview", [camera])
        media = Media(config)
        older = store_frame(conn, "preview", camera_id, (b"first", 1, 1, uuid4().hex, None, at - timedelta(seconds=10)), media)
        frame = conn.execute("SELECT * FROM frames WHERE id=%s", (older,)).fetchone()
        record_observation(conn, config, frame, model_fixture_rows()[0]["scene"])
        latest = store_frame(conn, "preview", camera_id, (b"second", 1, 1, uuid4().hex, None, at), media)
    with TestClient(create_app(config)) as client:
        camera_rows = client.get("/api/v1/dashboard").json()["cameras"]
        result = next(row for row in camera_rows if row["id"] == camera_id)
    assert result["frame_id"] == str(latest) and result["analyzed_frame_id"] == str(older)
    assert result["analysis_stage"] == "pending"
    assert result["scene"]["falling_snow"] == "no"

    with connect(settings) as conn:
        conn.execute("UPDATE frames SET observed_at=%s WHERE id=%s", (at - timedelta(minutes=3), older))
    with TestClient(create_app(config)) as client:
        camera_rows = client.get("/api/v1/dashboard").json()["cameras"]
        result = next(row for row in camera_rows if row["id"] == camera_id)
    assert result["scene"] is None and result["analyzed_frame_id"] is None
    assert result["archived_analysis"]["frame_id"] == str(older)
    assert datetime.fromisoformat(result["archived_analysis"]["at"]) == at - timedelta(minutes=3)
    assert result["archived_analysis"]["scene"]["falling_snow"] == "no"


def test_camera_location_and_slow_source_freshness(settings):
    from dataclasses import replace

    config = replace(settings, mode="preview")
    camera_id = "test-" + uuid4().hex
    coords = [158.639, 53.044]
    camera = {"id": camera_id, "name": "Панорама", "area": "Тест", "road": False,
              "coordinates": coords, "location_note": "Ориентир", "interval_seconds": 1800}
    now = datetime.now(timezone.utc)
    with connect(settings) as conn:
        prepare_run(conn, "preview", "preview", [camera])
        media = Media(config)
        frame = (b"frame", 10, 10, uuid4().hex, now - timedelta(minutes=45), now - timedelta(minutes=22))
        saved_id = store_frame(conn, "preview", camera_id, frame, media, analyze=False)
    with TestClient(create_app(config)) as client:
        result = next(row for row in client.get("/api/v1/dashboard").json()["cameras"] if row["id"] == camera_id)
    assert result["coordinates"] == coords and result["location_note"] == "Ориентир"
    assert result["frame_id"] == str(saved_id) and result["status"] == "available"


def test_five_day_forecast_fallback_and_interval():
    from monitoring.operations import warning_window

    calls = []
    at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)

    def endpoint(request):
        calls.append(request.url.path)
        if request.url.path.endswith("onecall"):
            return httpx.Response(401)
        return httpx.Response(200, json={"list": [{
            "dt": at.timestamp(), "main": {"temp": -2}, "weather": [{"id": 601}], "pop": .9,
            "snow": {"3h": 1.4}, "wind": {"speed": 5, "deg": 250, "gust": 9},
        }]})

    with httpx.Client(transport=httpx.MockTransport(endpoint)) as client:
        period, = forecast(client, "test", 53, 158)
    assert calls == ["/data/3.0/onecall", "/data/2.5/forecast"]
    assert period["horizon"] == "three_hourly" and period["snow_mm"] == 1.4 and period["gust_mps"] == 9
    window = warning_window([period], at - timedelta(hours=1), .5)
    assert datetime.fromisoformat(window["start"]) == at - timedelta(hours=3)
    assert datetime.fromisoformat(window["end"]) == at


def test_inspection_queue_explains_and_de_duplicates():
    from monitoring.dispatch import inspection_queue

    now = datetime.now(timezone.utc)
    base = dict(road=True, state="new", severity="watch", ended_at=None, evidence_stale=False,
                camera_id="A", id=uuid4(), name="Перекрёсток", area="Елизово", road_priority=1,
                road_priority_reason="Пешеходный переход", last_seen=now, kind="snowfall", reason="Снегопад")
    urgent = {**base, "id": uuid4(), "severity": "urgent"}
    result = inspection_queue([base, urgent, {**base, "camera_id": "B", "road_priority": 3},
                               {**urgent, "camera_id": "C", "evidence_stale": True},
                               {**urgent, "camera_id": "D", "state": "en_route"},
                               {**urgent, "camera_id": "E", "road": False}])
    assert [item["event_id"] for item in result] == [urgent["id"], base["id"]]
    assert result[0]["factors"][0] == "Пешеходный переход"


def test_dispatch_groups_cameras_and_links_reports_to_one_task(settings):
    from concurrent.futures import ThreadPoolExecutor

    run, other = ("test-" + uuid4().hex for _ in range(2))
    now = datetime.now(timezone.utc)
    section = dict(road=True, area="Елизово", district="Центр",
                   section_id="crossing", section_name="Перекрёсток")
    cameras = [dict(id=key, name=key, **section) for key in ("a", "b", "c")]
    events = [uuid4() for _ in cameras]
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", cameras)
        prepare_run(conn, other, "replay", cameras)
        for camera, event_id in zip(cameras, events):
            conn.execute(
                """INSERT INTO events (id,run_id,camera_id,kind,severity,reason,opened_at,last_seen)
                   VALUES (%s,%s,%s,'snowfall',%s,'Снегопад',%s,%s)""",
                (event_id, run, camera["id"], "urgent" if camera["id"] == "b" else "watch", now, now),
            )
    with TestClient(create_app(settings)) as client:
        def request(method, path, run_id=run, **kwargs):
            result = client.request(method, "/api/v1/" + path, params={"run": run_id}, **kwargs)
            assert result.is_success, result.text
            return result.json()

        location = {"location_key": "section:crossing", "kind": "snowfall"}
        report = {**location, "id": str(uuid4()), "description": "Заносит перекрёсток", "channel": "phone"}
        request("POST", "dispatch/reports", json=report)
        request("POST", "dispatch/reports", json=report)
        dashboard = request("GET", "dashboard")
        assert dashboard["dispatch_locations"] == [
            {"location_key": "section:crossing", "district": "Центр", "address": "Перекрёсток"},
        ]
        row, = dashboard["dispatch_queue"]
        assert row["severity"] == "urgent"
        assert {signal["id"] for signal in row["signals"]} == {str(event) for event in events}
        assert len(row["reports"]) == 1
        assert row["task"] is None

        def create():
            return request("POST", "dispatch/tasks", json={**location, "work_kind": "clear"})

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(create), pool.submit(create)
            task = first.result()
            assert task == second.result()
        conflict = client.post("/api/v1/dispatch/tasks", params={"run": run},
                               json={**location, "work_kind": "inspect"})
        assert conflict.status_code == 409

        late_report = {**report, "id": str(uuid4())}
        request("POST", "dispatch/reports", json=late_report)
        row, = request("GET", "dashboard")["dispatch_queue"]
        assert row["task"]["id"] == task["id"]
        assert all(signal["linked"] for signal in row["signals"])
        assert {report["id"] for report in row["reports"] if not report["linked"]} == {late_report["id"]}
        assert request("POST", "dispatch/tasks", json=location) == task
        with connect(settings) as conn:
            conn.execute("UPDATE events SET ended_at=%s WHERE run_id=%s", (now, run))
        row, = request("GET", "dashboard")["dispatch_queue"]
        assert all(signal["linked"] and not signal["active"] for signal in row["signals"])
        assert all(report["linked"] for report in row["reports"])
        assert request("GET", "dashboard", run_id=other)["dispatch_queue"] == []

        manual = {**report, "id": str(uuid4()), "location_key": None, "kind": "drift",
                  "district": "Южный", "address": "Лесная, 10"}
        registered = request("POST", "dispatch/reports", json=manual)
        manual.update(id=str(uuid4()), address="лесная,   10")
        assert request("POST", "dispatch/reports", json=manual)["location_key"] == registered["location_key"]

        road = request("GET", "dispatch/roads")["segments"][0]
        mapped = {**manual, "id": str(uuid4()), "address": None, "road_id": road["id"]}
        mapped_id = request("POST", "dispatch/reports", json=mapped)
        request("POST", "dispatch/reports", json=mapped)
        task = request("POST", "dispatch/tasks", json={
            "location_key": mapped_id["location_key"], "kind": "drift", "work_kind": "inspect",
        })
        endpoints = [[158.64, 53.04], [158.65, 53.05]]
        marked = {**manual, "id": str(uuid4()), "address": None, "endpoints": endpoints}
        point_id = request("POST", "dispatch/reports", json=marked)
        rows = {row["location_key"]: row for row in request("GET", "dashboard")["dispatch_queue"]}
        assert rows[mapped_id["location_key"]]["address"] == road["name"]
        assert rows[mapped_id["location_key"]]["task"]["id"] == task["id"]
        assert rows[mapped_id["location_key"]]["reports"][0]["map_section"]["coordinates"] == road["coordinates"]
        assert rows[point_id["location_key"]]["reports"][0]["map_section"]["coordinates"] == endpoints


def test_dispatch_tracks_assignments_and_overdue_across_districts(settings):
    run, other = ("test-" + uuid4().hex for _ in range(2))
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", ())
        prepare_run(conn, other, "replay", ())
    now = datetime.now(timezone.utc)
    future = (now + timedelta(hours=1)).isoformat()
    with TestClient(create_app(settings)) as client:
        def request(method, path, payload=None, run_id=run):
            return client.request(method, "/api/v1/" + path, params={"run": run_id}, json=payload)

        tasks = []
        for district in ("Центр", "Север", "Юг"):
            report = request("POST", "dispatch/reports", {
                "id": str(uuid4()), "district": district, "address": "Лесная, 10", "kind": "drift",
                "channel": "phone", "description": "Снежный вал", "severity": "urgent",
            }).json()
            tasks.append(request("POST", "dispatch/tasks", {
                "location_key": report["location_key"], "kind": "drift", "work_kind": "inspect",
            }).json()["id"])
        path = f"dispatch/tasks/{tasks[0]}"
        assert request("PUT", path + "/assignment", {
            "version": 0, "assignee": "Бригада 1", "due_at": now.isoformat(),
        }).status_code == 422
        assert request("PUT", path + "/assignment", {
            "version": 0, "assignee": "Бригада 1", "due_at": future,
        }).json() == {"assignment_version": 1}
        receipt = request("PUT", path + "/receipt", {"version": 1}).json()
        assert receipt["accepted_at"]
        assert request("PUT", path + "/receipt", {"version": 1}).json() == receipt
        assert request("PUT", path + "/assignment", {
            "version": 1, "assignee": "Бригада 2", "due_at": future,
        }).json() == {"assignment_version": 2}
        assert request("PUT", path + "/receipt", {"version": 1}).status_code == 409
        assert request("PUT", path + "/receipt", {"version": 2}, run_id=other).status_code == 404
        with connect(settings) as conn:
            conn.execute("UPDATE dispatch_tasks SET due_at=%s WHERE run_id=%s AND id=%s",
                         (now - timedelta(seconds=1), run, tasks[0]))
        rows = {row["district"]: row for row in request("GET", "dashboard").json()["dispatch_queue"]}
        assert set(rows) == {"Центр", "Север", "Юг"}
        task = rows["Центр"]["task"]
        assert task["assignee"] == "Бригада 2"
        assert task["accepted_at"] is None and task["overdue"]
        assert task["response_seconds"] >= 0
        assert all(rows[district]["task"]["assignee"] is None for district in ("Север", "Юг"))


def test_dispatch_crew_result_followup_and_report_archive(settings):
    run = "test-" + uuid4().hex
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", ())
    due = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with TestClient(create_app(settings)) as client:
        def call(method, path, payload=None, headers=None, expected=200):
            response = client.request(method, "/api/v1/" + path, params={"run": run}, json=payload, headers=headers)
            assert response.status_code == expected, response.text
            return response.json()

        report = call("POST", "dispatch/reports", {"id": str(uuid4()), "district": "Север",
            "address": "Лесная, 10", "kind": "drift", "description": "Вал у дома", "channel": "phone", "reference": "42"})
        inspection = call("POST", "dispatch/tasks", {
            "location_key": report["location_key"], "kind": "drift", "work_kind": "inspect",
        })["id"]
        second_report = call("POST", "dispatch/reports", {"id": str(uuid4()), "district": "Север",
            "address": "Лесная, 10А", "kind": "drift", "description": "Тот же вал", "channel": "phone", "reference": "43"})
        assert call("PUT", f"dispatch/reports/{second_report['id']}/task", {"task_id": inspection}) == {"task_id": inspection}
        dispatcher = "dispatch/tasks/" + inspection
        crew = "crew/tasks/" + inspection
        call("PUT", dispatcher + "/assignment", {"version": 0, "assignee": "Бригада 1", "due_at": due})
        token = call("POST", dispatcher + "/link")["token"]
        first_access = {"Authorization": "Bearer " + token}
        call("PUT", dispatcher + "/assignment", {"version": 1, "assignee": "Бригада 2", "due_at": due})
        call("GET", crew, headers=first_access, expected=404)
        token = call("POST", dispatcher + "/link")["token"]
        access = {"Authorization": "Bearer " + token}
        assert call("GET", crew, headers=access)["stage"] == "sent"
        assert token not in json.dumps(call("GET", "dashboard"))
        call("PUT", crew, {"version": 2, "action": "start"}, headers=access, expected=409)
        call("PUT", crew, {"version": 2, "action": "accept"}, headers=access)
        call("PUT", crew, {"version": 2, "action": "start"}, headers=access)
        image = BytesIO()
        Image.new("RGB", (16, 16), "white").save(image, format="PNG")
        files = [("photos", ("result.png", image.getvalue(), "image/png"))] * 2
        for _ in range(2):
            photo_result = client.post(f"/api/v1/{crew}/result", headers=access,
                                       data={"version": 3, "note": "Нужна уборка"}, files=files)
            assert photo_result.status_code == 200, photo_result.text
        row, = call("GET", "dashboard")["dispatch_queue"]
        ids = row["task"]["result_photo_ids"]
        assert len(ids) == 2 and len(set(ids)) == 2
        photo_url = "/api/v1/dispatch/result-photos/" + ids[0]
        assert client.get(photo_url, params={"run": run}).headers["content-type"] == "image/jpeg"
        assert client.get(photo_url, params={"run": "other"}).status_code == 404
        call("PUT", crew, {"version": 4, "action": "close", "note": "Закрыто"}, headers=access, expected=403)
        call("PUT", dispatcher + "/progress", {"version": 4, "action": "return", "note": "Укажите длину участка"})
        assert call("GET", crew, headers=access)["return_note"] == "Укажите длину участка"
        call("PUT", crew, {"version": 5, "action": "start"}, headers=access)
        call("PUT", crew, {"version": 6, "action": "finish", "note": "Вал 20 метров"}, headers=access)
        followup = {"version": 7, "action": "followup", "note": "Убрать снежный вал"}
        call("PUT", dispatcher + "/progress", followup)
        call("PUT", dispatcher + "/progress", followup, expected=404)
        row, = call("GET", "dashboard")["dispatch_queue"]
        assert {item["id"] for item in row["reports"] if item["linked"]} == {report["id"], second_report["id"]}
        cleanup = row["task"]
        assert cleanup["work_kind"] == "clear" and cleanup["instructions"] == "Убрать снежный вал"
        cleanup_path = "dispatch/tasks/" + cleanup["id"]
        actions = call("GET", cleanup_path + "/history")
        assert any(item["action"] == "finish" and item["note"] == "Вал 20 метров" for item in actions)
        call("PUT", cleanup_path + "/assignment", {"version": 0, "assignee": "Бригада 2", "due_at": due})
        for action, version, note in (("accept", 1, ""), ("start", 1, ""), ("finish", 2, "Убрано"), ("close", 3, "Вал убран")):
            call("PUT", cleanup_path + "/progress", {"version": version, "action": action, "note": note})
        assert not call("GET", "dashboard")["dispatch_queue"]
        archived = call("GET", "dispatch/archive")
        assert len(archived) == 2
        assert archived[0]["id"] == cleanup["id"]
        assert {item["resolution"] for item in archived[0]["reports"]} == {"Вал убран"}


def test_comparison_feedback_and_after_work_are_scoped(settings):
    from monitoring.operations import record_observation

    run = "test-" + uuid4().hex
    other = "test-" + uuid4().hex
    media = Media(settings)
    now = datetime.now(timezone.utc)
    first, _, heavy, _ = [item["scene"] for item in model_fixture_rows()]
    cameras = [dict(id=key, name=key, area="Тест", road=True, road_priority=1,
                    road_priority_reason="Пешеходный переход") for key in ("a", "b")]
    with connect(settings) as conn:
        prepare_run(conn, run, "replay", cameras)
        prepare_run(conn, other, "replay", cameras)
        def save(camera, seconds, data, scene=None):
            frame_id = store_frame(conn, run, camera, (data, 40, 25, uuid4().hex, None, now + timedelta(seconds=seconds)), media)
            if scene:
                frame = conn.execute("SELECT * FROM frames WHERE id=%s", (frame_id,)).fetchone()
                record_observation(conn, settings, frame, scene)
            return str(frame_id)
        baseline = save("a", -37, b"clear", first)
        wrong_camera = save("b", -35, b"different-view", first)
        save("a", -28, b"snow-first", heavy)
        operating = save("a", -25, b"snow-second", heavy)
        after = save("a", -5, b"cleared")
        event = conn.execute("SELECT id FROM events WHERE run_id=%s", (run,)).fetchone()
    endpoint = f'/api/v1/events/{event["id"]}'
    with TestClient(create_app(settings)) as client:
        query = {"run": run}
        compare = client.get(endpoint + "/review", params=query).json()
        assert compare["before"]["id"] == baseline and compare["current"]["id"] == operating
        assert not compare["candidate"]
        dashboard = client.get("/api/v1/dashboard", params=query).json()
        assert len(dashboard["inspection_queue"]) == 1
        feedback = {"verdict": "confirmed"}
        first_result = client.put(endpoint + "/feedback", params=query, json=feedback)
        retry = client.put(endpoint + "/feedback", params=query, json=feedback)
        assert first_result.status_code == 200 and first_result.json() == retry.json()
        assert client.put(endpoint + "/feedback", params={"run": other}, json=feedback).status_code == 404
        assert client.get("/api/v1/dashboard", params=query).json()["feedback_counts"] == {"confirmed": 1}
        with connect(settings) as conn:
            conn.execute("UPDATE events SET state='en_route' WHERE id=%s", (event["id"],))
        assert client.get(endpoint + "/review", params=query).json()["candidate"]["id"] == after
        assert client.put(endpoint + "/after-work", params=query, json={"frame_id": wrong_camera}).status_code == 409
        assert client.put(endpoint + "/after-work", params=query, json={"frame_id": after}).json()["id"] == after
        assert client.get(endpoint + "/review", params=query).json()["after"]["id"] == after


def test_bus_dashboard_preview_collection_and_demo_isolation(settings, tmp_path):
    from dataclasses import replace

    from monitoring.bus import measure_passages
    from monitoring.bus_demo import START, demo_inputs, demo_sections
    from monitoring.bus_worker import collect, dashboard_rnis, load_sections, map_rnis

    road = json.loads((Path(__file__).resolve().parents[1] / "monitoring/snow_segments.json").read_text())[1]
    path = tmp_path / "sections.json"
    config = {**road, "entry_fraction": .15, "exit_fraction": .85,
              "location_key": f"road:{road['id']}", "publish": ["night"]}
    path.write_text(json.dumps([config]))
    sections = load_sections(path)
    geometry = [section.geometry() for section in sections]
    now = START + timedelta(minutes=11)
    preview = replace(settings, mode="preview", bus_rnis_enabled=True, bus_sections_path=path)
    reference, scenarios, _ = demo_inputs(demo_sections())
    positions = scenarios["two-buses"]["positions"]
    with connect(preview) as conn:
        prepare_run(conn, "preview", "preview", ())
        with conn.cursor() as cursor:
            cursor.executemany(
                """INSERT INTO bus_passages VALUES ('preview',%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                [(item.section, item.direction, item.vehicle, item.entered_at, item.ended_at, item.received_at)
                 for item in measure_passages(geometry, reference)],
            )
        collect(conn, "preview", sections, geometry, now, positions, "ready")
        collect(conn, "preview", sections, geometry, now, positions, "ready")
        assert conn.execute("SELECT count(*) AS n FROM bus_hints WHERE run_id='preview'").fetchone()["n"] == 1
        assert conn.execute(
            "SELECT count(*) AS n FROM bus_positions WHERE run_id='preview'",
        ).fetchone()["n"] == len(positions)
        actual = dashboard_rnis(conn, preview, "preview", now)["rows"][0]
        assert actual["reason"] == "buses" and actual["coordinates"] == sections[0].coordinates
        assert dashboard_rnis(conn, preview, "preview", now + timedelta(hours=4)) is None
        layer = map_rnis(conn, preview, "preview", now)
        assert layer["state"] == "ready"
        assert [point["id"] for point in layer["positions"]] == ["bus-b"]
        assert map_rnis(conn, preview, "preview", now + timedelta(minutes=2))["positions"] == []
        collect(conn, "preview", sections, geometry, now + timedelta(minutes=1), [], "unavailable")
        assert conn.execute("SELECT closed_at FROM bus_hints WHERE run_id='preview'").fetchone()["closed_at"]

    with TestClient(create_app(preview)) as client:
        current = client.get("/api/v1/dashboard").json()["bus_observations"]
        assert current is None or current["state"] == "unavailable"
        demo = client.get("/api/v1/dashboard?run=demo").json()["bus_observations"]
        assert demo["source"] == "simulation"
        assert demo["scenarios"][0]["rows"][2]["reason"] == "buses"
        overlay = client.get("/api/v1/dashboard?run=demo").json()["bus_map"]
        assert overlay["source"] == "simulation" and len(overlay["positions"]) == 4

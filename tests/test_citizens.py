import asyncio
from dataclasses import replace
from io import BytesIO
import json
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from monitoring import api, citizen_import
from monitoring.citizen_bot import Bot, Draft, compact_id
from monitoring.citizens import MAX_PHOTO_BYTES, MAX_UPLOAD_BYTES
from monitoring.config import Settings
from monitoring.db import connect
from monitoring.districts import load_districts, locate_district


REPORTS = "/api/v1/citizens/reports"


def png():
    file = BytesIO()
    Image.new("RGB", (20, 10), "white").save(file, format="PNG")
    return file.getvalue()


def post(client, photo=None, **fields):
    data = {"description": " Сугроб у перехода ", "latitude": 53.02,
            "longitude": 158.65, "tags": ["снег", "снег", "сугроб"], **fields}
    return client.post(REPORTS, data=data, files={"photo": ("photo.png", png() if photo is None else photo)})


def test_rejects_invalid_citizen_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "migrate", lambda settings: None)
    monkeypatch.setattr(api.citizens, "refine_report_districts", lambda settings, districts: None)
    settings = Settings(database="unused", mode="demo", media_dir=tmp_path)
    with TestClient(api.create_app(settings)) as client:
        assert post(client, description="   ").status_code == 422
        assert post(client, latitude=91).status_code == 422
        assert post(client, tags=["гололёд"]).status_code == 422
        assert post(client, photo=b"not an image").status_code == 415
        assert post(client, photo=b"x" * (MAX_PHOTO_BYTES + 1)).status_code == 413
        assert post(client, photo=b"x" * MAX_UPLOAD_BYTES).status_code == 413
        assert client.get(REPORTS, params={"district": "center", "start": "2026-11-22T00:00:00",
                                           "end": "2026-11-23T00:00:00Z"}).status_code == 422
        assert client.get(REPORTS, params={"district": "center", "start": "2026-11-23T00:00:00Z",
                                           "end": "2026-11-22T00:00:00Z"}).status_code == 422


def test_persists_photo_and_filters_reports(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    district_id = f"north-{uuid4().hex}"
    boundaries = tmp_path / "districts.geojson"
    boundaries.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"id": district_id, "name": "Северный район"},
        "geometry": {"type": "Polygon",
                     "coordinates": [[[158.65, 53], [158.7, 53], [158.7, 53.1], [158.65, 53.1], [158.65, 53]]]},
    }]}), encoding="utf-8")
    settings = Settings(database=database, mode="replay", media_dir=tmp_path, citizen_districts_path=boundaries)
    with TestClient(api.create_app(settings)) as client:
        assert client.get("/api/v1/citizens/districts").json() == [
            {"id": district_id, "name": "Северный район"}, {"id": "unassigned", "name": None},
        ]
        response = post(client)
        assert response.status_code == 201, response.text
        first = response.json()
        assert first["description"] == "Сугроб у перехода"
        assert (first["latitude"], first["longitude"]) == (53.02, 158.65)
        assert first["tags"] == ["снег", "сугроб"]
        assert first["suggested_tags"] == ["уборка_снега"]
        assert first["district_id"] == district_id  # The point is on the boundary.
        assert first["district"] == "Северный район"

        second = post(client, longitude=158.67, tags=[]).json()
        outside = post(client).json()
        other = post(client, longitude=158.5, district=district_id).json()
        assert (other["district_id"], other["district"]) == ("unassigned", None)
        with connect(settings) as conn:
            for report, created_at in [(first, "00:00"), (second, "00:30"), (outside, "01:00"), (other, "00:30")]:
                conn.execute("UPDATE citizen_reports SET created_at=%s WHERE id=%s",
                             (f"2026-11-22T{created_at}:00+12:00", report["id"]))

        params = {"district": district_id, "start": "2026-11-22T00:00:00+12:00",
                  "end": "2026-11-22T01:00:00+12:00", "limit": 1}
        page = client.get(REPORTS, params=params).json()
        assert [item["id"] for item in page["items"]] == [second["id"]]
        assert page["next_offset"] == 1
        page = client.get(REPORTS, params={**params, "offset": page["next_offset"]}).json()
        assert [item["id"] for item in page["items"]] == [first["id"]]
        assert page["next_offset"] is None

        latest = client.get(REPORTS, params={"limit": 1, "start": "2026-11-22T00:00:00+12:00"}).json()
        assert [item["id"] for item in latest["items"]] == [outside["id"]]
        assert latest["next_offset"] == 1
        selected = client.get(REPORTS, params={"id": other["id"]}).json()
        assert [item["id"] for item in selected["items"]] == [other["id"]]

        with connect(settings) as conn:
            conn.execute("UPDATE citizen_reports SET district_id='unassigned', district=NULL WHERE id=%s",
                         (first["id"],))

    with TestClient(api.create_app(settings)) as client:
        assert client.get(REPORTS, params={"id": first["id"]}).json()["items"][0]["district_id"] == district_id
        assert client.get(REPORTS, params={"id": other["id"]}).json()["items"][0]["district_id"] == "unassigned"
        photo = client.get(first["photo_url"])
        assert photo.status_code == 200
        assert photo.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(photo.content)) as image:
            assert image.size == (20, 10)

    with TestClient(api.create_app(replace(settings, mode="demo"))) as client:
        assert client.get(first["photo_url"]).status_code == 404
        assert client.get(REPORTS, params=params).json() == {"items": [], "next_offset": None}


def test_citizen_clients_share_storage_and_telegram_history_is_private(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    settings = Settings(database=database, mode="demo", media_dir=tmp_path, citizen_bot_api_key="test-key")
    actor = str(uuid4().int % 10**18 + 1)
    headers = {"X-Citizen-Bot-Key": "test-key", "X-Telegram-User": actor}
    stranger = {**headers, "X-Telegram-User": str(int(actor) + 1)}
    request_id = str(uuid4())
    body = {"description": "Гололёд у дома", "latitude": -32.001, "longitude": 180,
            "request_id": request_id, "address": "Тестовый адрес"}

    with TestClient(api.create_app(settings)) as client:
        assert client.get("/citizens/").status_code == 200
        assert client.get("/citizens/app.js").status_code == 200
        assert client.get("/api/v1/citizens/form-config").json() == {"requires_token": False}

        def send(auth):
            return client.post(REPORTS, headers=auth, data=body, files={"photo": ("photo.png", png())})

        assert send({"X-Telegram-User": actor}).status_code == 401
        saved = send(headers)
        assert saved.status_code == 201, saved.text
        report = saved.json()
        assert report["address"] == body["address"]
        assert report["suggested_tags"] == ["гололёд"]
        assert send(headers).json()["id"] == report["id"]
        assert send({}).json()["id"] != report["id"]

        assert client.get(REPORTS + "/mine", headers=headers).json() == [report]
        assert client.get(REPORTS + "/mine", headers=stranger).json() == []
        assert client.get(REPORTS + "/mine", headers=headers, params={"before": report["id"]}).json() == []
        assert client.get(REPORTS + "/mine").status_code == 403
        assert client.get(f'{REPORTS}/{report["id"]}', headers=headers).json() == report
        assert client.get(f'{REPORTS}/{report["id"]}', headers=stranger).status_code == 404
        assert client.get(report["photo_url"], headers=stranger).status_code == 404
        assert client.get(report["photo_url"]).status_code == 200
        assert client.get(report["photo_url"], headers=headers).headers["content-type"] == "image/jpeg"
        zones = client.get(REPORTS + "/zones", params={"minimum": 2}).json()
        assert any(zone["latitude_cell"] == -3201 and zone["longitude_cell"] == 17999
                   and zone["count"] >= 2 for zone in zones)



def test_legacy_import_is_atomic_and_idempotent(tmp_path, monkeypatch):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    settings = Settings(database=database, mode="preview", media_dir=tmp_path)
    source = tmp_path / "old.sqlite3"
    message_id = uuid4().int % 10**18
    with sqlite3.connect(source) as conn:
        conn.executescript("""
            CREATE TABLE reports (id, user_id, message_id, photo_id, description, latitude, longitude, created_at);
            CREATE TABLE web_photos (report_id, content);
            CREATE TABLE web_addresses (report_id, address);
        """)
        conn.executemany("INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            (1, 0, message_id, "web", "Гололёд", 53.02, 158.65, "2025-01-01 10:00:00"),
            (2, message_id, 1, "telegram-photo", "Сугроб", 53.02, 158.65, "2025-01-01 10:05:00"),
        ])
        conn.execute("INSERT INTO web_photos VALUES (1, ?)", (png(),))
        conn.execute("INSERT INTO web_addresses VALUES (1, 'Тестовый адрес')")

    with pytest.raises(RuntimeError, match="№2"):
        citizen_import.import_reports(settings, source, "")
    monkeypatch.setattr(citizen_import, "telegram_photo",
                        lambda *_: citizen_import.photo_jpeg(BytesIO(png())))
    assert citizen_import.import_reports(settings, source, "test-token") == (2, 0)
    assert citizen_import.import_reports(settings, source, "test-token") == (0, 2)
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT created_at, address, photo_jpeg FROM citizen_reports WHERE mode=%s AND submitted_by=%s",
            (settings.mode, f"telegram:{message_id}"),
        ).fetchone()
    assert row["created_at"].isoformat() == "2025-01-01T10:05:00+00:00"
    assert row["photo_jpeg"].startswith(b"\xff\xd8")


def test_bot_submits_telegram_photo_through_api_and_reads_it_back():
    report_id = str(uuid4())
    report = {"id": report_id, "description": "Гололёд", "latitude": 53.02, "longitude": 158.65,
              "created_at": "2025-01-01T10:00:00Z", "tags": [], "suggested_tags": ["гололёд"]}
    image = png()

    async def transport(request):
        assert request.headers["X-Telegram-User"] == "123"
        assert request.headers["X-Citizen-Bot-Key"] == "test-key"
        if request.method == "POST":
            assert request.url.path == REPORTS
            assert request.headers["Content-Type"].startswith("multipart/form-data")
            assert image in await request.aread()
            return httpx.Response(201, json=report)
        if request.url.path.endswith("/photo"):
            return httpx.Response(200, content=image)
        assert request.url.path == f"{REPORTS}/{report_id}"
        return httpx.Response(200, json=report)

    async def exercise():
        file = SimpleNamespace(file_size=len(image), download_as_bytearray=AsyncMock(return_value=bytearray(image)))
        telegram = SimpleNamespace(get_file=AsyncMock(return_value=file))
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=123), text="", message_id=10, photo=(),
            location=SimpleNamespace(latitude=53.02, longitude=158.65),
            get_bot=lambda: telegram, reply_text=AsyncMock(), reply_photo=AsyncMock(),
        )
        async with httpx.AsyncClient(base_url="http://api", transport=httpx.MockTransport(transport)) as client:
            bot = Bot(client, "test-key")
            bot.drafts[123] = Draft(photo_id="telegram-photo", description=report["description"])
            update = SimpleNamespace(message=message)
            await bot.handle(update, None)
            telegram.get_file.assert_awaited_once_with("telegram-photo")
            assert 123 not in bot.drafts
            assert report_id in message.reply_text.await_args.args[0]
            message.text = "/r_" + compact_id(report_id)
            await bot.handle(update, None)
            assert message.reply_photo.await_args.args[0] == image

    asyncio.run(exercise())



@pytest.mark.parametrize(("latitude", "longitude", "expected"), [
    (53.0196, 158.6478, "pk-center"),
    (53.05, 158.616667, "pk-seroglazka"),
    (53.078, 158.645, "pk-severo-vostok"),
    (52.954364, 158.673296, "pk-zavoyko"),
    (53.10, 158.70, "petropavlovsk-kamchatsky"),
    (53.1873, 158.3804, "yelizovsky"),
    (52.9304, 158.4047, "vilyuchinsky"),
    (59.0838, 159.9511, "palana"),
    (55.197, 165.984, "aleutsky"),
    (55.75, 37.62, None),
])
def test_bundled_kamchatka_districts(latitude, longitude, expected):
    districts = load_districts(None)
    assert sum(district.parent_id is None for district in districts) == 14
    assert sum(district.parent_id == "petropavlovsk-kamchatsky" for district in districts) == 37
    district = locate_district(districts, latitude, longitude)
    assert (district.id if district else None) == expected



def test_city_filter_includes_microdistricts_and_refines_existing_reports(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    settings = Settings(database=database, mode="demo", media_dir=tmp_path)
    city = "petropavlovsk-kamchatsky"
    with TestClient(api.create_app(settings)) as client:
        options = client.get("/api/v1/citizens/districts").json()
        assert {"id": "pk-seroglazka", "name": "Сероглазка", "parent_id": city} in options
        report = post(client, latitude=53.05, longitude=158.616667).json()
        assert report["district_id"] == "pk-seroglazka"
        with connect(settings) as conn:
            conn.execute("UPDATE citizen_reports SET district_id=%s, district=NULL WHERE id=%s",
                         (city, report["id"]))

    with TestClient(api.create_app(settings)) as client:
        for district in (city, "pk-seroglazka"):
            result = client.get(REPORTS, params={"id": report["id"], "district": district}).json()
            assert result["items"] == [report]
        assert not client.get(REPORTS, params={"id": report["id"], "district": "pk-center"}).json()["items"]


def test_citizen_report_without_photo_reaches_dispatch_once(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    settings = Settings(database=database, mode="preview", media_dir=tmp_path)
    request_id = str(uuid4())
    fields = {"request_id": request_id, "description": "Нужно убрать снег у перекрёстка",
              "latitude": 53.02, "longitude": 158.65, "tags": "снег"}
    with TestClient(api.create_app(settings)) as client:
        initial = client.post(REPORTS, data=fields)
        assert initial.status_code == 201, initial.text
        report = initial.json()
        assert report["photo_url"] is None
        assert client.post(REPORTS, data=fields).json()["id"] == report["id"]
        rows = client.get("/api/v1/dashboard").json()["dispatch_queue"]
        [row] = [row for row in rows if row["location_key"] == f"citizen:{report['id']}"]
        [source] = row["reports"]
        assert source["citizen_report_id"] == report["id"]
        assert source["map_section"]["coordinates"] == [[158.65, 53.02]]
        assert row["kind"] == "coverage"
        assert client.get(f"{REPORTS}/{report['id']}/photo").status_code == 404
    with connect(settings) as conn:
        conn.execute("DELETE FROM dispatch_reports WHERE citizen_report_id=%s", (report["id"],))
        conn.execute("DELETE FROM citizen_reports WHERE id=%s", (report["id"],))

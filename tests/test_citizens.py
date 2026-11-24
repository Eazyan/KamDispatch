import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from psycopg.types.json import Jsonb

from monitoring import api, citizen_import
from monitoring.citizen_bot import BOT_DISPATCH_PATH, Bot, Draft, compact_id, deliver_task_notices, deliver_warnings
from monitoring.citizen_max_bot import MaxBot
from monitoring.citizens import MAX_PHOTO_BYTES, MAX_UPLOAD_BYTES, save_report
from monitoring.config import ROOT, Settings
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
    monkeypatch.setattr(api, "migrate", lambda settings, **_: None)
    monkeypatch.setattr(api.citizens, "refine_report_districts", lambda settings, districts, **_: None)
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


def test_citizen_clients_share_storage_and_bot_history_is_private(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    settings = Settings(database=database, mode="demo", media_dir=tmp_path, citizen_bot_api_key="test-key")
    actor = str(uuid4().int % 10**18 + 1)
    headers = {"X-Citizen-Bot-Key": "test-key", "X-Telegram-User": actor}
    max_headers = {"X-Citizen-Bot-Key": "test-key", "X-Max-User": actor}
    stranger = {**headers, "X-Telegram-User": str(int(actor) + 1)}
    request_id = str(uuid4())
    body = {"description": "Гололёд у дома", "latitude": -32.001, "longitude": 180,
            "request_id": request_id, "address": "Тестовый адрес"}

    with TestClient(api.create_app(settings)) as client:
        assert client.get("/citizens/").status_code == 200
        assert client.get("/citizens/app.js").status_code == 200
        assert client.get("/api/v1/citizens/form-config").json() == {"requires_token": False}

        def send(auth, image=None):
            return client.post(REPORTS, headers=auth, data=body, files={
                "photo": ("photo", png() if image is None else image),
            })

        assert send({"X-Telegram-User": actor}).status_code == 401
        assert send({"X-Max-User": actor}).status_code == 401
        assert send({**max_headers, "X-Telegram-User": actor}).status_code == 401
        saved = send(headers)
        assert saved.status_code == 201, saved.text
        report = saved.json()
        assert report["address"] == body["address"]
        assert report["suggested_tags"] == ["гололёд"]
        assert report["report_number"] == 1
        assert send(headers).json() == report
        assert send({}).json()["id"] != report["id"]
        webp = BytesIO()
        Image.new("RGB", (20, 10), "white").save(webp, format="WEBP")
        assert send(headers, webp.getvalue()).status_code == 415
        assert send({}, webp.getvalue()).status_code == 415
        saved_max = send(max_headers, webp.getvalue())
        assert saved_max.status_code == 201, saved_max.text
        max_report = saved_max.json()
        max_photo = client.get(max_report["photo_url"], headers=max_headers)
        assert max_photo.status_code == 200
        assert max_photo.headers["content-type"] == "image/jpeg"
        assert max_photo.content.startswith(b"\xff\xd8")
        assert max_report["id"] != report["id"]
        assert max_report["report_number"] == 1
        assert send(max_headers).json()["id"] == max_report["id"]
        assert client.get(REPORTS + "/mine", headers=max_headers).json() == [max_report]
        assert client.get(report["photo_url"], headers=max_headers).status_code == 404
        assert client.get(max_report["photo_url"], headers=headers).status_code == 404

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

        assert client.get(f"{REPORTS}/1", headers=headers).json() == report
        assert client.get(f"{REPORTS}/1", headers=max_headers).json() == max_report
        assert client.get(f"{REPORTS}/1", headers=stranger).status_code == 404
        assert client.get(f"{REPORTS}/9223372036854775808", headers=headers).status_code == 422

        def send_new(owner, retry_key):
            response = client.post(REPORTS, headers=owner, data={**body, "request_id": retry_key},
                                   files={"photo": ("photo", png())})
            assert response.status_code == 201, response.text
            return response.json()

        retry_key = str(uuid4())
        with ThreadPoolExecutor(max_workers=4) as pool:
            repeated, repeated_again, another, latest = pool.map(
                lambda key: send_new(headers, key), (retry_key, retry_key, str(uuid4()), str(uuid4())),
            )
        assert repeated == repeated_again
        assert sorted(item["report_number"] for item in (repeated, another, latest)) == [2, 3, 4]

        browser = {"X-Citizen-Browser-Id": str(uuid4())}
        second_browser = {"X-Citizen-Browser-Id": str(uuid4())}
        assert send_new(browser, str(uuid4()))["report_number"] == 1
        assert send_new(browser, str(uuid4()))["report_number"] == 2
        assert send_new(second_browser, str(uuid4()))["report_number"] == 1



def test_number_migration_preserves_existing_rows_and_continues_numbering(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    settings = Settings(database=database, mode="demo", media_dir=tmp_path)
    migrations = ROOT / "monitoring" / "migrations"
    early, late, other_author, other_mode = sorted(uuid4() for _ in range(4))
    image = png()
    with connect(settings) as conn:
        conn.execute((migrations / "011_citizen_reports.sql").read_text()
                     .replace("CREATE TABLE", "CREATE TEMP TABLE", 1))
        conn.execute((migrations / "012_citizen_clients.sql").read_text())
        for identifier, mode, author, date in (
            (late, "demo", "telegram:1", "2026-11-22"),
            (early, "demo", "telegram:1", "2026-11-22"),
            (other_author, "demo", "telegram:2", "2026-11-21"),
            (other_mode, "preview", "telegram:1", "2026-11-21"),
        ):
            conn.execute(
                """INSERT INTO citizen_reports (id, mode, submitted_by, created_at, district_id,
                                                description, latitude, longitude, photo_jpeg)
                   VALUES (%s, %s, %s, %s, 'unassigned', 'Снег', 0, 0, %s)""",
                (identifier, mode, author, date, image),
            )
        conn.execute((migrations / "015_citizen_report_numbers.sql").read_text())
        rows = conn.execute("SELECT id, report_number, photo_jpeg FROM citizen_reports").fetchall()
        assert {row["id"]: row["report_number"] for row in rows} == {
            early: 1, late: 2, other_author: 1, other_mode: 1,
        }
        assert all(row["photo_jpeg"] == image for row in rows)
        new = save_report(conn, mode="demo", actor="telegram:1", districts=[], image=image,
                          description="Снег", latitude=0, longitude=0)
        assert new["report_number"] == 3


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
    report = {"id": report_id, "report_number": 1, "description": "Гололёд", "latitude": 53.02, "longitude": 158.65,
              "created_at": "2025-01-01T14:30:00Z", "tags": [], "suggested_tags": ["гололёд"]}
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
        assert request.url.path in {f"{REPORTS}/{report_id}", f"{REPORTS}/1"}
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
            assert "Обращение №1 принято. Открыть · /r_1" in message.reply_text.await_args.args[0]
            for command in ("/r_1", "/report 1", "/r_" + compact_id(report_id)):
                message.text = command
                await bot.handle(update, None)
                assert message.reply_photo.await_args.args[0] == image
                assert "02.01.2025 02:30 (Камчатка)" in message.reply_photo.await_args.kwargs["caption"]

    asyncio.run(exercise())



def test_max_bot_submits_photo_and_returns_personal_history():
    report = {"id": str(uuid4()), "report_number": 1, "description": "Гололёд", "latitude": 53.02, "longitude": 158.65,
              "created_at": "2025-01-01T14:30:00Z", "tags": [], "suggested_tags": ["гололёд"]}
    image = png()
    outgoing = []
    submissions = 0
    role = "contractor"
    task_id = str(uuid4())

    async def transport(request):
        nonlocal submissions, role
        if request.url.host == "i.oneme.ru":
            assert "Authorization" not in request.headers
            return httpx.Response(200, content=image)
        if request.url.host == "iu.oneme.ru":
            assert request.headers["Authorization"] == "max-token"
            assert b'name="data"' in await request.aread()
            return httpx.Response(200, json={"photos": {"id": {"token": "uploaded-image"}}})
        if request.url.host == "platform-api2.max.ru":
            assert request.headers["Authorization"] == "max-token"
            if request.url.path == "/uploads":
                assert request.url.params["type"] == "image"
                return httpx.Response(200, json={"url": "https://iu.oneme.ru/uploadImage"})
            if request.url.path == "/answers":
                assert request.url.params["callback_id"] == "switch"
                outgoing.append(json.loads(await request.aread())["message"])
                return httpx.Response(200, json={"success": True})
            assert request.url.path == "/messages"
            assert request.url.params["user_id"] == "123"
            outgoing.append(json.loads(await request.aread()))
            return httpx.Response(200, json={})
        assert request.url.host == "api"
        assert request.headers["X-Max-User"] == "123"
        assert request.headers["X-Citizen-Bot-Key"] == "test-key"
        if request.url.path == BOT_DISPATCH_PATH + "/role":
            if request.method == "PUT":
                role = json.loads(await request.aread())["role"]
            return httpx.Response(200, json={"role": role, "assigned_role": "contractor"})
        if request.url.path == f"{BOT_DISPATCH_PATH}/tasks/{task_id}/result":
            assert request.method == "POST"
            payload = await request.aread()
            assert image in payload and "Снег убран".encode() in payload
            return httpx.Response(200, json={"stage": "done"})
        if request.method == "POST":
            submissions += 1
            if submissions == 1:
                return httpx.Response(413, json={"detail": "Фото должно быть не больше 16 мегапикселей"})
            payload = await request.aread()
            assert image in payload and "Гололёд".encode() in payload
            assert b'name="request_id"' in payload
            assert b'name="latitude"\r\n\r\n53.0749' in payload
            assert b'name="longitude"\r\n\r\n158.6497' in payload
            assert "улица Ларина, 22, Петропавловск-Камчатский".encode() in payload
            return httpx.Response(201, json=report)
        if request.url.path.endswith("/photo"):
            return httpx.Response(200, content=image)
        assert request.url.path == REPORTS + "/mine"
        return httpx.Response(200, json=[report])

    async def exercise():
        mock = httpx.MockTransport(transport)
        async with (
            httpx.AsyncClient(base_url="http://api", transport=mock) as citizen,
            httpx.AsyncClient(base_url="https://platform-api2.max.ru", transport=mock,
                              headers={"Authorization": "max-token"}) as platform,
            httpx.AsyncClient(transport=mock) as media,
        ):
            bot = MaxBot(citizen, "test-key")
            sender = {"user_id": 123, "is_bot": False}

            async def send(body, chat_type="dialog"):
                await bot.handle_update({
                    "update_type": "message_created",
                    "message": {"sender": sender, "recipient": {"chat_type": chat_type}, "body": body},
                }, platform, media)

            await bot.handle_update({"update_type": "bot_started", "user": sender}, platform, media)
            assert outgoing[-1]["attachments"][0]["payload"]["buttons"][0][0]["text"] == "Новое обращение"
            await send({"text": "Моя роль"})
            for expected in ("resident", "contractor"):
                button = outgoing[-1]["attachments"][0]["payload"]["buttons"][-1][0]
                assert button == {"type": "callback", "text": "Сменить роль", "payload": f"/role_{expected}"}
                await bot.handle_update({
                    "update_type": "message_callback",
                    "callback": {"user": sender, "payload": button["payload"], "callback_id": "switch"},
                    "message": {"recipient": {"chat_type": "dialog"}},
                }, platform, media)
                assert role == expected
            await send({"text": f"/finish_{compact_id(task_id)}_3"})
            assert "Пришлите фото" in outgoing[-1]["text"]
            await send({"text": "Снег убран"})
            assert "в подписи" in outgoing[-1]["text"]
            await send({"text": "Снег убран", "attachments": [
                {"type": "image", "payload": {"url": "https://i.oneme.ru/result"}},
            ]})
            assert "Результат и фото переданы" in outgoing[-1]["text"]
            assert not bot.task_notes
            await send({"text": "/new"}, chat_type="chat")
            assert not bot.drafts
            await send({"mid": "mid.photo", "text": report["description"], "attachments": [
                {"type": "image", "payload": {"url": "https://i.oneme.ru/image"}},
            ]})
            assert outgoing[-1]["attachments"][0]["payload"]["buttons"][0][0]["type"] == "request_geo_location"
            await send({"mid": "mid.location", "attachments": [
                {"type": "location", "latitude": 53.02, "longitude": 158.65},
            ]})
            assert "16 мегапикселей" in outgoing[-1]["text"]
            assert not bot.drafts[123].photo_id
            assert bot.drafts[123].description == report["description"]
            await send({"mid": "mid.replacement", "attachments": [
                {"type": "image", "payload": {"url": "https://i.oneme.ru/replacement"}},
            ]})
            assert "улицу и номер дома" in outgoing[-1]["text"]
            await send({"mid": "mid.incomplete-address", "text": "улица Ларина"})
            assert "Укажите улицу и номер дома" in outgoing[-1]["text"]
            await send({"mid": "mid.unknown-address", "text": "улица Ларина, 999"})
            assert "Дом по этому адресу не найден" in outgoing[-1]["text"]
            assert bot.drafts[123].description == report["description"]
            await send({"mid": "mid.misspelled-address", "text": "ул. Ларна, д. 22"})
            assert "Проверьте адрес: улица Ларина, 22, Петропавловск-Камчатский" in outgoing[-1]["text"]
            button = outgoing[-1]["attachments"][0]["payload"]["buttons"][0][0]
            assert button == {"type": "message", "text": "Подтвердить адрес"}
            assert submissions == 1
            await send({"mid": "mid.confirmed-address", "text": button["text"]})
            assert not bot.drafts
            assert "Обращение №1 принято. Открыть · /r_1" in outgoing[-1]["text"]
            await send({"mid": "mid.history", "text": "Мои обращения"})
            assert report["description"] in outgoing[-1]["text"]
            assert "02.01.2025 02:30 (Камчатка)" in outgoing[-1]["text"]
            assert {"type": "image", "payload": {"token": "uploaded-image"}} in outgoing[-1]["attachments"]

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

def test_messenger_roles_share_dispatch_workflow_and_warning_receipts(tmp_path):
    database = os.getenv("TEST_DATABASE_URL")
    if not database:
        pytest.skip("Set TEST_DATABASE_URL to a temporary PostgreSQL database")
    names = {"telegram:912": "Тестовая бригада", "telegram:913": "Другая бригада", "max:916": "Тестовая бригада"}
    settings = Settings(
        database=database, mode="preview", media_dir=tmp_path, citizen_bot_api_key="test-key",
        dispatch_bot_crews=names, dispatch_telegram_admins=("914",), dispatch_max_operators=("915",),
    )
    now = datetime.now(timezone.utc)
    point = f"Тестовый район {uuid4().hex}"

    def auth(user, platform="Telegram"):
        return {"X-Citizen-Bot-Key": "test-key", f"X-{platform}-User": user}

    with TestClient(api.create_app(settings)) as client:
        def bot_request(method, path, user, platform="Telegram", **kwargs):
            return client.request(method, BOT_DISPATCH_PATH + path, headers=auth(user, platform), **kwargs)

        assert client.get(BOT_DISPATCH_PATH + "/role").status_code == 403
        assert bot_request("GET", "/role", "911").json() == {"role": "resident"}
        assert bot_request("GET", "/role", "912").json() == {"role": "contractor"}
        assert bot_request("GET", "/role", "914").json() == {"role": "administration"}
        assert bot_request("GET", "/role", "915", "Max").json() == {"role": "dispatcher", "assigned_role": "dispatcher"}
        assert bot_request("GET", "/role", "915").json() == {"role": "resident"}
        assert bot_request("GET", "/tasks", "911").status_code == 403
        assert bot_request("GET", "/warnings", "912").status_code == 403
        assert bot_request("PUT", "/role", "917", "Max", json={"role": "contractor"}).status_code == 403
        assert bot_request("PUT", "/role", "916", "Max", json={"role": "dispatcher"}).status_code == 403
        assert bot_request("PUT", "/role", "916", "Max", json={"role": "resident"}).json() == {
            "role": "resident", "assigned_role": "contractor",
        }
        assert bot_request("GET", "/role", "916", "Max").json()["role"] == "resident"
        assert bot_request("GET", "/tasks", "916", "Max").status_code == 403
        assert bot_request("PUT", "/role", "916", "Max", json={"role": "contractor"}).json()["role"] == "contractor"
        assert bot_request("GET", "/tasks", "916", "Max").status_code == 200

        def complain():
            return client.post(
                REPORTS, headers=auth("911"),
                data={"request_id": request_id, "address": point, "description": "Сугроб перекрыл проезд",
                      "latitude": 53.02, "longitude": 158.65, "tags": "сугроб"},
                files={"photo": ("photo.png", png())},
            )

        request_id = str(uuid4())
        first = complain()
        assert first.status_code == 201, first.text
        assert complain().json() == first.json()
        citizen_id = first.json()["id"]
        dashboard = client.get("/api/v1/dashboard").json()
        assert set(dashboard["dispatch_assignees"]) >= set(names.values())
        group = next(group for group in dashboard["dispatch_queue"] if group["address"] == point)
        assert [report["id"] for report in group["reports"]] == [citizen_id]
        assert group["reports"][0]["citizen_report_id"] == citizen_id
        created = client.post("/api/v1/dispatch/tasks", json={
            "location_key": group["location_key"], "kind": group["kind"], "work_kind": "clear",
        })
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]
        assignment = f"/api/v1/dispatch/tasks/{task_id}/assignment"

        def assign(assignee, version):
            response = client.put(assignment, json={
                "version": version, "assignee": assignee, "due_at": (now + timedelta(hours=2)).isoformat(),
            })
            assert response.status_code == 200, response.text
            return response.json()["assignment_version"]

        version = assign(names["telegram:912"], 0)
        assert task_id in [task["id"] for task in bot_request("GET", "/tasks", "912").json()["items"]]
        assert task_id not in [task["id"] for task in bot_request("GET", "/tasks", "913").json()["items"]]

        def progress(action, by, version, note="", platform="Telegram"):
            return bot_request("PUT", f"/tasks/{task_id}", by, platform,
                               json={"action": action, "version": version, "note": note})

        assert progress("accept", "911", version).status_code == 403
        assert progress("accept", "913", version).status_code == 404
        reassigned = assign(names["telegram:913"], version)
        version = assign(names["telegram:912"], reassigned)
        assert progress("accept", "912", reassigned).status_code == 409
        crew = [("912", "Telegram"), ("916", "Max")]
        for user, platform in crew:
            notices = bot_request("GET", "/task-notices", user, platform).json()
            assert any(job["id"] == task_id and job["claimed_by"] is None for job in notices)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda member: progress("accept", member[0], version, platform=member[1]), crew))
        assert sorted(response.status_code for response in results) == [200, 409]
        winner_index = next(index for index, response in enumerate(results) if response.status_code == 200)
        winner, winner_platform = crew[winner_index]
        other, other_platform = crew[1 - winner_index]
        assert task_id not in [task["id"] for task in bot_request("GET", "/tasks", other, other_platform).json()["items"]]
        assert progress("start", other, version, platform=other_platform).status_code == 409
        assert progress("start", winner, version, platform=winner_platform).json() == {"stage": "in_progress"}
        version += 1
        assert progress("finish", winner, version, "Снег убран", winner_platform).status_code == 422
        assert bot_request("POST", f"/tasks/{task_id}/result", winner, winner_platform,
                           data={"version": version, "note": "Снег убран"}).status_code == 422

        def send_result(user, platform, image):
            return bot_request("POST", f"/tasks/{task_id}/result", user, platform,
                               data={"version": version, "note": "Снег убран"},
                               files={"photo": ("result.png", image)})

        assert send_result(other, other_platform, png()).status_code == 409
        assert send_result(winner, winner_platform, b"broken photo").status_code == 415
        assert send_result(winner, winner_platform, png()).json() == {"stage": "done"}
        version += 1
        assert progress("close", winner, version, "Проверено", winner_platform).status_code == 403
        photo_path = f"/api/v1/dispatch/tasks/{task_id}/result-photo"
        result_photo = client.get(photo_path)
        live_task = next(group["task"] for group in client.get("/api/v1/dashboard").json()["dispatch_queue"]
                         if group["address"] == point)
        assert live_task["has_result_photo"] and live_task["work_in_bot"]
        [photo_id] = live_task["result_photo_ids"]
        assert client.get(f"/api/v1/dispatch/result-photos/{photo_id}").content == result_photo.content
        assert result_photo.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(result_photo.content)) as image:
            assert image.size == (20, 10)
        assert bot_request("GET", f"/tasks/{task_id}/result-photo", other, other_platform).status_code == 403
        assert progress("finish", "915", version, "Снег убран", "Max").status_code == 403

        with connect(settings) as conn:
            conn.execute(
                "INSERT INTO warnings (run_id,point,revision,payload) VALUES ('preview',%s,1,%s)",
                (point, Jsonb({"start": now.isoformat(), "end": (now + timedelta(hours=1)).isoformat(),
                              "probability": .8})),
            )

        async def exercise_bots():
            outgoing = []

            async def transport(request):
                if request.url.host == "http.test":
                    response = client.request(
                        request.method, request.url.raw_path.decode(),
                        headers=request.headers, content=await request.aread(),
                    )
                    return httpx.Response(response.status_code, content=response.content)
                assert request.headers["Authorization"] == "test-max-token"
                if request.url.host == "iu.oneme.ru":
                    assert result_photo.content in await request.aread()
                    return httpx.Response(200, json={"photos": {"id": {"token": "result-image"}}})
                if request.url.path == "/uploads":
                    return httpx.Response(200, json={"url": "https://iu.oneme.ru/upload-result"})
                data = json.loads(await request.aread())
                if request.url.path == "/answers":
                    assert request.url.params["callback_id"] == "test-callback"
                    outgoing.append(data["message"])
                else:
                    assert request.url.path == "/messages"
                    assert request.url.params["user_id"] == "915"
                    outgoing.append(data)
                return httpx.Response(200, json={"success": True})

            mock = httpx.MockTransport(transport)
            async with (
                httpx.AsyncClient(base_url="http://http.test", transport=mock) as citizen,
                httpx.AsyncClient(base_url="https://platform-api2.max.ru", transport=mock,
                                  headers={"Authorization": "test-max-token"}) as max_client,
            ):
                telegram = SimpleNamespace(send_message=AsyncMock())
                bot = Bot(citizen, "test-key")
                await deliver_warnings(bot, telegram, "914")
                await deliver_warnings(bot, telegram, "914")
                telegram.send_message.assert_awaited_once()
                assert telegram.send_message.await_args.kwargs["chat_id"] == 914
                assert point in telegram.send_message.await_args.kwargs["text"]

                max_bot = MaxBot(citizen, "test-key")
                notify = AsyncMock()
                for member_bot, member_id in ((bot, "912"), (max_bot, "916")):
                    await deliver_task_notices(member_bot, notify, member_id)
                    await deliver_task_notices(member_bot, notify, member_id)
                assert {call.args[0] for call in notify.await_args_list if point in call.args[1]} == {"912", "916"}
                assert sum(point in call.args[1] for call in notify.await_args_list) == 2
                returned = client.put(f"/api/v1/dispatch/tasks/{task_id}/progress", json={
                    "action": "return", "version": version, "note": "Расчистите второй проход",
                })
                assert returned.json() == {"stage": "returned"}
                assert client.get(photo_path).status_code == 404
                notify.reset_mock()
                for member_bot, member_id in ((bot, "912"), (max_bot, "916")):
                    await deliver_task_notices(member_bot, notify, member_id)
                    await deliver_task_notices(member_bot, notify, member_id)
                notify.assert_awaited_once()
                recipient, text, keyboard = notify.await_args.args
                assert recipient == winner
                assert "Расчистите второй проход" in text and "новое фото" in text
                assert keyboard.inline_keyboard[0][0].text == "Начать доработку"
                assert progress("start", winner, version + 1, platform=winner_platform).json() == {"stage": "in_progress"}
                photo_again = bot_request(
                    "POST", f"/tasks/{task_id}/result", winner, winner_platform,
                    data={"version": version + 2, "note": "Снег убран"}, files={"photo": ("result.png", png())},
                )
                assert photo_again.json() == {"stage": "done"}

                sender = {"user_id": 915, "is_bot": False}

                async def send(text):
                    await max_bot.handle_update({
                        "update_type": "message_created",
                        "message": {"sender": sender, "recipient": {"chat_type": "dialog"}, "body": {"text": text}},
                    }, max_client, max_client)

                await send("/role")
                switch = outgoing[-1]["attachments"][0]["payload"]["buttons"][-1][0]
                assert switch["text"] == "Сменить роль"

                async def click(payload):
                    await max_bot.handle_update({
                        "update_type": "message_callback",
                        "callback": {"user": sender, "payload": payload, "callback_id": "test-callback"},
                        "message": {"recipient": {"chat_type": "dialog"}},
                    }, max_client, max_client)

                await click(switch["payload"])
                assert "Житель" in outgoing[-1]["text"]
                await click(outgoing[-1]["attachments"][0]["payload"]["buttons"][-1][0]["payload"])
                assert "Диспетчер" in outgoing[-1]["text"]
                await send("/jobs")
                task_card = next(item for item in outgoing if point in item["text"])
                assert task_card["attachments"][0] == {"type": "image", "payload": {"token": "result-image"}}
                button = task_card["attachments"][1]["payload"]["buttons"][0][0]
                assert button["text"] == "Подтвердить выполнение"
                assert button["type"] == "callback"
                await click(button["payload"])
                assert "Напишите, как проверили" in outgoing[-1]["text"]
                await send("Проверено диспетчером")
                assert "Выполнение подтверждено" in outgoing[-1]["text"]

        asyncio.run(exercise_bots())
        assert any(item["point"] == point for item in bot_request("GET", "/warnings", "914").json())
        with connect(settings) as conn:
            task = conn.execute("SELECT stage,result FROM dispatch_tasks WHERE (run_id,id)=('preview',%s)",
                                (task_id,)).fetchone()
            report = conn.execute("SELECT resolution FROM dispatch_reports WHERE (run_id,id)=('preview',%s)",
                                  (citizen_id,)).fetchone()
        assert task == {"stage": "closed", "result": "Снег убран"}
        assert report["resolution"] == "Проверено диспетчером"
        archived = next(item for item in client.get("/api/v1/dispatch/archive").json() if item["id"] == task_id)
        assert archived["has_result_photo"]
        [final_photo_id] = archived["result_photo_ids"]
        assert final_photo_id != photo_id
        assert client.get(f"/api/v1/dispatch/result-photos/{final_photo_id}").content == result_photo.content
        assert archived["claimed_by"] == f"{winner_platform.lower()}:{winner}"
        assert client.get(photo_path).content == result_photo.content

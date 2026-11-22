from dataclasses import replace
from io import BytesIO
import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from monitoring import api
from monitoring.citizens import MAX_PHOTO_BYTES, MAX_UPLOAD_BYTES
from monitoring.config import Settings
from monitoring.db import connect


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
        assert first["suggested_tags"] == []
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

    with TestClient(api.create_app(settings)) as client:
        photo = client.get(first["photo_url"])
        assert photo.status_code == 200
        assert photo.headers["content-type"] == "image/jpeg"
        with Image.open(BytesIO(photo.content)) as image:
            assert image.size == (20, 10)

    with TestClient(api.create_app(replace(settings, mode="demo"))) as client:
        assert client.get(first["photo_url"]).status_code == 404
        assert client.get(REPORTS, params=params).json() == {"items": [], "next_offset": None}

from datetime import datetime
from io import BytesIO
import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.routing import APIRoute
from PIL import Image, ImageOps
from pydantic import AwareDatetime, BaseModel

from .citizen_tagging import analyze_description
from .db import connect
from .districts import DISTRICT_ID_PATTERN, UNASSIGNED, locate_district


MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_BYTES = MAX_PHOTO_BYTES + 64 * 1024
MAX_PHOTO_PIXELS = 16_000_000
Tag = Literal["снег", "дождь", "пробка", "сугроб"]
LOGGER = logging.getLogger("uvicorn.error.citizens")
REPORTS_PATH = "/api/v1/citizens/reports"
REPORT_COLUMNS = "id, district_id, district, description, latitude, longitude, tags, suggested_tags, created_at, address"
REPORT_SELECT = REPORT_COLUMNS + ", (photo_jpeg IS NOT NULL) AS has_photo"


class Report(BaseModel):
    id: UUID
    district_id: str
    district: str | None
    description: str
    latitude: float
    longitude: float
    tags: list[Tag]
    suggested_tags: list[str]
    address: str
    created_at: AwareDatetime
    photo_url: str | None


class ReportPage(BaseModel):
    items: list[Report]
    next_offset: int | None


class DistrictOption(BaseModel):
    id: str
    name: str | None
    parent_id: str | None = None


class ReportRoute(APIRoute):
    max_upload_bytes = MAX_UPLOAD_BYTES

    def get_route_handler(self):
        handle = super().get_route_handler()
        if "POST" not in self.methods:
            return handle

        async def limit_upload(request: Request):
            async def receive():
                nonlocal received
                message = await request.receive()
                received += len(message.get("body", b""))
                if received > self.max_upload_bytes:
                    raise HTTPException(413, "Превышен максимальный размер запроса")
                return message

            received = 0
            return await handle(Request(request.scope, receive))

        return limit_upload


def photo_jpeg(file, *, max_bytes=MAX_PHOTO_BYTES, max_pixels=MAX_PHOTO_PIXELS) -> bytes:
    data = file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"Фото должно быть не больше {max_bytes // (1024 * 1024)} МиБ")
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
            if image.width * image.height > max_pixels:
                raise HTTPException(413, f"Фото должно быть не больше {max_pixels // 1_000_000} мегапикселей")
            if getattr(image, "is_animated", False):
                raise HTTPException(415, "Требуется один снимок в формате JPEG или PNG")
            normalized = ImageOps.exif_transpose(image).convert("RGB")
    except Image.DecompressionBombError as exc:
        raise HTTPException(413, f"Фото должно быть не больше {max_pixels // 1_000_000} мегапикселей") from exc
    except (OSError, ValueError, SyntaxError) as exc:
        raise HTTPException(415, "Требуется корректное фото JPEG или PNG") from exc

    normalized.thumbnail((2048, 2048))
    normalized.info.clear()
    output = BytesIO()
    normalized.save(output, format="JPEG", quality=90)
    if output.tell() > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Фото должно быть не больше 5 МиБ после преобразования")
    return output.getvalue()


def serialize(row, request: Request):
    photo_path = request.url_for("citizen_report_photo", report_id=row["id"]).path if row["has_photo"] else None
    return Report(**row, photo_url=photo_path)


def save_report(conn, *, mode, actor, districts, image, description, latitude, longitude,
                tags=(), address="", request_id=None, created_at: datetime | None = None):
    district = locate_district(districts, latitude, longitude)
    return conn.execute(
        f"""INSERT INTO citizen_reports (id, mode, submitted_by, district, district_id, description,
                                         latitude, longitude, tags, suggested_tags, photo_jpeg,
                                         address, request_id, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,COALESCE(%s,now()))
            ON CONFLICT (mode, submitted_by, request_id) DO UPDATE
            SET request_id=EXCLUDED.request_id RETURNING {REPORT_SELECT}""",
        (uuid4(), mode, actor, district.name if district else None, district.id if district else UNASSIGNED,
         description, latitude, longitude, list(dict.fromkeys(tags)), list(analyze_description(description)),
         image, address, request_id, created_at),
    ).fetchone()


def refine_report_districts(settings, districts):
    eligible = [UNASSIGNED, *sorted({district.parent_id for district in districts if district.parent_id})]
    with connect(settings) as conn, conn.cursor(name="unassigned_citizen_reports") as reports:
        reports.execute(
            "SELECT id, district_id, latitude, longitude FROM citizen_reports WHERE mode=%s AND district_id=ANY(%s)",
            (settings.mode, eligible),
        )
        for report in reports:
            district = locate_district(districts, report["latitude"], report["longitude"])
            if district and (report["district_id"] == UNASSIGNED or district.parent_id == report["district_id"]):
                conn.execute(
                    """UPDATE citizen_reports SET district_id=%s, district=%s
                       WHERE id=%s AND mode=%s AND district_id=%s""",
                    (district.id, district.name, report["id"], settings.mode, report["district_id"]),
                )


def router(settings, citizen_identity, staff_identity):
    routes = APIRouter(tags=["citizens"], route_class=ReportRoute)

    @routes.get("/api/v1/citizens/districts", dependencies=[Depends(staff_identity)], response_model_exclude_unset=True)
    def list_districts(request: Request) -> list[DistrictOption]:
        return [
            *(DistrictOption(id=district.id, name=district.name, **({"parent_id": district.parent_id} if district.parent_id else {}))
              for district in request.app.state.citizen_districts),
            DistrictOption(id=UNASSIGNED, name=None),
        ]

    @routes.post(REPORTS_PATH, status_code=201)
    def submit(
        request: Request,
        actor: Annotated[str, Depends(citizen_identity)],
        description: Annotated[str, Form(min_length=1, max_length=2000)],
        latitude: Annotated[float, Form(ge=-90, le=90)],
        longitude: Annotated[float, Form(ge=-180, le=180)],
        tags: Annotated[list[Tag] | None, Form(max_length=4)] = None,
        address: Annotated[str, Form(max_length=240)] = "",
        request_id: Annotated[UUID | None, Form()] = None,
        photo: Annotated[UploadFile | None, File()] = None,
    ) -> Report:
        description = description.strip()
        if not description or "\x00" in description:
            raise HTTPException(422, "Укажите описание")

        if "\x00" in address:
            raise HTTPException(422, "Проверьте адрес")
        image = photo_jpeg(photo.file) if photo else None
        with connect(settings) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))", (settings.mode, actor))
            if request_id is not None:
                saved = conn.execute(
                    f"SELECT {REPORT_SELECT} FROM citizen_reports WHERE mode=%s AND submitted_by=%s AND request_id=%s",
                    (settings.mode, actor, request_id),
                ).fetchone()
                if saved:
                    return serialize(saved, request)
            recent = conn.execute(
                """SELECT count(*) AS count FROM citizen_reports WHERE mode=%s AND submitted_by=%s
                   AND created_at > now() - interval '1 hour'""", (settings.mode, actor),
            ).fetchone()
            if recent["count"] >= 30:
                raise HTTPException(429, "Слишком много обращений. Повторите позже.", headers={"Retry-After": "3600"})
            row = save_report(conn, mode=settings.mode, actor=actor,
                              districts=request.app.state.citizen_districts, image=image,
                              description=description, latitude=latitude, longitude=longitude,
                              tags=tags or (), address=address.strip(), request_id=request_id)
        LOGGER.info("Citizen report accepted id=%s mode=%s", row["id"], settings.mode)
        return serialize(row, request)

    @routes.get(REPORTS_PATH, dependencies=[Depends(staff_identity)])
    def list_reports(
        request: Request,
        district: Annotated[str | None, Query(pattern=DISTRICT_ID_PATTERN)] = None,
        start: AwareDatetime | None = None,
        end: AwareDatetime | None = None,
        id: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    ) -> ReportPage:
        if start is not None and end is not None and start >= end:
            raise HTTPException(422, "Начало периода должно предшествовать концу")
        conditions = ["mode=%s"]
        values = [settings.mode]
        if district is not None:
            conditions.append("district_id=ANY(%s)")
            values.append([district, *(item.id for item in request.app.state.citizen_districts if item.parent_id == district)])
        for condition, value in (("created_at >= %s", start),
                                 ("created_at < %s", end), ("id=%s", id)):
            if value is not None:
                conditions.append(condition)
                values.append(value)
        with connect(settings) as conn:
            rows = conn.execute(
                f"""SELECT {REPORT_SELECT} FROM citizen_reports
                    WHERE {" AND ".join(conditions)}
                    ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s""",
                (*values, limit + 1, offset),
            ).fetchall()
        return ReportPage(
            items=[serialize(row, request) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )

    @routes.get(REPORTS_PATH + "/mine")
    def own_reports(
        request: Request,
        actor: Annotated[str, Depends(citizen_identity)],
        before: UUID | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 5,
    ) -> list[Report]:
        # Local browser requests share one actor. Personal history requires a verified identity.
        if settings.mode != "live" and not actor.startswith("telegram:"):
            raise HTTPException(403, "Требуется личный аккаунт")
        with connect(settings) as conn:
            rows = conn.execute(
                f"""SELECT {REPORT_SELECT} FROM citizen_reports
                    WHERE mode=%s AND submitted_by=%s AND
                    (%s::uuid IS NULL OR (created_at, id) < (
                        SELECT created_at, id FROM citizen_reports
                        WHERE mode=%s AND submitted_by=%s AND id=%s))
                    ORDER BY created_at DESC, id DESC LIMIT %s""",
                (settings.mode, actor, before, settings.mode, actor, before, limit),
            ).fetchall()
        return [serialize(row, request) for row in rows]

    @routes.get(REPORTS_PATH + "/zones", dependencies=[Depends(staff_identity)])
    def zones(minimum: Annotated[int, Query(ge=1, le=2**31 - 1)] = 2):
        with connect(settings) as conn:
            return conn.execute(
                """SELECT LEAST(floor(latitude::numeric * 100), 8999)::int AS latitude_cell,
                          LEAST(floor(longitude::numeric * 100), 17999)::int AS longitude_cell,
                          count(*) AS count
                   FROM citizen_reports WHERE mode=%s GROUP BY 1, 2 HAVING count(*) >= %s
                   ORDER BY count DESC, 1, 2""",
                (settings.mode, minimum),
            ).fetchall()

    @routes.get(REPORTS_PATH + "/{report_id}")
    def own_report(request: Request, report_id: UUID, actor: Annotated[str, Depends(citizen_identity)]):
        if settings.mode != "live" and not actor.startswith("telegram:"):
            raise HTTPException(403, "Требуется личный аккаунт")
        with connect(settings) as conn:
            row = conn.execute(
                f"SELECT {REPORT_SELECT} FROM citizen_reports WHERE mode=%s AND submitted_by=%s AND id=%s",
                (settings.mode, actor, report_id),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Обращение не найдено")
        return serialize(row, request)

    @routes.get(REPORTS_PATH + "/{report_id}/photo", name="citizen_report_photo")
    def get_photo(report_id: UUID, actor: Annotated[str, Depends(citizen_identity)]):
        staff = (settings.mode != "live" and actor == "local-demo"
                 or actor in settings.operators or actor in settings.viewers)
        with connect(settings) as conn:
            row = conn.execute(
                """SELECT photo_jpeg FROM citizen_reports WHERE mode=%s AND id=%s
                   AND (submitted_by=%s OR %s)""",
                (settings.mode, report_id, actor, staff),
            ).fetchone()
        if not row or row["photo_jpeg"] is None:
            raise HTTPException(404, "Фото не найдено")
        return Response(row["photo_jpeg"], media_type="image/jpeg")

    return routes

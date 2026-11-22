from io import BytesIO
import logging
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.routing import APIRoute
from PIL import Image, ImageOps
from pydantic import AwareDatetime, BaseModel

from .db import connect
from .districts import DISTRICT_ID_PATTERN, UNASSIGNED, locate_district


MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_BYTES = MAX_PHOTO_BYTES + 64 * 1024
MAX_PHOTO_PIXELS = 16_000_000
Tag = Literal["снег", "дождь", "пробка", "сугроб"]
LOGGER = logging.getLogger("uvicorn.error.citizens")
REPORTS_PATH = "/api/v1/citizens/reports"
REPORT_COLUMNS = "id, district_id, district, description, latitude, longitude, tags, suggested_tags, created_at"


class Report(BaseModel):
    id: UUID
    district_id: str
    district: str | None
    description: str
    latitude: float
    longitude: float
    tags: list[Tag]
    suggested_tags: list[Tag]
    created_at: AwareDatetime
    photo_url: str


class ReportPage(BaseModel):
    items: list[Report]
    next_offset: int | None


class DistrictOption(BaseModel):
    id: str
    name: str | None


class ReportRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()
        if "POST" not in self.methods:
            return handle

        async def limit_upload(request: Request):
            async def receive():
                nonlocal received
                message = await request.receive()
                received += len(message.get("body", b""))
                if received > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Превышен максимальный размер запроса")
                return message

            received = 0
            return await handle(Request(request.scope, receive))

        return limit_upload


def photo_jpeg(file) -> bytes:
    data = file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, "Фото должно быть не больше 5 МиБ")
    try:
        with Image.open(BytesIO(data), formats=("JPEG", "PNG")) as image:
            if image.width * image.height > MAX_PHOTO_PIXELS:
                raise HTTPException(413, "Фото должно быть не больше 16 мегапикселей")
            if getattr(image, "is_animated", False):
                raise HTTPException(415, "Требуется один снимок в формате JPEG или PNG")
            normalized = ImageOps.exif_transpose(image).convert("RGB")
    except Image.DecompressionBombError as exc:
        raise HTTPException(413, "Фото должно быть не больше 16 мегапикселей") from exc
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
    photo_path = request.url_for("citizen_report_photo", report_id=row["id"]).path
    return Report(**row, photo_url=photo_path)


def router(settings, citizen_identity, staff_identity):
    routes = APIRouter(tags=["citizens"], route_class=ReportRoute)

    @routes.get("/api/v1/citizens/districts", dependencies=[Depends(staff_identity)])
    def list_districts(request: Request) -> list[DistrictOption]:
        return [
            *(DistrictOption(id=district.id, name=district.name) for district in request.app.state.citizen_districts),
            DistrictOption(id=UNASSIGNED, name=None),
        ]

    @routes.post(REPORTS_PATH, status_code=201)
    def submit(
        request: Request,
        actor: Annotated[str, Depends(citizen_identity)],
        photo: Annotated[UploadFile, File()],
        description: Annotated[str, Form(min_length=1, max_length=2000)],
        latitude: Annotated[float, Form(ge=-90, le=90)],
        longitude: Annotated[float, Form(ge=-180, le=180)],
        tags: Annotated[list[Tag] | None, Form(max_length=4)] = None,
    ) -> Report:
        description = description.strip()
        if not description or "\x00" in description:
            raise HTTPException(422, "Укажите описание")

        image = photo_jpeg(photo.file)
        district = locate_district(request.app.state.citizen_districts, latitude, longitude)
        district_id = district.id if district else UNASSIGNED
        with connect(settings) as conn:
            row = conn.execute(
                f"""INSERT INTO citizen_reports (id, mode, submitted_by, district, district_id, description,
                                                 latitude, longitude, tags, photo_jpeg)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING {REPORT_COLUMNS}""",
                (uuid4(), settings.mode, actor, district.name if district else None, district_id, description, latitude, longitude,
                 list(dict.fromkeys(tags or ())), image),
            ).fetchone()
        LOGGER.info("Citizen report created id=%s mode=%s", row["id"], settings.mode)
        return serialize(row, request)

    @routes.get(REPORTS_PATH, dependencies=[Depends(staff_identity)])
    def list_reports(
        request: Request,
        district: Annotated[str, Query(pattern=DISTRICT_ID_PATTERN)],
        start: AwareDatetime,
        end: AwareDatetime,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=2**63 - 1)] = 0,
    ) -> ReportPage:
        if start >= end:
            raise HTTPException(422, "Начало периода должно предшествовать концу")
        with connect(settings) as conn:
            rows = conn.execute(
                f"""SELECT {REPORT_COLUMNS} FROM citizen_reports
                    WHERE mode=%s AND district_id=%s AND created_at >= %s AND created_at < %s
                    ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s""",
                (settings.mode, district, start, end, limit + 1, offset),
            ).fetchall()
        return ReportPage(
            items=[serialize(row, request) for row in rows[:limit]],
            next_offset=offset + limit if len(rows) > limit else None,
        )

    @routes.get(REPORTS_PATH + "/{report_id}/photo", name="citizen_report_photo")
    def get_photo(report_id: UUID, actor: Annotated[str, Depends(citizen_identity)]):
        with connect(settings) as conn:
            row = conn.execute(
                """SELECT photo_jpeg FROM citizen_reports WHERE mode=%s AND id=%s
                   AND (submitted_by=%s OR %s)""",
                (settings.mode, report_id, actor, actor in settings.operators or actor in settings.viewers),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Фото не найдено")
        return Response(row["photo_jpeg"], media_type="image/jpeg")

    return routes

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from . import dispatch_queue
from .citizens import MAX_BOT_PHOTO_FORMATS, PHOTO_FORMATS, ReportRoute, photo_jpeg
from .db import connect


PATH = "/api/v1/bot-dispatch"


class BotProgress(dispatch_queue.ProgressDraft):
    action: Literal["accept", "start", "finish", "close"]


class RoleSelection(BaseModel):
    role: Literal["resident", "contractor", "dispatcher"]


class TaskNoticeReceipt(BaseModel):
    run_id: str = Field(min_length=1, max_length=255)
    task_id: UUID
    version: int = Field(ge=1, le=2**31 - 1)


class WarningReceipt(BaseModel):
    run_id: str = Field(min_length=1, max_length=255)
    point: str = Field(min_length=1, max_length=255)
    revision: int = Field(ge=1, le=2**31 - 1)


def router(settings, citizen_identity, selected_run):
    routes = APIRouter(prefix=PATH, route_class=ReportRoute)

    def bot_actor(actor: str = Depends(citizen_identity)):
        if not actor.startswith(("telegram:", "max:")):
            raise HTTPException(403, "Действие доступно в боте")
        return actor

    def assigned_role(actor):
        if actor in settings.dispatch_bot_crews:
            return "contractor"
        if actor.startswith("telegram:") and actor[9:] in settings.dispatch_telegram_admins:
            return "administration"
        if actor.startswith("max:") and actor[4:] in settings.dispatch_max_operators:
            return "dispatcher"
        return "resident"

    def can_switch(actor, assigned):
        return actor.startswith("max:") and assigned in {"contractor", "dispatcher"}

    def role_info(conn, actor):
        assigned = assigned_role(actor)
        if not can_switch(actor, assigned):
            return {"role": assigned}
        resident = conn.execute(
            "SELECT 1 FROM bot_role_preferences WHERE (mode,actor)=(%s,%s)", (settings.mode, actor),
        ).fetchone()
        return {"role": "resident" if resident else assigned, "assigned_role": assigned}

    def administration(actor: str = Depends(bot_actor)):
        if assigned_role(actor) != "administration":
            raise HTTPException(403, "Раздел доступен администрации")
        return actor

    def task_access(role, actor):
        if role == "contractor":
            return settings.dispatch_bot_crews[actor], ["sent", "accepted", "in_progress", "returned", "done"]
        if role == "dispatcher":
            return None, ["sent", "done"]
        raise HTTPException(403, "Раздел доступен подрядчику или диспетчеру")

    @routes.get("/role")
    def role(actor: str = Depends(bot_actor)):
        with connect(settings) as conn:
            return role_info(conn, actor)

    @routes.put("/role")
    def select_role(draft: RoleSelection, actor: str = Depends(bot_actor)):
        assigned = assigned_role(actor)
        if not can_switch(actor, assigned) or draft.role not in {"resident", assigned}:
            raise HTTPException(403, "Эта роль не назначена вашему аккаунту")
        with connect(settings) as conn:
            if draft.role == "resident":
                conn.execute("INSERT INTO bot_role_preferences VALUES (%s,%s) ON CONFLICT DO NOTHING",
                             (settings.mode, actor))
            else:
                conn.execute("DELETE FROM bot_role_preferences WHERE (mode,actor)=(%s,%s)",
                             (settings.mode, actor))
            return role_info(conn, actor)

    @routes.get("/tasks")
    def tasks(actor: str = Depends(bot_actor), offset: int = Query(0, ge=0, le=2**31 - 1)):
        with connect(settings) as conn:
            current = role_info(conn, actor)["role"]
            assignee, stages = task_access(current, actor)
            rows = conn.execute(
                """SELECT id,address,district,work_kind,assignee,due_at,stage,instructions,result,assignment_version,claimed_by,
                          EXISTS (SELECT 1 FROM dispatch_task_photos p WHERE (p.run_id,p.task_id)=(t.run_id,t.id)) AS has_result_photo,
                          (SELECT note FROM dispatch_task_history h WHERE (h.run_id,h.task_id)=(t.run_id,t.id)
                           AND action='return' ORDER BY id DESC LIMIT 1) AS return_note
                   FROM dispatch_tasks t WHERE run_id=%s AND closed_at IS NULL AND stage=ANY(%s)
                     AND (%s::text IS NULL OR (assignee=%s AND (claimed_by IS NULL OR claimed_by=%s)))
                   ORDER BY (severity='urgent') DESC,due_at NULLS LAST,id LIMIT 6 OFFSET %s""",
                (selected_run(conn, None), stages, assignee, assignee, actor, offset),
            ).fetchall()
        return {"role": current, "items": rows[:5], "next_offset": offset + 5 if len(rows) > 5 else None}

    def contractor_token(conn, run_id, task_id, assignee):
        row = conn.execute(
            """SELECT crew_token FROM dispatch_tasks
               WHERE (run_id,id)=(%s,%s) AND assignee=%s AND closed_at IS NULL""",
            (run_id, task_id, assignee),
        ).fetchone()
        if not row or not row["crew_token"]:
            raise HTTPException(404, "Открытое задание не найдено")
        return row["crew_token"]

    @routes.put("/tasks/{task_id}")
    def progress(task_id: UUID, draft: BotProgress, actor: str = Depends(bot_actor)):
        with connect(settings) as conn:
            assignee, _ = task_access(role_info(conn, actor)["role"], actor)
            actions = {"accept", "close"} if assignee is None else {"accept", "start", "finish"}
            if draft.action not in actions:
                raise HTTPException(403, "Действие недоступно для этой роли")
            run_id = selected_run(conn, None)
            token = contractor_token(conn, run_id, task_id, assignee) if assignee is not None else None
            return dispatch_queue.progress_task(conn, run_id, task_id, actor, draft, token, contractor=assignee is not None)

    @routes.post("/tasks/{task_id}/result")
    def task_result(
        task_id: UUID, version: Annotated[int, Form(ge=1, le=2**31 - 1)],
        note: Annotated[str, Form(min_length=1, max_length=1500)],
        photo: Annotated[UploadFile, File()], actor: str = Depends(bot_actor),
    ):
        with connect(settings) as conn:
            if role_info(conn, actor)["role"] != "contractor":
                raise HTTPException(403, "Результат отправляет подрядчик")
            run_id = selected_run(conn, None)
            token = contractor_token(conn, run_id, task_id, settings.dispatch_bot_crews[actor])
            formats = MAX_BOT_PHOTO_FORMATS if actor.startswith("max:") else PHOTO_FORMATS
            image = photo_jpeg(photo.file, formats=formats)
            if not note.strip():
                raise HTTPException(422, "Опишите результат работы")
            draft = dispatch_queue.ProgressDraft(action="finish", version=version, note=note)
            return dispatch_queue.progress_task(conn, run_id, task_id, actor, draft, token,
                                                contractor=True, photo=image)

    @routes.get("/tasks/{task_id}/result-photo")
    def result_photo(task_id: UUID, actor: str = Depends(bot_actor)):
        with connect(settings) as conn:
            if role_info(conn, actor)["role"] != "dispatcher":
                raise HTTPException(403, "Фото результата доступно диспетчеру")
            row = conn.execute("SELECT photo_jpeg FROM dispatch_task_photos WHERE (run_id,task_id)=(%s,%s)",
                               (selected_run(conn, None), task_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Фото результата не найдено")
        return Response(row["photo_jpeg"], media_type="image/jpeg")

    def assigned_crew(actor: str = Depends(bot_actor)):
        crew = settings.dispatch_bot_crews.get(actor)
        if crew is None:
            raise HTTPException(403, "Аккаунт не входит в бригаду")
        return actor, crew

    @routes.get("/task-notices")
    def task_notices(member=Depends(assigned_crew)):
        actor, crew = member
        with connect(settings) as conn:
            return conn.execute(
                """SELECT t.run_id,t.id,t.address,t.district,t.work_kind,t.stage,t.assignment_version,
                          t.claimed_by, n.version AS notice_version, n.rework,
                          CASE WHEN n.rework THEN (
                              SELECT note FROM dispatch_task_history h
                              WHERE (h.run_id,h.task_id)=(t.run_id,t.id) AND h.action='return'
                              ORDER BY h.id DESC LIMIT 1
                          ) END AS return_note
                   FROM dispatch_tasks t CROSS JOIN LATERAL (
                       SELECT t.stage='returned' AND t.claimed_by=%s AS rework,
                              CASE WHEN t.stage='returned' AND t.claimed_by=%s THEN t.assignment_version
                                   ELSE t.assignment_notice_version END AS version
                   ) n LEFT JOIN dispatch_task_notice_receipts r
                     ON (r.run_id,r.task_id,r.recipient)=(t.run_id,t.id,%s)
                   WHERE t.run_id=%s AND t.assignee=%s
                     AND n.version > COALESCE(r.version,0)
                   ORDER BY n.rework DESC NULLS LAST,t.assigned_at,t.id LIMIT 20""",
                (actor, actor, actor, selected_run(conn, None), crew),
            ).fetchall()

    @routes.put("/task-notices/receipt")
    def task_notice_receipt(draft: TaskNoticeReceipt, member=Depends(assigned_crew)):
        actor, crew = member
        with connect(settings) as conn:
            if selected_run(conn, None) != draft.run_id:
                raise HTTPException(404, "Запуск не найден")
            conn.execute(
                """INSERT INTO dispatch_task_notice_receipts (run_id,task_id,recipient,version)
                   SELECT run_id,id,%s,%s FROM dispatch_tasks
                   WHERE (run_id,id)=(%s,%s) AND assignee=%s AND
                     (assignment_notice_version=%s OR (stage='returned' AND claimed_by=%s AND assignment_version=%s))
                   ON CONFLICT (run_id,task_id,recipient) DO UPDATE
                   SET version=GREATEST(dispatch_task_notice_receipts.version,EXCLUDED.version)""",
                (actor, draft.version, draft.run_id, draft.task_id, crew, draft.version, actor, draft.version),
            )
        return {"ok": True}

    @routes.get("/warnings")
    def warnings(pending: bool = False, actor: str = Depends(administration)):
        with connect(settings) as conn:
            return conn.execute(
                """SELECT w.run_id,w.point,w.revision,w.payload FROM warnings w
                   LEFT JOIN bot_warning_receipts r ON (r.run_id,r.point,r.recipient)=(w.run_id,w.point,%s)
                   WHERE w.run_id=%s AND (w.payload->>'end')::timestamptz > now()
                     AND (NOT %s OR w.revision > COALESCE(r.revision,0))
                   ORDER BY w.point""",
                (actor, selected_run(conn, None), pending),
            ).fetchall()

    @routes.put("/warnings/receipt")
    def warning_receipt(draft: WarningReceipt, actor: str = Depends(administration)):
        with connect(settings) as conn:
            if selected_run(conn, None) != draft.run_id:
                raise HTTPException(404, "Запуск не найден")
            cursor = conn.execute(
                """INSERT INTO bot_warning_receipts (run_id,point,recipient,revision)
                   SELECT run_id,point,%s,%s FROM warnings WHERE (run_id,point)=(%s,%s) AND revision >= %s
                   ON CONFLICT (run_id,point,recipient) DO UPDATE
                   SET revision=GREATEST(bot_warning_receipts.revision,EXCLUDED.revision)""",
                (actor, draft.revision, draft.run_id, draft.point, draft.revision),
            )
            if not cursor.rowcount:
                raise HTTPException(404, "Предупреждение не найдено")
        return {"ok": True}

    return routes

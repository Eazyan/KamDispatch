from hashlib import sha256
from datetime import datetime, timezone
from functools import lru_cache
import gzip
import json
from secrets import token_urlsafe
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, model_validator

from .config import ROOT


ROADS_FILE = ROOT / "monitoring" / "snow_roads.json.gz"
IssueKind = Literal["snowfall", "coverage", "drift", "other"]
WorkKind = Literal["inspect", "clear"]
LocationText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Point = tuple[Annotated[float, Field(ge=-180, le=180)], Annotated[float, Field(ge=-85.05, le=85.05)]]


@lru_cache(maxsize=1)
def road_index():
    with gzip.open(ROADS_FILE, "rt", encoding="utf-8") as stream:
        return {road["id"]: road for road in json.load(stream)["segments"]}


class ReportDraft(BaseModel):
    id: UUID
    location_key: LocationText | None = None
    district: LocationText | None = None
    address: LocationText | None = None
    road_id: LocationText | None = None
    endpoints: tuple[Point, Point] | None = None
    kind: IssueKind
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1500)]
    channel: Literal["phone", "online", "in_person", "other"]
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    severity: Literal["urgent", "watch"] = "watch"
    passage_obstructed: bool = False

    @model_validator(mode="after")
    def validate_location(self):
        if self.location_key and (self.district or self.address or self.road_id or self.endpoints):
            raise ValueError("Выберите один способ указания места")
        if self.road_id and self.endpoints:
            raise ValueError("Выберите дорогу или отметьте границы вручную")
        if self.endpoints and self.endpoints[0] == self.endpoints[1]:
            raise ValueError("Укажите две разные точки")
        if not self.location_key and (not self.district or not (self.address or self.road_id or self.endpoints)):
            raise ValueError("Укажите район и место")
        return self


class TaskDraft(BaseModel):
    location_key: LocationText
    kind: IssueKind
    work_kind: WorkKind | None = Field(default=None)


class AssignmentDraft(BaseModel):
    version: int = Field(ge=0)
    assignee: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    due_at: AwareDatetime
    instructions: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1500)] = ""


class ReceiptDraft(BaseModel):
    version: int = Field(ge=1)

class ProgressDraft(ReceiptDraft):
    action: Literal["accept", "start", "finish", "return", "close", "followup"]
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1500)] = ""


class ReportLinkDraft(BaseModel):
    task_id: UUID


def log_task(conn, run_id, task_id, actor, action, note="", assignee=None, due_at=None):
    conn.execute(
        """INSERT INTO dispatch_task_history (run_id,task_id,action,actor,note,assignee,due_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""", (run_id, task_id, action, actor, note, assignee, due_at),
    )


def assignment_task(conn, run_id, task_id, version, token=None):
    task = conn.execute(
        """SELECT * FROM dispatch_tasks WHERE (run_id,id)=(%s,%s) AND closed_at IS NULL
           AND (%s::text IS NULL OR crew_token=%s) FOR UPDATE""", (run_id, task_id, token, token),
    ).fetchone()
    if task is None:
        raise HTTPException(404, "Открытое задание не найдено")
    if version != task["assignment_version"]:
        raise HTTPException(409, "Назначение изменилось. Обновите список и проверьте исполнителя")
    return task


def assign_task(conn, run_id, task_id, actor, draft):
    task = assignment_task(conn, run_id, task_id, draft.version)
    if task["stage"] in {"done", "closed"}:
        raise HTTPException(409, "Сначала рассмотрите результат работ")
    same_assignee = task["assignee"] == draft.assignee
    if same_assignee and task["due_at"] == draft.due_at and task["instructions"] == draft.instructions:
        return {"assignment_version": task["assignment_version"]}
    if draft.due_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "Срок должен быть позже текущего времени")
    result = conn.execute(
        """WITH first_source AS (
               SELECT min(received_at) AS at FROM (
                   SELECT e.opened_at AS received_at FROM dispatch_task_events s
                   JOIN events e ON (e.run_id,e.id)=(s.run_id,s.event_id) WHERE (s.run_id,s.task_id)=(%s,%s)
                   UNION ALL
                   SELECT r.created_at FROM dispatch_task_reports s
                   JOIN dispatch_reports r ON (r.run_id,r.id)=(s.run_id,s.report_id) WHERE (s.run_id,s.task_id)=(%s,%s)
               ) sources
           )
           UPDATE dispatch_tasks t SET assignee=%s,due_at=%s,assigned_at=now(),assigned_by=%s,instructions=%s,
                  accepted_at=%s, accepted_by=%s, stage=%s, crew_token=%s,
                  result=CASE WHEN %s THEN t.result ELSE '' END,
                  completed_at=CASE WHEN %s THEN t.completed_at END,
                  response_seconds=COALESCE(t.response_seconds,
                      GREATEST(0, EXTRACT(EPOCH FROM now()-COALESCE(first_source.at,t.created_at)))::int),
                  assignment_version=assignment_version+1
           FROM first_source WHERE (t.run_id,t.id)=(%s,%s) RETURNING t.assignment_version""",
        (run_id, task_id, run_id, task_id, draft.assignee, draft.due_at, actor, draft.instructions,
         task["accepted_at"] if same_assignee else None, task["accepted_by"] if same_assignee else None,
         task["stage"] if same_assignee else "sent",
         (task["crew_token"] if same_assignee else None) or token_urlsafe(32),
         same_assignee, same_assignee, run_id, task_id),
    ).fetchone()
    log_task(conn, run_id, task_id, actor, "assignment", draft.instructions, draft.assignee, draft.due_at)
    return result


def confirm_task(conn, run_id, task_id, actor, draft):
    task = assignment_task(conn, run_id, task_id, draft.version)
    if not task["assignee"]:
        raise HTTPException(409, "У задания ещё нет исполнителя")
    return record_receipt(conn, task, actor)


def record_receipt(conn, task, actor):
    if task["accepted_at"]:
        return {"accepted_at": task["accepted_at"]}
    if task["stage"] != "sent":
        raise HTTPException(409, "Задание не ожидает подтверждения")
    log_task(conn, task["run_id"], task["id"], actor, "accept")
    return conn.execute(
        """UPDATE dispatch_tasks SET stage='accepted',accepted_at=now(),accepted_by=%s
           WHERE (run_id,id)=(%s,%s) RETURNING accepted_at""", (actor, task["run_id"], task["id"]),
    ).fetchone()


def crew_task(conn, task_id, token):
    task = conn.execute(
        """SELECT run_id,id,work_kind,address,district,assignee,due_at,stage,severity,instructions,result,assignment_version,
                  (SELECT r.map_section FROM dispatch_task_reports s JOIN dispatch_reports r
                   ON (r.run_id,r.id)=(s.run_id,s.report_id)
                   WHERE (s.run_id,s.task_id)=(t.run_id,t.id) AND r.map_section IS NOT NULL
                   ORDER BY r.created_at DESC LIMIT 1) AS map_section,
                  (SELECT note FROM dispatch_task_history h
                   WHERE (h.run_id,h.task_id)=(t.run_id,t.id) AND action='return' ORDER BY id DESC LIMIT 1) AS return_note
           FROM dispatch_tasks t WHERE t.id=%s AND t.crew_token=%s""", (task_id, token),
    ).fetchone()
    if task is None:
        raise HTTPException(404, "Задание по этой ссылке недоступно")
    return task


def issue_crew_link(conn, run_id, task_id):
    row = conn.execute(
        """UPDATE dispatch_tasks SET crew_token=COALESCE(crew_token,%s)
           WHERE (run_id,id)=(%s,%s) AND assignee IS NOT NULL AND closed_at IS NULL RETURNING crew_token""",
        (token_urlsafe(32), run_id, task_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Открытое назначенное задание не найдено")
    return row["crew_token"]


def progress_task(conn, run_id, task_id, actor, draft, token=None):
    action = draft.action
    transitions = {
        "accept": ({"sent", "accepted", "in_progress", "returned", "done"}, "accepted"),
        "start": ({"accepted", "returned"}, "in_progress"),
        "finish": ({"in_progress"}, "done"),
        "return": ({"done"}, "returned"),
        "close": ({"done"}, "closed"),
        "followup": ({"done"}, "closed"),
    }
    if token and action not in {"accept", "start", "finish"}:
        raise HTTPException(403, "Решение доступно диспетчеру")
    if action in {"close", "followup"}:
        location = conn.execute(
            "SELECT location_key,kind FROM dispatch_tasks WHERE (run_id,id)=(%s,%s)", (run_id, task_id),
        ).fetchone()
        if location:
            lock_group(conn, run_id, location["location_key"], location["kind"])
    task = assignment_task(conn, run_id, task_id, draft.version, token)
    previous, stage = transitions[action]
    if task["stage"] not in previous:
        raise HTTPException(409, "Действие недоступно в текущем статусе")
    if action in {"finish", "return", "close", "followup"} and not draft.note:
        raise HTTPException(422, "Укажите результат или причину")
    if action == "accept":
        return record_receipt(conn, task, actor)
    if action == "followup" and task["work_kind"] != "inspect":
        raise HTTPException(409, "Уборку по итогам можно создать только из проверки")
    log_task(conn, run_id, task_id, actor, action, draft.note)
    conn.execute(
        """UPDATE dispatch_tasks SET stage=%s, assignment_version=assignment_version+1,
                 result=CASE WHEN %s='finish' THEN %s ELSE result END,
                 completed_at=CASE WHEN %s='finish' THEN now() WHEN %s='return' THEN NULL ELSE completed_at END,
                 closed_at=CASE WHEN %s='closed' THEN now() ELSE closed_at END
           WHERE (run_id,id)=(%s,%s)""",
        (stage, action, draft.note, action, action, stage, run_id, task_id),
    )
    if action == "close":
        conn.execute(
            """UPDATE dispatch_reports r SET closed_at=now(),closed_by=%s,resolution=%s
               FROM dispatch_task_reports s WHERE (s.run_id,s.report_id)=(r.run_id,r.id)
               AND (s.run_id,s.task_id)=(%s,%s) AND r.closed_at IS NULL""", (actor, draft.note, run_id, task_id),
        )
        event_changes = conn.execute(
            """UPDATE events e SET state='resolved',version=version+1 FROM dispatch_task_events s
               WHERE (e.run_id,e.id)=(s.run_id,s.event_id) AND (s.run_id,s.task_id)=(%s,%s)
                 AND e.state<>'resolved' RETURNING e.id""", (run_id, task_id),
        ).fetchall()
        for event in event_changes:
            conn.execute(
                "INSERT INTO actions (event_id,request_id,actor,state,note) VALUES (%s,%s,%s,'resolved',%s)",
                (event["id"], uuid4(), actor, draft.note[:500]),
            )
    if action == "followup":
        new_id = uuid4()
        conn.execute(
            """INSERT INTO dispatch_tasks (run_id,id,location_key,kind,district,address,work_kind,created_by,previous_task_id,instructions,severity)
               VALUES (%s,%s,%s,%s,%s,%s,'clear',%s,%s,%s,%s)""",
            (run_id, new_id, task["location_key"], task["kind"], task["district"], task["address"], actor, task_id,
             draft.note, task["severity"]),
        )
        conn.execute(
            """INSERT INTO dispatch_task_reports (run_id,task_id,report_id)
               SELECT s.run_id,%s,s.report_id FROM dispatch_task_reports s JOIN dispatch_reports r
               ON (r.run_id,r.id)=(s.run_id,s.report_id)
               WHERE (s.run_id,s.task_id)=(%s,%s) AND r.closed_at IS NULL""", (new_id, run_id, task_id),
        )
        conn.execute(
            """INSERT INTO dispatch_task_events (run_id,task_id,event_id)
               SELECT run_id,%s,event_id FROM dispatch_task_events WHERE (run_id,task_id)=(%s,%s)""", (new_id, run_id, task_id),
        )
        log_task(conn, run_id, new_id, actor, "created", draft.note)
    return {"stage": stage}


def task_history(conn, run_id, task_id):
    return conn.execute(
        """SELECT h.action,h.actor,h.note,h.assignee,h.due_at,h.created_at,t.work_kind
           FROM dispatch_task_history h
           JOIN dispatch_tasks t ON (t.run_id,t.id)=(h.run_id,h.task_id)
           JOIN dispatch_tasks selected ON (selected.run_id,selected.id)=(t.run_id,%s)
           WHERE h.run_id=%s AND (t.id=selected.id OR t.id=selected.previous_task_id)
           ORDER BY h.id DESC""", (task_id, run_id),
    ).fetchall()


def archive(conn, run_id):
    return conn.execute(
        """SELECT id,address,district,result,work_kind,closed_at,
                 (SELECT COALESCE(jsonb_agg(jsonb_build_object('reference',r.reference,'description',r.description,
                            'resolution',r.resolution,'closed_at',r.closed_at) ORDER BY r.created_at),'[]')
                  FROM dispatch_task_reports s JOIN dispatch_reports r ON (s.run_id,s.report_id)=(r.run_id,r.id)
                  WHERE (s.run_id,s.task_id)=(t.run_id,t.id)) AS reports
           FROM dispatch_tasks t WHERE run_id=%s AND closed_at IS NOT NULL ORDER BY closed_at DESC,id LIMIT 100""",
        (run_id,),
    ).fetchall()


def link_report(conn, run_id, report_id, task_id, actor):
    task = conn.execute(
        """SELECT district FROM dispatch_tasks WHERE (run_id,id)=(%s,%s)
           AND closed_at IS NULL FOR UPDATE""", (run_id, task_id),
    ).fetchone()
    report = conn.execute(
        """SELECT district,address,reference FROM dispatch_reports WHERE (run_id,id)=(%s,%s)
           AND closed_at IS NULL FOR UPDATE""", (run_id, report_id),
    ).fetchone()
    if not task or not report:
        raise HTTPException(404, "Открытое задание или обращение не найдено")
    if task["district"].casefold() != report["district"].casefold():
        raise HTTPException(409, "Обращение и задание должны относиться к одному району")
    existing = conn.execute(
        """SELECT s.task_id FROM dispatch_task_reports s JOIN dispatch_tasks t
           ON (t.run_id,t.id)=(s.run_id,s.task_id) WHERE (s.run_id,s.report_id)=(%s,%s)
           AND t.closed_at IS NULL""", (run_id, report_id),
    ).fetchone()
    if existing:
        if existing["task_id"] != task_id:
            raise HTTPException(409, "Обращение уже связано с другим открытым заданием")
    else:
        conn.execute("INSERT INTO dispatch_task_reports (run_id,task_id,report_id) VALUES (%s,%s,%s)",
                     (run_id, task_id, report_id))
        reference = " № " + report["reference"] if report["reference"] else ""
        log_task(conn, run_id, task_id, actor, "link_report", f"Обращение{reference} · {report['address']}")
    return {"task_id": task_id}


def locations(conn, run_id):
    return conn.execute(
        """SELECT DISTINCT location_key, district, address
           FROM dispatch_camera_locations WHERE run_id=%s ORDER BY district, address, location_key""",
        (run_id,),
    ).fetchall()


def register_report(conn, run_id, actor, draft):
    map_section = None
    if draft.location_key:
        location = conn.execute(
            """SELECT location_key,district,address FROM dispatch_camera_locations
               WHERE run_id=%s AND location_key=%s ORDER BY camera_id LIMIT 1""",
            (run_id, draft.location_key),
        ).fetchone()
        if location is None:
            raise HTTPException(404, "Участок не найден")
    elif draft.road_id or draft.endpoints:
        if draft.road_id:
            try:
                road = road_index().get(draft.road_id)
            except FileNotFoundError as exc:
                raise HTTPException(503, "Справочник дорог недоступен. Отметьте границы вручную") from exc
            if road is None:
                raise HTTPException(404, "Дорога не найдена")
            map_section = {"coordinates": road["coordinates"]}
            address = draft.address or road["name"]
            location_key = f"road:{road['id']}"
        else:
            coordinates = [list(point) for point in sorted(draft.endpoints)]
            map_section = {"coordinates": coordinates}
            address = draft.address or "Участок на карте"
            location_key = "map:" + sha256(json.dumps(coordinates).encode()).hexdigest()
        location = {"location_key": location_key, "district": draft.district, "address": address}
    else:
        normalized = [" ".join(value.casefold().split()) for value in (draft.district, draft.address)]
        address_key = sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()
        location = {"location_key": f"address:{address_key}", "district": draft.district, "address": draft.address}
    report = {**location, **draft.model_dump(exclude={"id", "location_key", "district", "address", "road_id", "endpoints"}),
              "created_by": actor}
    fields = ("location_key", "district", "address", "kind", "description", "channel", "reference", "severity",
              "created_by", "passage_obstructed")
    conn.execute(
        """INSERT INTO dispatch_reports
           (run_id,id,location_key,district,address,kind,description,channel,reference,severity,created_by,
            passage_obstructed,map_section)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id,id) DO NOTHING""",
        (run_id, draft.id, *(report[field] for field in fields), Jsonb(map_section) if map_section else None),
    )
    saved = conn.execute("SELECT * FROM dispatch_reports WHERE run_id=%s AND id=%s", (run_id, draft.id)).fetchone()
    if saved["map_section"] != map_section or any(saved[field] != report[field] for field in fields):
        raise HTTPException(409, "Номер регистрации уже использован")
    return {"id": saved["id"], "location_key": saved["location_key"], "kind": saved["kind"]}


def queue(conn, run_id, now, freshness):
    tasks = conn.execute(
        """SELECT id,location_key,kind,district,address,work_kind,created_at,assignee,due_at,stage,instructions,severity,
                  result,completed_at,assigned_at,accepted_at,response_seconds,assignment_version,
                  COALESCE(COALESCE(completed_at,%s) > due_at,false) AS overdue,
                  (SELECT note FROM dispatch_task_history h WHERE (h.run_id,h.task_id)=(t.run_id,t.id)
                   AND action='return' ORDER BY id DESC LIMIT 1) AS return_note
           FROM dispatch_tasks t WHERE run_id=%s AND closed_at IS NULL""", (now, run_id),
    ).fetchall()
    groups = {
        (task["location_key"], task["kind"]): {
            "location_key": task["location_key"], "kind": task["kind"], "district": task["district"],
            "address": task["address"], "task": task, "signals": [], "reports": [],
            "severity": task["severity"], "last_seen": task["created_at"], "road_priority": 0,
        }
        for task in tasks
    }
    signals = conn.execute(
        """WITH attached AS (
               SELECT s.event_id,t.id AS task_id FROM dispatch_task_events s
               JOIN dispatch_tasks t ON (t.run_id,t.id)=(s.run_id,s.task_id)
               WHERE s.run_id=%s AND t.closed_at IS NULL
           ), candidates AS (
               SELECT e.id FROM events e
               JOIN dispatch_camera_locations l ON (l.run_id,l.camera_id)=(e.run_id,e.camera_id)
               WHERE e.run_id=%s AND e.ended_at IS NULL AND e.state IN ('new','acknowledged','en_route')
               UNION SELECT event_id FROM attached
           )
           SELECT e.id,e.kind,e.severity,e.reason,e.last_seen,e.latest_frame AS frame_id,
                  c.name,c.road_priority,c.road_priority_reason,COALESCE(l.location_key,'camera:' || c.id) AS location_key,
                  COALESCE(l.district,c.district,c.area) AS district,COALESCE(l.address,c.name) AS address,attached.task_id,
                  (l.location_key IS NOT NULL AND e.ended_at IS NULL AND e.state IN ('new','acknowledged','en_route') AND (
                    NOT %s OR e.last_seen >= %s - GREATEST(%s,2*COALESCE(c.interval_seconds,0))*interval '1 second'
                  )) AS active
           FROM candidates JOIN events e ON e.id=candidates.id
           JOIN cameras c ON (c.run_id,c.id)=(e.run_id,e.camera_id)
           LEFT JOIN dispatch_camera_locations l ON (l.run_id,l.camera_id)=(e.run_id,e.camera_id)
           LEFT JOIN attached ON attached.event_id=e.id
           ORDER BY e.last_seen DESC, e.id""",
        (run_id, run_id, run_id in {"live", "preview"}, now, freshness),
    ).fetchall()
    reports = conn.execute(
        """WITH attached AS (
               SELECT s.report_id,t.id AS task_id FROM dispatch_task_reports s
               JOIN dispatch_tasks t ON (t.run_id,t.id)=(s.run_id,s.task_id)
               WHERE s.run_id=%s AND t.closed_at IS NULL
           ), candidates AS (
               SELECT id FROM dispatch_reports WHERE run_id=%s AND closed_at IS NULL
               UNION SELECT report_id FROM attached
           )
           SELECT r.id,r.kind,r.location_key,r.district,r.address,r.description,r.channel,r.reference,r.severity,r.map_section,
                  r.created_at AS last_seen,r.closed_at IS NULL AS active,attached.task_id,0 AS road_priority
           FROM candidates JOIN dispatch_reports r ON r.run_id=%s AND r.id=candidates.id
           LEFT JOIN attached ON attached.report_id=r.id
           ORDER BY r.created_at DESC, r.id""", (run_id, run_id, run_id),
    ).fetchall()
    by_task = {row["task"]["id"]: row for row in groups.values()}
    for source_type, sources in (("signals", signals), ("reports", reports)):
        for source in sources:
            task_id = source.pop("task_id")
            if not source["active"] and task_id is None:
                continue
            key = (source.pop("location_key"), source.pop("kind"))
            district, address = source.pop("district"), source.pop("address")
            if source_type == "reports":
                source["address"] = address
            group = by_task.get(task_id)
            source["linked"] = group is not None
            if group is None:
                group = groups.setdefault(key, {
                    "location_key": key[0], "kind": key[1], "district": district, "address": address,
                    "severity": "watch", "last_seen": source["last_seen"], "road_priority": 0,
                    "task": None, "signals": [], "reports": [],
                })
            if source["active"]:
                if source["severity"] == "urgent":
                    group["severity"] = "urgent"
                group["last_seen"] = max(group["last_seen"], source["last_seen"])
                group["road_priority"] = max(group["road_priority"], source["road_priority"])
            group[source_type].append(source)
    return sorted(groups.values(), key=lambda row: (
        row["severity"] == "urgent", row["road_priority"], row["last_seen"], row["location_key"], row["kind"],
    ), reverse=True)


def lock_group(conn, run_id, location_key, kind):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (json.dumps([run_id, location_key, kind]),))


def save_task(conn, run_id, actor, draft, now, freshness):
    lock_group(conn, run_id, draft.location_key, draft.kind)
    group = next((row for row in queue(conn, run_id, now, freshness)
                  if (row["location_key"], row["kind"]) == (draft.location_key, draft.kind)), None)
    if group is None:
        raise HTTPException(404, "Источники по участку не найдены. Обновите очередь")
    key = (run_id, draft.location_key, draft.kind)
    if draft.work_kind:
        created = conn.execute(
            """INSERT INTO dispatch_tasks (run_id,location_key,kind,id,district,address,work_kind,created_by,severity)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (run_id,location_key,kind) WHERE closed_at IS NULL DO NOTHING RETURNING id""",
            (*key, uuid4(), group["district"], group["address"], draft.work_kind, actor, group["severity"]),
        ).fetchone()
        if created:
            log_task(conn, run_id, created["id"], actor, "created")
    task = conn.execute(
        """SELECT id,work_kind FROM dispatch_tasks WHERE (run_id,location_key,kind)=(%s,%s,%s)
           AND closed_at IS NULL FOR UPDATE""", key,
    ).fetchone()
    if task is None or (draft.work_kind and task["work_kind"] != draft.work_kind):
        raise HTTPException(409, "Задание изменилось. Обновите очередь")
    conn.execute(
        """INSERT INTO dispatch_task_events (run_id,task_id,event_id)
           SELECT %s,%s,unnest(%s::uuid[]) ON CONFLICT DO NOTHING""",
        (run_id, task["id"], [signal["id"] for signal in group["signals"] if signal["active"]]),
    )
    for report in sorted(group["reports"], key=lambda item: item["id"]):
        if report["active"] and not report["linked"]:
            link_report(conn, run_id, report["id"], task["id"], actor)
    return task

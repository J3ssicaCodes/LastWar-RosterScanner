"""Alliance-scoped leadership wall, inbox, and recurring work."""

from datetime import date, datetime, time, timedelta, timezone
import json
import re
from threading import Event, Thread
from zoneinfo import ZoneInfo

from dateutil import rrule, tz
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from alliance_auth import audit, csrf_check, require_account, require_leader, workspace_by_slug
from alliance_claims import notify


LEADERSHIP_SCHEMA=(
    """CREATE TABLE IF NOT EXISTS alliance_wall_posts (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, account_id BIGINT UNSIGNED NOT NULL,
        body TEXT NOT NULL, pinned BOOLEAN NOT NULL DEFAULT FALSE,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_wall (alliance_id,pinned,created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_wall_replies (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, post_id BIGINT UNSIGNED NOT NULL,
        account_id BIGINT UNSIGNED NOT NULL, body TEXT NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_wall_reply (alliance_id,post_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_tasks (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, creator_id BIGINT UNSIGNED NOT NULL,
        assignee_id BIGINT UNSIGNED NOT NULL, title VARCHAR(160) NOT NULL,
        description VARCHAR(1000) NOT NULL DEFAULT '', schedule_json JSON NOT NULL,
        timezone VARCHAR(80) NOT NULL, completion_rule ENUM('manual','vs_saved') NOT NULL DEFAULT 'manual',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_task_alliance (alliance_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_task_occurrences (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, task_id BIGINT UNSIGNED NOT NULL,
        assignee_id BIGINT UNSIGNED NOT NULL, local_date DATE NOT NULL, due_at DATETIME(6) NOT NULL,
        status ENUM('pending','done','skipped') NOT NULL DEFAULT 'pending',
        completed_by BIGINT UNSIGNED NULL, completed_at DATETIME(6) NULL,
        due_notified BOOLEAN NOT NULL DEFAULT FALSE,
        UNIQUE KEY uq_task_date (task_id,local_date),
        KEY idx_occurrence_assignee (alliance_id,assignee_id,status,due_at)
    ) ENGINE=InnoDB""",
)


class WallBody(BaseModel):
    body: str = Field(min_length=1,max_length=4000)


class PinBody(BaseModel):
    pinned: bool


class Schedule(BaseModel):
    kind: str = Field(pattern=r"^(once|daily|weekly|monthly|interval)$")
    start_date: date
    due_time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    interval: int = Field(default=1,ge=1,le=365)
    interval_unit: str = Field(default="day",pattern=r"^(day|week|month)$")
    weekdays: list[int] = Field(default_factory=list,max_length=7)
    monthly_mode: str = Field(default="date",pattern=r"^(date|weekday)$")
    month_day: int = Field(default=1,ge=1,le=31)
    month_week: int = Field(default=1,ge=-1,le=5)
    month_weekday: int = Field(default=0,ge=0,le=6)
    end_date: date | None = None


class TaskBody(BaseModel):
    title: str = Field(min_length=1,max_length=160)
    description: str = Field(default="",max_length=1000)
    assignee: str = Field(min_length=3,max_length=40)
    completion_rule: str = Field(default="manual",pattern=r"^(manual|vs_saved)$")
    schedule: Schedule


class OccurrenceUpdate(BaseModel):
    action: str = Field(pattern=r"^(done|skip|reassign|reopen)$")
    assignee: str = Field(default="",max_length=40)


def schedule_dates(schedule, timezone_name, start_utc, end_utc):
    zone=tz.gettz(timezone_name)
    if not zone:
        raise ValueError("Choose a valid alliance time zone.")
    start_date=date.fromisoformat(schedule["start_date"])
    hour,minute=map(int,schedule["due_time"].split(":"))
    start=datetime.combine(start_date,time(hour,minute),tzinfo=zone)
    if schedule.get("end_date") and date.fromisoformat(schedule["end_date"])<start_date:
        raise ValueError("The end date must follow the start date.")
    if schedule["kind"]=="once":
        candidates=[start]
    else:
        freq={"daily":rrule.DAILY,"weekly":rrule.WEEKLY,"monthly":rrule.MONTHLY}
        kind=schedule["kind"]
        if kind=="interval":
            kind={"day":"daily","week":"weekly","month":"monthly"}[schedule["interval_unit"]]
        options={"freq":freq[kind],"dtstart":start,"interval":schedule.get("interval",1)}
        if kind=="weekly":
            weekdays=schedule.get("weekdays") or [start.weekday()]
            if any(day not in range(7) for day in weekdays):
                raise ValueError("Choose valid weekdays.")
            options["byweekday"]=[rrule.weekday(day) for day in sorted(set(weekdays))]
        if kind=="monthly" and schedule["kind"]=="monthly":
            if schedule["monthly_mode"]=="date":
                options["bymonthday"]=schedule["month_day"]
            else:
                if schedule["month_week"]==0:
                    raise ValueError("Choose a week in the month.")
                options["byweekday"]=rrule.weekday(schedule["month_weekday"],schedule["month_week"])
        if schedule.get("end_date"):
            end=date.fromisoformat(schedule["end_date"])
            options["until"]=datetime.combine(end,time(23,59,59),tzinfo=zone)
        candidates=rrule.rrule(**options).between(start_utc.astimezone(zone),end_utc.astimezone(zone),inc=True)
    for item in candidates:
        if not tz.datetime_exists(item):
            item=tz.resolve_imaginary(item)
        utc=item.astimezone(timezone.utc)
        if start_utc<=utc<=end_utc:
            yield item.date(),utc.replace(tzinfo=None)


def materialize(cursor, task, now, assigned_at=None):
    schedule=task["schedule_json"]
    if isinstance(schedule,str):schedule=json.loads(schedule)
    start=now-timedelta(days=14)
    if assigned_at:
        if assigned_at.tzinfo is None:assigned_at=assigned_at.replace(tzinfo=timezone.utc)
        zone=tz.gettz(task['timezone'])
        local_day=assigned_at.astimezone(zone).date()
        start=max(start,datetime.combine(local_day,time.min,tzinfo=zone).astimezone(timezone.utc))
    end=now+timedelta(days=90)
    rows=[(task["alliance_id"],task["id"],task["assignee_id"],local_date,due_at)
          for local_date,due_at in schedule_dates(schedule,task["timezone"],start,end)]
    if rows:
        cursor.executemany("""INSERT IGNORE INTO alliance_task_occurrences
            (alliance_id,task_id,assignee_id,local_date,due_at) VALUES (%s,%s,%s,%s,%s)""",rows)


def prepare_occurrences(cursor, alliance_id):
    now=datetime.now(timezone.utc)
    from alliance_operations import reconcile_operations
    reconcile_operations(cursor,alliance_id)
    cursor.execute("""SELECT t.*,l.assigned_at AS operation_assigned_at FROM alliance_tasks t
        LEFT JOIN alliance_operation_task_links l ON l.task_id=t.id AND l.alliance_id=t.alliance_id
        LEFT JOIN alliance_memberships m ON m.alliance_id=t.alliance_id AND m.account_id=t.assignee_id
        WHERE t.alliance_id=%s AND (l.task_id IS NULL OR (l.active=TRUE AND m.role IN ('R4','R5')))""",(alliance_id,))
    for task in cursor.fetchall():
        materialize(cursor,task,now,assigned_at=task.get('operation_assigned_at'))
    cursor.execute("""SELECT o.id,o.assignee_id,o.task_id,w.slug FROM alliance_task_occurrences o
        JOIN alliance_workspaces w ON w.id=o.alliance_id
        WHERE o.alliance_id=%s AND o.status='pending' AND o.due_notified=FALSE
          AND o.due_at BETWEEN UTC_TIMESTAMP(6) AND UTC_TIMESTAMP(6)+INTERVAL 1 HOUR FOR UPDATE""",
        (alliance_id,))
    for occurrence in cursor.fetchall():
        notify(cursor,alliance_id,occurrence["assignee_id"],"task","A task is due soon",
               "/account?alliance="+occurrence["slug"]+"&task="+str(occurrence["id"]))
        cursor.execute("UPDATE alliance_task_occurrences SET due_notified=TRUE WHERE id=%s",(occurrence["id"],))
    cursor.execute("""SELECT o.id,o.local_date,t.completion_rule FROM alliance_task_occurrences o
        JOIN alliance_tasks t ON t.id=o.task_id AND t.alliance_id=o.alliance_id
        WHERE o.alliance_id=%s AND o.status='pending' AND t.completion_rule='vs_saved'
          AND EXISTS(SELECT 1 FROM workspace_vs_points v JOIN workspace_media_scan_jobs j
          ON j.id=v.source_job_id AND j.alliance_id=v.alliance_id
          WHERE v.alliance_id=o.alliance_id AND v.score_date=o.local_date
          AND j.status IN ('ready','approved')) FOR UPDATE""",(alliance_id,))
    for occurrence in cursor.fetchall():
        cursor.execute("""UPDATE alliance_task_occurrences SET status='done',
            completed_at=UTC_TIMESTAMP(6) WHERE id=%s AND status='pending'""",(occurrence["id"],))
        audit(cursor,alliance_id,None,"task_auto_completed",{"occurrence_id":occurrence["id"]})


def install_leadership(app,connect_db):
    router=APIRouter(prefix="/api/auth/alliances/{slug}")
    stop=Event()

    @app.on_event("startup")
    def start_reminders():
        def run():
            while not stop.wait(45):
                try:
                    with connect_db() as conn, conn.cursor() as cur:
                        cur.execute("SELECT GET_LOCK('alliance_reminder_tick',0) AS acquired")
                        if not cur.fetchone()["acquired"]:
                            continue
                        try:
                            cur.execute("SELECT id FROM alliance_workspaces")
                            for workspace in cur.fetchall():
                                prepare_occurrences(cur,workspace["id"])
                            from alliance_push import dispatch_push
                            dispatch_push(cur)
                            conn.commit()
                        finally:
                            cur.execute("SELECT RELEASE_LOCK('alliance_reminder_tick')")
                except Exception:
                    import logging
                    logging.exception("Leadership reminder processing failed.")
        stop.clear()
        Thread(target=run,name="alliance-reminders",daemon=True).start()

    @app.on_event("shutdown")
    def stop_reminders():
        stop.set()

    def leader(request,slug,cur):
        actor=require_account(connect_db,request)
        workspace=workspace_by_slug(cur,slug)
        require_leader(cur,workspace["id"],actor["id"])
        return actor,workspace

    @router.get("/leaders")
    def leaders(slug: str,request: Request):
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("""SELECT a.username,a.display_name,m.role FROM alliance_memberships m
                JOIN alliance_accounts a ON a.id=m.account_id WHERE m.alliance_id=%s ORDER BY a.display_name""",
                (workspace["id"],))
            return {"leaders":cur.fetchall()}

    @router.get("/wall")
    def wall(slug: str,request: Request):
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("""SELECT p.id,p.body,p.pinned,p.created_at,a.display_name
                FROM alliance_wall_posts p JOIN alliance_accounts a ON a.id=p.account_id
                WHERE p.alliance_id=%s ORDER BY p.pinned DESC,p.created_at DESC LIMIT 200""",(workspace["id"],))
            posts=cur.fetchall()
            cur.execute("""SELECT r.post_id,r.body,r.created_at,a.display_name
                FROM alliance_wall_replies r JOIN alliance_accounts a ON a.id=r.account_id
                WHERE r.alliance_id=%s ORDER BY r.created_at""",(workspace["id"],))
            return {"posts":posts,"replies":cur.fetchall()}

    @router.post("/wall")
    def post_wall(slug: str,body: WallBody,request: Request):
        csrf_check(request)
        text=body.body.strip()
        if not text:raise HTTPException(422,"Write a post.")
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("INSERT INTO alliance_wall_posts (alliance_id,account_id,body) VALUES (%s,%s,%s)",
                        (workspace["id"],actor["id"],text))
            post_id=cur.lastrowid
            audit(cur,workspace["id"],actor,"wall_posted",{"post_id":post_id})
            conn.commit()
        return {"id":post_id}

    @router.post("/wall/{post_id}/replies")
    def reply_wall(slug: str,post_id: int,body: WallBody,request: Request):
        csrf_check(request)
        text=body.body.strip()
        if not text:raise HTTPException(422,"Write a reply.")
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("SELECT id FROM alliance_wall_posts WHERE id=%s AND alliance_id=%s",(post_id,workspace["id"]))
            if not cur.fetchone():raise HTTPException(404,"Post not found.")
            cur.execute("INSERT INTO alliance_wall_replies (alliance_id,post_id,account_id,body) VALUES (%s,%s,%s,%s)",
                        (workspace["id"],post_id,actor["id"],text))
            audit(cur,workspace["id"],actor,"wall_replied",{"post_id":post_id})
            conn.commit()
        return {"ok":True}

    @router.put("/wall/{post_id}/pin")
    def pin_wall(slug: str,post_id: int,body: PinBody,request: Request):
        csrf_check(request)
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("UPDATE alliance_wall_posts SET pinned=%s WHERE alliance_id=%s AND id=%s",
                        (body.pinned,workspace["id"],post_id))
            if not cur.rowcount:raise HTTPException(404,"Post not found or already set.")
            audit(cur,workspace["id"],actor,"wall_pin_changed",{"post_id":post_id,"pinned":body.pinned})
            conn.commit()
        return {"ok":True}

    @router.get("/tasks")
    def tasks(slug: str,request: Request,mine: bool=False):
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            prepare_occurrences(cur,workspace["id"])
            sql="""SELECT o.id,o.task_id,o.local_date,o.due_at,o.status,o.assignee_id,t.title,
                t.description,t.completion_rule,a.display_name AS assignee FROM alliance_task_occurrences o
                JOIN alliance_tasks t ON t.id=o.task_id AND t.alliance_id=o.alliance_id
                JOIN alliance_accounts a ON a.id=o.assignee_id
                WHERE o.alliance_id=%s"""
            values=[workspace["id"]]
            if mine:
                sql+=" AND o.assignee_id=%s"
                values.append(actor["id"])
            cur.execute(sql+" ORDER BY o.due_at LIMIT 300",tuple(values))
            rows=cur.fetchall()
            conn.commit()
        return {"occurrences":rows}

    @router.post("/tasks")
    def create_task(slug: str,body: TaskBody,request: Request):
        csrf_check(request)
        title=body.title.strip()
        if not title:raise HTTPException(422,"Enter a task.")
        schedule=body.schedule.model_dump(mode="json")
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("""SELECT a.id FROM alliance_accounts a JOIN alliance_memberships m ON m.account_id=a.id
                WHERE m.alliance_id=%s AND a.username=%s""",(workspace["id"],body.assignee.lower()))
            assigned=cur.fetchone()
            if not assigned:raise HTTPException(422,"Choose a leader in this alliance.")
            cur.execute("SELECT timezone FROM alliance_workspaces WHERE id=%s",(workspace["id"],))
            timezone_name=cur.fetchone()["timezone"]
            try:
                list(schedule_dates(schedule,timezone_name,datetime.now(timezone.utc)-timedelta(days=14),
                                    datetime.now(timezone.utc)+timedelta(days=90)))
            except (ValueError,OverflowError) as exc:raise HTTPException(422,str(exc)) from exc
            cur.execute("""INSERT INTO alliance_tasks
                (alliance_id,creator_id,assignee_id,title,description,schedule_json,timezone,completion_rule)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (workspace["id"],actor["id"],assigned["id"],title,body.description.strip(),
                 json.dumps(schedule),timezone_name,body.completion_rule))
            task_id=cur.lastrowid
            materialize(cur,{"id":task_id,"alliance_id":workspace["id"],"assignee_id":assigned["id"],
                            "schedule_json":schedule,"timezone":timezone_name},datetime.now(timezone.utc))
            cur.execute("""SELECT id FROM alliance_task_occurrences WHERE task_id=%s AND alliance_id=%s
                ORDER BY due_at LIMIT 1""",(task_id,workspace["id"]))
            first=cur.fetchone()
            notify(cur,workspace["id"],assigned["id"],"task","A task was assigned to you",
                   "/account?alliance="+slug+("&task="+str(first["id"]) if first else ""))
            audit(cur,workspace["id"],actor,"task_created",{"task_id":task_id,"assignee_id":assigned["id"]})
            conn.commit()
        return {"id":task_id}

    @router.post("/tasks/{occurrence_id}")
    def update_task(slug: str,occurrence_id: int,body: OccurrenceUpdate,request: Request):
        csrf_check(request)
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("""SELECT status,assignee_id FROM alliance_task_occurrences
                WHERE id=%s AND alliance_id=%s FOR UPDATE""",(occurrence_id,workspace["id"]))
            occurrence=cur.fetchone()
            if not occurrence:raise HTTPException(404,"Task occurrence not found.")
            if body.action=="reassign":
                cur.execute("""SELECT a.id FROM alliance_accounts a JOIN alliance_memberships m ON m.account_id=a.id
                    WHERE m.alliance_id=%s AND a.username=%s""",(workspace["id"],body.assignee.lower()))
                target=cur.fetchone()
                if not target:raise HTTPException(422,"Choose a leader in this alliance.")
                cur.execute("""UPDATE alliance_task_occurrences SET assignee_id=%s,due_notified=FALSE
                    WHERE id=%s AND alliance_id=%s""",(target["id"],occurrence_id,workspace["id"]))
                notify(cur,workspace["id"],target["id"],"task","A task was assigned to you",
                       "/account?alliance="+slug+"&task="+str(occurrence_id))
            else:
                status={"done":"done","skip":"skipped","reopen":"pending"}[body.action]
                cur.execute("""UPDATE alliance_task_occurrences SET status=%s,completed_by=%s,
                    completed_at=IF(%s='pending',NULL,UTC_TIMESTAMP(6)) WHERE id=%s AND alliance_id=%s""",
                    (status,None if status=="pending" else actor["id"],status,occurrence_id,workspace["id"]))
            audit(cur,workspace["id"],actor,"task_"+body.action,{"occurrence_id":occurrence_id})
            conn.commit()
        return {"ok":True}

    @router.get("/inbox")
    def inbox(slug: str,request: Request):
        with connect_db() as conn, conn.cursor() as cur:
            actor,workspace=leader(request,slug,cur)
            cur.execute("""SELECT c.id,p.player_name FROM alliance_claims c
                JOIN workspace_players p ON p.id=c.player_id AND p.alliance_id=c.alliance_id
                WHERE c.alliance_id=%s AND c.status='pending' ORDER BY c.created_at""",(workspace["id"],))
            claims=cur.fetchall()
            cur.execute("""SELECT j.id,j.destination,j.status,j.message,
                (SELECT COUNT(*) FROM workspace_roster_reviews r WHERE r.alliance_id=j.alliance_id AND r.job_id=j.id)+
                (SELECT COUNT(*) FROM workspace_vs_scan_rows v WHERE v.alliance_id=j.alliance_id AND v.job_id=j.id)+
                (SELECT COUNT(*) FROM workspace_media_missing_players m JOIN workspace_players p
                  ON p.id=m.player_id AND p.alliance_id=m.alliance_id
                  WHERE m.alliance_id=j.alliance_id AND m.job_id=j.id AND p.archived=m.reactivate
                    AND (m.reactivate=TRUE OR p.last_import_id=m.last_import_id COLLATE utf8mb4_unicode_ci
                         OR (p.last_import_id IS NULL AND m.last_import_id IS NULL))) AS pending
                FROM workspace_media_scan_jobs j WHERE j.alliance_id=%s
                AND (j.status IN ('error','interrupted','review') OR
                     EXISTS(SELECT 1 FROM workspace_roster_reviews r WHERE r.alliance_id=j.alliance_id AND r.job_id=j.id) OR
                     EXISTS(SELECT 1 FROM workspace_vs_scan_rows v WHERE v.alliance_id=j.alliance_id AND v.job_id=j.id) OR
                     EXISTS(SELECT 1 FROM workspace_media_missing_players m JOIN workspace_players p
                       ON p.id=m.player_id AND p.alliance_id=m.alliance_id
                       WHERE m.alliance_id=j.alliance_id AND m.job_id=j.id AND p.archived=m.reactivate
                         AND (m.reactivate=TRUE OR p.last_import_id=m.last_import_id COLLATE utf8mb4_unicode_ci
                              OR (p.last_import_id IS NULL AND m.last_import_id IS NULL))))
                ORDER BY j.created_at DESC""",(workspace["id"],))
            jobs=[row for row in cur.fetchall() if row["pending"] or row["status"] in ("error","interrupted")]
            cur.execute("""SELECT assignment_date,conductor_name FROM workspace_train_assignments
                WHERE alliance_id=%s AND vip_method='dice' AND vip_id IS NULL ORDER BY assignment_date DESC""",
                (workspace["id"],))
            return {"claims":claims,"jobs":jobs,"trains":cur.fetchall()}

    app.include_router(router)

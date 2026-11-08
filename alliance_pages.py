"""Public alliance pages and explicitly filtered viewer data."""

from datetime import date, timedelta
import json
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from alliance_auth import require_account, require_leader, workspace_by_slug, csrf_check


BASE = Path(__file__).parent
PUBLIC_ROSTER_FIELDS = {"rank","power","hq_level","kills","donations"}


def install_alliance_pages(app, connect_db):
    @app.get("/a/{slug}")
    def viewer_page(slug: str):
        with connect_db() as conn, conn.cursor() as cur:
            workspace_by_slug(cur,slug)
        return FileResponse(BASE/"alliance_viewer.html",headers={"Cache-Control":"no-store",
            "Referrer-Policy":"no-referrer"})

    @app.get("/a/{slug}/manage")
    def management_page(slug: str):
        return FileResponse(BASE/"index.html",headers={"Cache-Control":"no-store",
            "Referrer-Policy":"no-referrer"})

    @app.get("/account")
    @app.get("/join/{code}")
    @app.get("/new-alliance/{code}")
    @app.get("/claim/{code}")
    def account_page():
        return FileResponse(BASE/"alliance_account.html",headers={"Cache-Control":"no-store",
            "Referrer-Policy":"no-referrer"})

    @app.get("/alliance_ui.js")
    def viewer_script():
        return FileResponse(BASE/"alliance_ui.js",media_type="application/javascript",headers={"Cache-Control":"no-cache"})

    @app.get("/alliance_account.js")
    def account_script():
        return FileResponse(BASE/"alliance_account.js",media_type="application/javascript",headers={"Cache-Control":"no-cache"})

    @app.get("/a/{slug}/manifest.webmanifest")
    def manifest(slug: str):
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
        return JSONResponse({"id":"/a/"+slug,"name":workspace["name"]+" Alliance Tools",
            "short_name":workspace["tag"]+" Tools","start_url":"/a/"+slug,"scope":"/",
            "display":"standalone","background_color":"#111326","theme_color":"#17142c",
            "icons":[{"src":"/alliance-icon-192.png?v=2","sizes":"192x192","type":"image/png"},
                     {"src":"/alliance-icon-512.png?v=2","sizes":"512x512","type":"image/png"}]},
            media_type="application/manifest+json")

    @app.get("/service-worker.js")
    def service_worker():
        return FileResponse(BASE/"service-worker.js",media_type="application/javascript",headers={"Cache-Control":"no-cache"})

    @app.get("/alliance-icon-{size}.png")
    def icon(size: int):
        if size not in (192,512):
            raise HTTPException(404,"Icon not found.")
        return FileResponse(BASE/f"alliance-icon-{size}.png",media_type="image/png")

    @app.get("/api/public/{slug}")
    def public_viewer(slug: str, week_start: date | None = None):
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            cur.execute("SELECT MAX(score_date) AS latest FROM workspace_vs_points WHERE alliance_id=%s",
                        (workspace["id"],))
            latest=cur.fetchone()["latest"]
            chosen=week_start or latest or date.today()
            monday=chosen-timedelta(days=chosen.weekday())
            saturday=monday+timedelta(days=5)
            cur.execute("""SELECT v.score_date,v.player_name,v.points FROM workspace_vs_points v
                LEFT JOIN workspace_players p ON p.alliance_id=v.alliance_id AND p.id=v.player_id
                WHERE v.alliance_id=%s AND v.score_date BETWEEN %s AND %s
                AND COALESCE(p.archived,FALSE)=FALSE ORDER BY v.player_name,v.score_date""",
                (workspace["id"],monday,saturday))
            scores=cur.fetchall()
            cur.execute("""SELECT assignment_date,conductor_name,vip_name,vip_id,vip_method
                FROM workspace_train_assignments WHERE alliance_id=%s
                ORDER BY assignment_date DESC LIMIT 30""",(workspace["id"],))
            trains=cur.fetchall()
            cur.execute("SELECT viewer_roster_fields FROM alliance_workspaces WHERE id=%s",(workspace["id"],))
            selected=cur.fetchone()["viewer_roster_fields"]
            if isinstance(selected,str):
                selected=json.loads(selected)
            selected=set(selected or []) & PUBLIC_ROSTER_FIELDS
            roster=[]
            if selected:
                selected_columns={"rank":"player_rank","power":"power","hq_level":"hq_level",
                                  "kills":"kills","donations":"donations"}
                cols=", ".join(f"`{selected_columns[name]}` AS `{name}`" for name in sorted(selected))
                cur.execute(f"""SELECT player_name AS name,{cols} FROM workspace_players
                    WHERE alliance_id=%s AND archived=FALSE ORDER BY player_name""",(workspace["id"],))
                roster=cur.fetchall()
        return JSONResponse({"alliance":{"slug":workspace["slug"],"name":workspace["name"],
            "tag":workspace["tag"],"server":workspace["server_number"]},
            "week_start":monday.isoformat(),
            "scores":[{"name":r["player_name"],"date":r["score_date"].isoformat(),"points":r["points"]} for r in scores],
            "trains":[{"date":r["assignment_date"].isoformat(),"conductor":r["conductor_name"],
                "vip":r["vip_name"] if r["vip_id"] else "Pending dice roll"} for r in trains],
            "roster":roster,"roster_fields":sorted(selected)},headers={"Cache-Control":"no-store"})

    @app.get("/api/auth/alliances/{slug}/visibility")
    def get_visibility(slug: str, request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"],r5=True)
            cur.execute("SELECT viewer_roster_fields,timezone FROM alliance_workspaces WHERE id=%s",(workspace["id"],))
            row=cur.fetchone()
            fields=row["viewer_roster_fields"]
            return {"fields":json.loads(fields) if isinstance(fields,str) else fields or [],"timezone":row["timezone"]}

    @app.put("/api/auth/alliances/{slug}/visibility")
    async def set_visibility(slug: str, request: Request):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        from alliance_auth import audit
        csrf_check(request)
        actor=require_account(connect_db,request)
        body=await request.json()
        fields=body.get("fields")
        timezone=body.get("timezone","UTC")
        if not isinstance(fields,list) or any(field not in PUBLIC_ROSTER_FIELDS for field in fields):
            raise HTTPException(422,"Choose valid roster fields.")
        try:
            ZoneInfo(timezone)
        except (TypeError,ValueError,ZoneInfoNotFoundError) as exc:
            raise HTTPException(422,"Choose a valid time zone.") from exc
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"],r5=True)
            cur.execute("UPDATE alliance_workspaces SET viewer_roster_fields=%s,timezone=%s WHERE id=%s",
                        (json.dumps(sorted(set(fields))),timezone,workspace["id"]))
            audit(cur,workspace["id"],actor,"visibility_changed",{"fields":sorted(set(fields)),"timezone":timezone})
            conn.commit()
        return {"ok":True}

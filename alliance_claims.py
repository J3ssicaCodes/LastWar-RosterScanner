"""Private player profile claims and per-claim leadership discussion."""

from datetime import datetime
from io import BytesIO
import re

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from pymysql.err import IntegrityError

from alliance_auth import _invite, audit, csrf_check, require_account, require_leader, workspace_by_slug


CLAIM_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS alliance_claims (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, account_id BIGINT UNSIGNED NOT NULL,
        player_id BIGINT UNSIGNED NOT NULL, invite_id BIGINT UNSIGNED NOT NULL,
        birthday CHAR(5) NOT NULL, proof MEDIUMBLOB NOT NULL, proof_mime VARCHAR(30) NOT NULL,
        note VARCHAR(500) NOT NULL DEFAULT '',
        status ENUM('pending','more_proof','approved','rejected') NOT NULL DEFAULT 'pending',
        handled_by BIGINT UNSIGNED NULL, handled_at DATETIME(6) NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_claim_alliance_status (alliance_id,status), KEY idx_claim_player (alliance_id,player_id),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_player_claims (
        alliance_id BIGINT UNSIGNED NOT NULL, player_id BIGINT UNSIGNED NOT NULL,
        account_id BIGINT UNSIGNED NOT NULL, approved_claim_id BIGINT UNSIGNED NOT NULL,
        PRIMARY KEY (alliance_id,player_id), UNIQUE KEY uq_claim_account (alliance_id,account_id),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_claim_comments (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, claim_id BIGINT UNSIGNED NOT NULL,
        account_id BIGINT UNSIGNED NOT NULL, body VARCHAR(1000) NOT NULL,
        visibility ENUM('leadership','claimant') NOT NULL DEFAULT 'leadership',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_claim_comments (alliance_id,claim_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_notifications (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, account_id BIGINT UNSIGNED NOT NULL,
        kind VARCHAR(40) NOT NULL, title VARCHAR(160) NOT NULL, target VARCHAR(240) NOT NULL,
        read_at DATETIME(6) NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_notification_user (account_id,read_at,created_at),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


class ClaimCode(BaseModel):
    code: str = Field(min_length=16,max_length=128)


class Decision(BaseModel):
    action: str = Field(pattern=r"^(approve|more_proof|reject)$")
    message: str = Field(default="",max_length=1000)
    confirm_conflicts: bool = False
    replace_existing: bool = False


class Comment(BaseModel):
    body: str = Field(min_length=1,max_length=1000)


class Birthday(BaseModel):
    alliance_slug: str = Field(max_length=40)
    birthday: str = Field(max_length=5)


def birthday_value(value):
    if not re.fullmatch(r"\d{2}-\d{2}",value):
        raise HTTPException(422,"Use MM-DD for the birthday.")
    try:
        datetime.strptime("2000-"+value,"%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(422,"Enter a valid month and day.") from exc
    return value


def notify(cursor, alliance_id, account_id, kind, title, target):
    cursor.execute("""INSERT INTO alliance_notifications (alliance_id,account_id,kind,title,target)
        VALUES (%s,%s,%s,%s,%s)""",(alliance_id,account_id,kind,title,target))


def notify_leaders(cursor, alliance_id, kind, title, target):
    cursor.execute("SELECT account_id FROM alliance_memberships WHERE alliance_id=%s AND role IN ('R4','R5')",
                   (alliance_id,))
    for row in cursor.fetchall():
        notify(cursor,alliance_id,row["account_id"],kind,title,target)


def evidence_bytes(file):
    data=file.read(8*1024*1024+1)
    if not data or len(data)>8*1024*1024:
        raise HTTPException(413,"Choose a screenshot smaller than 8 MB.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ("PNG","JPEG","WEBP","GIF") or image.width*image.height>25000000:
                raise HTTPException(422,"Choose a PNG, JPEG, WebP, or GIF screenshot.")
            mime=Image.MIME[image.format]
            image.verify()
    except (UnidentifiedImageError,OSError,Image.DecompressionBombError) as exc:
        raise HTTPException(422,"The screenshot could not be opened.") from exc
    return data,mime


def install_claims(app, connect_db):
    router=APIRouter(prefix="/api/auth")

    @router.post("/claims/roster")
    def claim_roster(body: ClaimCode,request: Request):
        csrf_check(request)
        require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            invite=_invite(cur,body.code,("claim",))
            cur.execute("""SELECT id,player_name AS name FROM workspace_players
                WHERE alliance_id=%s AND archived=FALSE ORDER BY player_name""",(invite["alliance_id"],))
            return {"players":cur.fetchall()}

    @router.post("/claims")
    def submit_claim(request: Request,code: str=Form(...),player_id: int=Form(...),
                     birthday: str=Form(...),note: str=Form(""),screenshot: UploadFile=File(...)):
        csrf_check(request)
        actor=require_account(connect_db,request)
        birthday=birthday_value(birthday)
        if len(note)>500:
            raise HTTPException(422,"Keep the note under 500 characters.")
        proof,mime=evidence_bytes(screenshot.file)
        with connect_db() as conn, conn.cursor() as cur:
            invite=_invite(cur,code,("claim",))
            cur.execute("SELECT player_name FROM workspace_players WHERE alliance_id=%s AND id=%s AND archived=FALSE",
                        (invite["alliance_id"],player_id))
            player=cur.fetchone()
            if not player:
                raise HTTPException(404,"Choose a player on this alliance's roster.")
            cur.execute("""INSERT INTO alliance_claims
                (alliance_id,account_id,player_id,invite_id,birthday,proof,proof_mime,note)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (invite["alliance_id"],actor["id"],player_id,invite["id"],birthday,proof,mime,note.strip()))
            claim_id=cur.lastrowid
            cur.execute("UPDATE alliance_invites SET use_count=use_count+1 WHERE id=%s",(invite["id"],))
            cur.execute("SELECT slug FROM alliance_workspaces WHERE id=%s",(invite["alliance_id"],))
            slug=cur.fetchone()["slug"]
            notify_leaders(cur,invite["alliance_id"],"claim","Player claim awaiting review",
                           "/account?alliance="+slug+"&claim="+str(claim_id))
            audit(cur,invite["alliance_id"],actor,"claim_submitted",{"claim_id":claim_id,"player_id":player_id})
            conn.commit()
        return {"id":claim_id,"slug":slug}

    @router.get("/alliances/{slug}/claims")
    def list_claims(slug: str,request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("""SELECT c.id,c.account_id,c.player_id,p.player_name AS player_name,c.birthday,c.note,
                c.status,c.created_at,c.handled_at,h.display_name AS handled_by,
                EXISTS(SELECT 1 FROM alliance_player_claims linked WHERE linked.alliance_id=c.alliance_id
                    AND linked.player_id=c.player_id AND linked.account_id<>c.account_id) AS already_claimed,
                (SELECT COUNT(*) FROM alliance_claims competing WHERE competing.alliance_id=c.alliance_id
                    AND competing.player_id=c.player_id AND competing.id<>c.id
                    AND competing.status IN ('pending','more_proof')) AS competing
                FROM alliance_claims c JOIN workspace_players p ON p.id=c.player_id AND p.alliance_id=c.alliance_id
                LEFT JOIN alliance_accounts h ON h.id=c.handled_by
                WHERE c.alliance_id=%s ORDER BY c.created_at DESC LIMIT 200""",(workspace["id"],))
            return {"claims":cur.fetchall()}

    @router.get("/alliances/{slug}/claims/{claim_id}/proof")
    def claim_proof(slug: str,claim_id: int,request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("SELECT proof,proof_mime FROM alliance_claims WHERE id=%s AND alliance_id=%s",
                        (claim_id,workspace["id"]))
            row=cur.fetchone()
            if not row:
                raise HTTPException(404,"Claim not found.")
        return Response(row["proof"],media_type=row["proof_mime"],headers={"Cache-Control":"no-store",
            "X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'; sandbox"})

    @router.post("/alliances/{slug}/claims/{claim_id}/decision")
    def decide_claim(slug: str,claim_id: int,body: Decision,request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("SELECT * FROM alliance_claims WHERE id=%s AND alliance_id=%s FOR UPDATE",
                        (claim_id,workspace["id"]))
            claim=cur.fetchone()
            if not claim:
                raise HTTPException(404,"Claim not found.")
            if claim["status"] not in ("pending","more_proof"):
                raise HTTPException(409,"This claim has already been handled.")
            if body.action=="approve":
                cur.execute("SELECT id FROM workspace_players WHERE alliance_id=%s AND id=%s FOR UPDATE",
                            (workspace["id"],claim["player_id"]))
                if not cur.fetchone():
                    raise HTTPException(409,"The roster profile no longer exists.")
                cur.execute("""SELECT account_id FROM alliance_player_claims
                    WHERE alliance_id=%s AND player_id=%s FOR UPDATE""",(workspace["id"],claim["player_id"]))
                linked=cur.fetchone()
                if linked and linked["account_id"]!=claim["account_id"] and not body.replace_existing:
                    raise HTTPException(409,"This profile is already claimed. Confirm replacement to continue.")
                cur.execute("""SELECT player_id FROM alliance_player_claims
                    WHERE alliance_id=%s AND account_id=%s FOR UPDATE""",(workspace["id"],claim["account_id"]))
                other_profile=cur.fetchone()
                if other_profile and other_profile["player_id"]!=claim["player_id"]:
                    raise HTTPException(409,"This account already claims another player in the alliance.")
                cur.execute("""SELECT COUNT(*) AS n FROM alliance_claims WHERE alliance_id=%s AND player_id=%s
                    AND id<>%s AND status IN ('pending','more_proof')""",
                    (workspace["id"],claim["player_id"],claim_id))
                if cur.fetchone()["n"] and not body.confirm_conflicts:
                    raise HTTPException(409,"Other claims are pending. Review and confirm before approving.")
                if linked and linked["account_id"]!=claim["account_id"]:
                    cur.execute("DELETE FROM alliance_player_claims WHERE alliance_id=%s AND player_id=%s",
                                (workspace["id"],claim["player_id"]))
                    notify(cur,workspace["id"],linked["account_id"],"claim","A player profile link changed","/account")
                try:
                    cur.execute("""INSERT INTO alliance_player_claims (alliance_id,player_id,account_id,approved_claim_id)
                        VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                        account_id=VALUES(account_id),approved_claim_id=VALUES(approved_claim_id)""",
                        (workspace["id"],claim["player_id"],claim["account_id"],claim_id))
                except IntegrityError as exc:
                    raise HTTPException(409,"This account already claims another player in the alliance.") from exc
                cur.execute("""UPDATE workspace_players SET birthday=%s,updated_at=UTC_TIMESTAMP(6)
                    WHERE alliance_id=%s AND id=%s""",("2000-"+claim["birthday"],workspace["id"],claim["player_id"]))
                status="approved"
            else:
                status="more_proof" if body.action=="more_proof" else "rejected"
            cur.execute("""UPDATE alliance_claims SET status=%s,handled_by=%s,handled_at=UTC_TIMESTAMP(6),
                updated_at=UTC_TIMESTAMP(6) WHERE id=%s AND alliance_id=%s""",
                (status,actor["id"],claim_id,workspace["id"]))
            if body.message.strip():
                cur.execute("""INSERT INTO alliance_claim_comments (alliance_id,claim_id,account_id,body,visibility)
                    VALUES (%s,%s,%s,%s,'claimant')""",(workspace["id"],claim_id,actor["id"],body.message.strip()))
            notify(cur,workspace["id"],claim["account_id"],"claim","Your player claim was updated",
                   "/account?claim="+str(claim_id))
            audit(cur,workspace["id"],actor,"claim_"+status,{"claim_id":claim_id})
            conn.commit()
        return {"status":status}

    @router.get("/my-claims")
    def my_claims(request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT c.id,c.status,c.note,c.created_at,w.slug,w.name,p.player_name,
                (SELECT body FROM alliance_claim_comments x WHERE x.claim_id=c.id AND x.visibility='claimant'
                 ORDER BY x.id DESC LIMIT 1) AS response
                FROM alliance_claims c JOIN alliance_workspaces w ON w.id=c.alliance_id
                JOIN workspace_players p ON p.id=c.player_id AND p.alliance_id=c.alliance_id
                WHERE c.account_id=%s ORDER BY c.id DESC LIMIT 100""",(actor["id"],))
            return {"claims":cur.fetchall()}

    @router.post("/claims/{claim_id}/more-proof")
    def add_proof(claim_id: int,request: Request,note: str=Form(""),screenshot: UploadFile=File(...)):
        csrf_check(request)
        actor=require_account(connect_db,request)
        if len(note)>500:
            raise HTTPException(422,"Keep the note under 500 characters.")
        proof,mime=evidence_bytes(screenshot.file)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT c.alliance_id,w.slug FROM alliance_claims c
                JOIN alliance_workspaces w ON w.id=c.alliance_id
                WHERE c.id=%s AND c.account_id=%s AND c.status='more_proof' FOR UPDATE""",
                (claim_id,actor["id"]))
            claim=cur.fetchone()
            if not claim:
                raise HTTPException(404,"That claim is unavailable for more proof.")
            cur.execute("""UPDATE alliance_claims SET proof=%s,proof_mime=%s,note=%s,
                status='pending',updated_at=UTC_TIMESTAMP(6) WHERE id=%s""",(proof,mime,note.strip(),claim_id))
            notify_leaders(cur,claim["alliance_id"],"claim","New proof is ready for review",
                           "/account?alliance="+claim["slug"]+"&claim="+str(claim_id))
            audit(cur,claim["alliance_id"],actor,"claim_more_proof_submitted",{"claim_id":claim_id})
            conn.commit()
        return {"ok":True}

    @router.get("/alliances/{slug}/claims/{claim_id}/comments")
    def claim_comments(slug: str,claim_id: int,request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("""SELECT c.body,c.created_at,a.display_name FROM alliance_claim_comments c
                JOIN alliance_accounts a ON a.id=c.account_id
                WHERE c.alliance_id=%s AND c.claim_id=%s ORDER BY c.id""",(workspace["id"],claim_id))
            return {"comments":cur.fetchall()}

    @router.post("/alliances/{slug}/claims/{claim_id}/comments")
    def add_claim_comment(slug: str,claim_id: int,body: Comment,request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        text=body.body.strip()
        if not text:raise HTTPException(422,"Enter a note.")
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("SELECT id FROM alliance_claims WHERE alliance_id=%s AND id=%s",(workspace["id"],claim_id))
            if not cur.fetchone():raise HTTPException(404,"Claim not found.")
            cur.execute("""INSERT INTO alliance_claim_comments (alliance_id,claim_id,account_id,body)
                VALUES (%s,%s,%s,%s)""",(workspace["id"],claim_id,actor["id"],text))
            conn.commit()
        return {"ok":True}

    @router.get("/notifications")
    def notifications(request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT id,alliance_id,kind,title,target,read_at,created_at FROM alliance_notifications
                WHERE account_id=%s ORDER BY created_at DESC,id DESC LIMIT 100""",(actor["id"],))
            rows=cur.fetchall()
            cur.execute("SELECT COUNT(*) AS n FROM alliance_notifications WHERE account_id=%s AND read_at IS NULL",
                        (actor["id"],))
            unread=cur.fetchone()["n"]
        return {"notifications":rows,"unread":unread}

    @router.post("/notifications/{notification_id}/read")
    def read_notification(notification_id: int,request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE alliance_notifications SET read_at=COALESCE(read_at,UTC_TIMESTAMP(6))
                WHERE id=%s AND account_id=%s""",(notification_id,actor["id"]))
            conn.commit()
        return {"ok":True}

    @router.patch("/my-birthday")
    def update_my_birthday(body: Birthday,request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        birthday=birthday_value(body.birthday)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,body.alliance_slug)
            cur.execute("SELECT player_id FROM alliance_player_claims WHERE alliance_id=%s AND account_id=%s",
                        (workspace["id"],actor["id"]))
            link=cur.fetchone()
            if not link:
                raise HTTPException(403,"Claim your player profile first.")
            cur.execute("UPDATE workspace_players SET birthday=%s WHERE alliance_id=%s AND id=%s",
                        ("2000-"+birthday,workspace["id"],link["player_id"]))
            audit(cur,workspace["id"],actor,"player_birthday_updated",{"player_id":link["player_id"]})
            conn.commit()
        return {"ok":True}

    app.include_router(router)

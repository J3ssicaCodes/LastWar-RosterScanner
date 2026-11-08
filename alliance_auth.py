"""Accounts, sessions, alliance invitations, and leadership handoffs."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import re
import secrets
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pymysql.err import IntegrityError


COOKIE_NAME = "alliance_session"
SETUP_COOKIE = "alliance_setup_device"
PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
HASH_LIMIT = BoundedSemaphore(2)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(20))

ACCOUNT_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS alliance_sessions (
        token_hash BINARY(32) PRIMARY KEY, account_id BIGINT UNSIGNED NOT NULL,
        expires_at DATETIME(6) NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_session_account (account_id), KEY idx_session_expiry (expires_at),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_login_attempts (
        fingerprint BINARY(32) PRIMARY KEY, attempts INT UNSIGNED NOT NULL,
        started_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_invites (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        token_hash BINARY(32) NOT NULL UNIQUE,
        kind ENUM('referral','leadership','claim') NOT NULL,
        alliance_id BIGINT UNSIGNED NULL, issuer_id BIGINT UNSIGNED NOT NULL,
        expires_at DATETIME(6) NOT NULL, revoked_at DATETIME(6) NULL,
        used_at DATETIME(6) NULL, use_count INT UNSIGNED NOT NULL DEFAULT 0,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_invite_alliance (alliance_id, kind),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id),
        FOREIGN KEY (issuer_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS platform_audit_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        actor_id BIGINT UNSIGNED NOT NULL, alliance_id BIGINT UNSIGNED NULL,
        action VARCHAR(60) NOT NULL, details JSON NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_platform_audit (created_at,id)
    ) ENGINE=InnoDB""",
)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def token_hash(token):
    return sha256(token.encode("utf-8")).digest()


def normalize_username(username):
    value = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,40}", value):
        raise HTTPException(422, "Use 3–40 letters, numbers, periods, hyphens, or underscores.")
    return value


def validate_password(password):
    if not 12 <= len(password) <= 128:
        raise HTTPException(422, "Choose a password between 12 and 128 characters.")


def verify_password(encoded, password):
    try:
        with HASH_LIMIT:
            return PASSWORDS.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def csrf_check(request):
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(403, "Refresh the page and try again.")
    parsed = urlsplit(origin)
    host = request.headers.get("host", "").lower()
    if parsed.scheme not in ("https", "http") or parsed.netloc.lower() != host:
        raise HTTPException(403, "Request origin did not match this site.")
    if parsed.path or parsed.query or parsed.fragment:
        raise HTTPException(403, "Request origin did not match this site.")


def audit(cursor, alliance_id, actor, action, details):
    cursor.execute("""INSERT INTO alliance_audit_log
        (alliance_id, actor_id, actor_name, action, details) VALUES (%s,%s,%s,%s,%s)""",
        (alliance_id, actor["id"] if actor else None,
         actor["display_name"] if actor else "System", action, json.dumps(details)))


def current_account(connect_db, request):
    token = request.cookies.get(COOKIE_NAME, "")
    if len(token) < 30 or len(token) > 128:
        return None
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT a.id,a.username,a.display_name,a.platform_admin,a.setup_required
            FROM alliance_sessions s JOIN alliance_accounts a ON a.id=s.account_id
            WHERE s.token_hash=%s AND s.expires_at>UTC_TIMESTAMP(6)""", (token_hash(token),))
        return cur.fetchone()


def require_account(connect_db, request):
    account = current_account(connect_db, request)
    if not account:
        raise HTTPException(401, "Sign in to continue.")
    return account


def membership(cursor, alliance_id, account_id):
    cursor.execute("""SELECT role FROM alliance_memberships
        WHERE alliance_id=%s AND account_id=%s""", (alliance_id, account_id))
    row = cursor.fetchone()
    return row["role"] if row else None


def require_leader(cursor, alliance_id, account_id, r5=False):
    role = membership(cursor, alliance_id, account_id)
    if role not in (("R5",) if r5 else ("R4", "R5")):
        raise HTTPException(403, "Leadership access is required for this alliance.")
    return role


def workspace_by_slug(cursor, slug, lock=False):
    cursor.execute("SELECT id,slug,name,tag,server_number FROM alliance_workspaces WHERE slug=%s" +
                   (" FOR UPDATE" if lock else ""), (slug,))
    workspace = cursor.fetchone()
    if not workspace:
        raise HTTPException(404, "Alliance not found.")
    return workspace


def _set_cookie(response, request, token):
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(COOKIE_NAME, token, max_age=7 * 86400, httponly=True,
                        secure=secure, samesite="lax", path="/")


def _session(cursor, account_id):
    token = secrets.token_urlsafe(32)
    cursor.execute("INSERT INTO alliance_sessions (token_hash,account_id,expires_at) VALUES (%s,%s,%s)",
                   (token_hash(token), account_id, utcnow() + timedelta(days=7)))
    return token


class Login(BaseModel):
    username: str = Field(max_length=40)
    password: str = Field(max_length=128)
    setup_code: str = Field(default="", max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)


class InviteRequest(BaseModel):
    kind: str = Field(pattern=r"^(referral|leadership|claim)$")
    alliance_slug: str | None = Field(default=None, max_length=40)
    hours: int = Field(default=168, ge=1, le=24 * 90)
    support_reason: str = Field(default="", max_length=240)


class Register(BaseModel):
    code: str = Field(min_length=16, max_length=128)
    username: str = Field(max_length=40)
    password: str = Field(max_length=128)
    display_name: str = Field(min_length=1, max_length=80)


class Redeem(BaseModel):
    code: str = Field(min_length=16, max_length=128)


class NewAlliance(BaseModel):
    code: str = Field(min_length=16, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    tag: str = Field(min_length=1, max_length=16)
    server: int = Field(ge=1, le=999999)


class TransferR5(BaseModel):
    username: str = Field(max_length=40)
    support_reason: str = Field(default="", max_length=240)


def _invite(cursor, token, kinds):
    cursor.execute("""SELECT * FROM alliance_invites WHERE token_hash=%s FOR UPDATE""", (token_hash(token),))
    row = cursor.fetchone()
    if (not row or row["kind"] not in kinds or row["revoked_at"] or row["expires_at"] <= utcnow()
            or (row["kind"] == "referral" and row["used_at"])):
        raise HTTPException(400, "That invite is invalid, expired, or revoked.")
    return row


def install_accounts(app, connect_db):
    router = APIRouter(prefix="/api/auth")

    @router.post("/login")
    def login(body: Login, request: Request, response: Response):
        csrf_check(request)
        username = normalize_username(body.username)
        client = request.client.host if request.client else "unknown"
        fingerprint = sha256((username + "|" + client).encode()).digest()
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("SELECT attempts,started_at FROM alliance_login_attempts WHERE fingerprint=%s FOR UPDATE",
                        (fingerprint,))
            attempt = cur.fetchone()
            if attempt and attempt["started_at"] > utcnow() - timedelta(minutes=15) and attempt["attempts"] >= 10:
                raise HTTPException(429, "Too many attempts. Try again later.")
            cur.execute("SELECT * FROM alliance_accounts WHERE username=%s", (username,))
            account = cur.fetchone()
            valid = verify_password(account["password_hash"] if account else DUMMY_HASH, body.password)
            first_setup=False
            if valid and account["setup_required"]:
                first_setup = bool(account["setup_token_hash"] and account["setup_expires_at"]
                    and account["setup_expires_at"] > utcnow()
                    and hmac.compare_digest(account["setup_token_hash"], token_hash(body.setup_code)))
                device=request.cookies.get(SETUP_COOKIE,"")
                device_ok=bool(device and account["setup_device_hash"] and
                    hmac.compare_digest(account["setup_device_hash"],token_hash(device)))
                valid=first_setup or device_ok
            if not valid:
                cur.execute("""INSERT INTO alliance_login_attempts (fingerprint,attempts,started_at)
                    VALUES (%s,1,UTC_TIMESTAMP(6))
                    ON DUPLICATE KEY UPDATE
                    attempts=IF(started_at<UTC_TIMESTAMP(6)-INTERVAL 15 MINUTE,1,attempts+1),
                    started_at=IF(started_at<UTC_TIMESTAMP(6)-INTERVAL 15 MINUTE,UTC_TIMESTAMP(6),started_at)""",
                    (fingerprint,))
                conn.commit()
                raise HTTPException(401, "Sign-in details were not accepted.")
            cur.execute("DELETE FROM alliance_login_attempts WHERE fingerprint=%s", (fingerprint,))
            device_token=None
            if first_setup:
                device_token=secrets.token_urlsafe(32)
                cur.execute("""UPDATE alliance_accounts SET setup_token_hash=NULL,setup_expires_at=NULL,
                    setup_device_hash=%s WHERE id=%s""",(token_hash(device_token),account["id"]))
            token = _session(cur, account["id"])
            conn.commit()
        _set_cookie(response, request, token)
        if device_token:
            secure=request.url.scheme=="https" or request.headers.get("x-forwarded-proto")=="https"
            response.set_cookie(SETUP_COOKIE,device_token,max_age=30*86400,httponly=True,
                                secure=secure,samesite="strict",path="/")
        return {"username":account["username"], "display_name":account["display_name"]}

    @router.get("/me")
    def me(request: Request):
        account = require_account(connect_db, request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT w.slug,w.name,w.tag,w.server_number,m.role
                FROM alliance_memberships m JOIN alliance_workspaces w ON w.id=m.alliance_id
                WHERE m.account_id=%s ORDER BY w.name""", (account["id"],))
            workspaces = cur.fetchall()
        return {"username":account["username"], "display_name":account["display_name"],
                "platform_admin":bool(account["platform_admin"]), "workspaces":workspaces}

    @router.post("/logout")
    def logout(request: Request, response: Response):
        csrf_check(request)
        token = request.cookies.get(COOKIE_NAME, "")
        if token:
            with connect_db() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM alliance_sessions WHERE token_hash=%s", (token_hash(token),))
                conn.commit()
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok":True}

    @router.post("/change-password")
    def change_password(body: PasswordChange, request: Request, response: Response):
        csrf_check(request)
        actor = require_account(connect_db, request)
        validate_password(body.new_password)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM alliance_accounts WHERE id=%s FOR UPDATE", (actor["id"],))
            account = cur.fetchone()
            if not account or not verify_password(account["password_hash"], body.current_password):
                raise HTTPException(400, "Current password was not accepted.")
            with HASH_LIMIT:
                encoded = PASSWORDS.hash(body.new_password)
            cur.execute("""UPDATE alliance_accounts SET password_hash=%s,setup_required=FALSE,
                setup_token_hash=NULL,setup_expires_at=NULL,setup_device_hash=NULL WHERE id=%s""", (encoded,actor["id"]))
            cur.execute("DELETE FROM alliance_sessions WHERE account_id=%s", (actor["id"],))
            token = _session(cur, actor["id"])
            cur.execute("SELECT alliance_id FROM alliance_memberships WHERE account_id=%s", (actor["id"],))
            for row in cur.fetchall():
                audit(cur,row["alliance_id"],actor,"password_changed",{})
            conn.commit()
        _set_cookie(response,request,token)
        response.delete_cookie(SETUP_COOKIE,path="/")
        return {"ok":True}

    @router.post("/invites")
    def create_invite(body: InviteRequest, request: Request):
        csrf_check(request)
        actor = require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace = workspace_by_slug(cur,body.alliance_slug) if body.alliance_slug else None
            if body.kind == "referral":
                cur.execute("SELECT 1 FROM alliance_memberships WHERE account_id=%s AND role='R5' LIMIT 1", (actor["id"],))
                if not actor["platform_admin"] and not cur.fetchone():
                    raise HTTPException(403, "An R5 or platform admin can create a referral.")
            else:
                if not workspace:
                    raise HTTPException(422, "Choose an alliance.")
                role = membership(cur,workspace["id"],actor["id"])
                allowed = role == "R5" or (body.kind == "claim" and role == "R4")
                support = bool(actor["platform_admin"] and body.support_reason.strip())
                if not allowed and not (body.kind == "leadership" and support):
                    raise HTTPException(403, "This invitation requires alliance leadership access.")
            token=secrets.token_urlsafe(24)
            expiry=utcnow()+timedelta(hours=body.hours)
            cur.execute("""INSERT INTO alliance_invites
                (token_hash,kind,alliance_id,issuer_id,expires_at) VALUES (%s,%s,%s,%s,%s)""",
                (token_hash(token),body.kind,workspace["id"] if workspace else None,actor["id"],expiry))
            invite_id=cur.lastrowid
            if workspace:
                audit(cur,workspace["id"],actor,"invite_created",
                      {"invite_id":invite_id,"kind":body.kind,"support_reason":body.support_reason.strip() or None})
            if actor["platform_admin"] and (body.support_reason.strip() or body.kind=="referral"):
                cur.execute("""INSERT INTO platform_audit_log (actor_id,alliance_id,action,details)
                    VALUES (%s,%s,%s,%s)""",(actor["id"],workspace["id"] if workspace else None,
                    "invite_created",json.dumps({"invite_id":invite_id,"kind":body.kind,
                    "reason":body.support_reason.strip() or None})))
            conn.commit()
        return {"id":invite_id,"code":token,"expires_at":expiry.isoformat()+"Z","kind":body.kind}

    @router.post("/register")
    def register(body: Register, request: Request, response: Response):
        csrf_check(request)
        username=normalize_username(body.username)
        validate_password(body.password)
        display=body.display_name.strip()
        if not display:
            raise HTTPException(422,"Enter your in-game name.")
        with HASH_LIMIT:
            encoded=PASSWORDS.hash(body.password)
        with connect_db() as conn, conn.cursor() as cur:
            invite=_invite(cur,body.code,("referral","leadership","claim"))
            try:
                cur.execute("""INSERT INTO alliance_accounts (username,display_name,password_hash)
                    VALUES (%s,%s,%s)""",(username,display,encoded))
                account_id=cur.lastrowid
                if invite["kind"] == "leadership":
                    cur.execute("INSERT INTO alliance_memberships (alliance_id,account_id,role) VALUES (%s,%s,'R4')",
                                (invite["alliance_id"],account_id))
                    cur.execute("UPDATE alliance_invites SET use_count=use_count+1 WHERE id=%s",(invite["id"],))
                    audit(cur,invite["alliance_id"],{"id":account_id,"display_name":display},
                          "leadership_invite_redeemed",{"invite_id":invite["id"],"role":"R4"})
                    cur.execute("SELECT slug FROM alliance_workspaces WHERE id=%s",(invite["alliance_id"],))
                    slug=cur.fetchone()["slug"]
                else:
                    slug=None
                token=_session(cur,account_id)
                conn.commit()
            except IntegrityError as exc:
                conn.rollback()
                raise HTTPException(409,"That username is unavailable.") from exc
        _set_cookie(response,request,token)
        return {"username":username,"invite_kind":invite["kind"],"slug":slug}

    @router.post("/inspect-invite")
    def inspect_invite(body: Redeem, request: Request):
        csrf_check(request)
        with connect_db() as conn, conn.cursor() as cur:
            invite=_invite(cur,body.code,("referral","leadership","claim"))
            workspace=None
            if invite["alliance_id"]:
                cur.execute("SELECT name,slug FROM alliance_workspaces WHERE id=%s",(invite["alliance_id"],))
                workspace=cur.fetchone()
        return {"kind":invite["kind"],"alliance":workspace}

    @router.post("/redeem")
    def redeem(body: Redeem, request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            invite=_invite(cur,body.code,("leadership",))
            if membership(cur,invite["alliance_id"],actor["id"]):
                raise HTTPException(409,"You are already in this alliance's leadership.")
            cur.execute("INSERT INTO alliance_memberships (alliance_id,account_id,role) VALUES (%s,%s,'R4')",
                        (invite["alliance_id"],actor["id"]))
            cur.execute("UPDATE alliance_invites SET use_count=use_count+1 WHERE id=%s",(invite["id"],))
            audit(cur,invite["alliance_id"],actor,"leadership_invite_redeemed",
                  {"invite_id":invite["id"],"role":"R4"})
            conn.commit()
            cur.execute("SELECT slug FROM alliance_workspaces WHERE id=%s",(invite["alliance_id"],))
            slug=cur.fetchone()["slug"]
        return {"ok":True,"slug":slug}

    @router.get("/alliances/{slug}/invites")
    def list_invites(slug: str, request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("""SELECT id,kind,expires_at,revoked_at,use_count,created_at
                FROM alliance_invites WHERE alliance_id=%s ORDER BY created_at DESC LIMIT 100""",
                (workspace["id"],))
            return {"invites":cur.fetchall()}

    @router.get("/referrals")
    def list_referrals(request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT id,expires_at,revoked_at,used_at,use_count,created_at
                FROM alliance_invites WHERE kind='referral' AND issuer_id=%s ORDER BY created_at DESC LIMIT 100""",
                (actor["id"],))
            return {"referrals":cur.fetchall()}

    @router.post("/referrals/{invite_id}/revoke")
    def revoke_referral(invite_id: int,request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT alliance_id,issuer_id,revoked_at FROM alliance_invites
                WHERE id=%s AND kind='referral' FOR UPDATE""",(invite_id,))
            invite=cur.fetchone()
            if not invite or (invite["issuer_id"]!=actor["id"] and not actor["platform_admin"]):
                raise HTTPException(404,"Referral not found.")
            if not invite["revoked_at"]:
                cur.execute("UPDATE alliance_invites SET revoked_at=UTC_TIMESTAMP(6) WHERE id=%s",(invite_id,))
                if invite["alliance_id"]:
                    audit(cur,invite["alliance_id"],actor,"referral_revoked",{"invite_id":invite_id})
                if actor["platform_admin"]:
                    cur.execute("""INSERT INTO platform_audit_log (actor_id,alliance_id,action,details)
                        VALUES (%s,%s,'referral_revoked',%s)""",
                        (actor["id"],invite["alliance_id"],json.dumps({"invite_id":invite_id})))
            conn.commit()
        return {"ok":True}

    @router.post("/alliances/{slug}/invites/{invite_id}/revoke")
    def revoke_invite(slug: str, invite_id: int, request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            cur.execute("""SELECT kind,revoked_at FROM alliance_invites
                WHERE id=%s AND alliance_id=%s FOR UPDATE""",(invite_id,workspace["id"]))
            invite=cur.fetchone()
            if not invite:
                raise HTTPException(404,"Invite not found.")
            require_leader(cur,workspace["id"],actor["id"],r5=invite["kind"] != "claim")
            if not invite["revoked_at"]:
                cur.execute("UPDATE alliance_invites SET revoked_at=UTC_TIMESTAMP(6) WHERE id=%s",(invite_id,))
                audit(cur,workspace["id"],actor,"invite_revoked",{"invite_id":invite_id,"kind":invite["kind"]})
            conn.commit()
        return {"ok":True}

    @router.post("/create-alliance")
    def create_alliance(body: NewAlliance, request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        name=body.name.strip()
        tag=body.tag.strip().upper()
        if not name or not re.fullmatch(r"[A-Z0-9]{1,16}",tag):
            raise HTTPException(422,"Enter an alliance name and an alphanumeric tag.")
        slug=f"{body.server}-{tag.lower()}"
        with connect_db() as conn, conn.cursor() as cur:
            invite=_invite(cur,body.code,("referral",))
            try:
                cur.execute("""INSERT INTO alliance_workspaces
                    (slug,server_number,tag,name,referred_by) VALUES (%s,%s,%s,%s,%s)""",
                    (slug,body.server,tag,name,invite["issuer_id"]))
                alliance_id=cur.lastrowid
                cur.execute("INSERT INTO alliance_memberships (alliance_id,account_id,role) VALUES (%s,%s,'R5')",
                            (alliance_id,actor["id"]))
                cur.execute("UPDATE alliance_invites SET used_at=UTC_TIMESTAMP(6),use_count=1 WHERE id=%s",
                            (invite["id"],))
                audit(cur,alliance_id,actor,"alliance_created",{"referral_id":invite["id"]})
                conn.commit()
            except IntegrityError as exc:
                conn.rollback()
                raise HTTPException(409,"An alliance with this server and tag already exists.") from exc
        return {"slug":slug}

    @router.get("/alliances/{slug}/members")
    def members(slug: str, request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            role=membership(cur,workspace["id"],actor["id"])
            if role != "R5":
                raise HTTPException(403,"R5 access is required.")
            cur.execute("""SELECT a.username,a.display_name,m.role FROM alliance_memberships m
                JOIN alliance_accounts a ON a.id=m.account_id WHERE m.alliance_id=%s ORDER BY m.role DESC,a.display_name""",
                (workspace["id"],))
            return {"members":cur.fetchall()}

    @router.get("/alliances/{slug}/audit")
    def audit_log(slug: str, request: Request, person: str = "", action: str = "",
                  start: str = "", end: str = ""):
        actor=require_account(connect_db,request)
        if len(person)>80 or len(action)>60 or any(value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}",value)
                                                   for value in (start,end)):
            raise HTTPException(422,"Choose valid audit filters.")
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            sql="""SELECT id,actor_name,action,details,created_at FROM alliance_audit_log
                WHERE alliance_id=%s AND actor_name LIKE %s AND action LIKE %s"""
            values=[workspace["id"],"%"+person+"%","%"+action+"%"]
            if start:
                sql+=" AND created_at>=%s"
                values.append(start+" 00:00:00")
            if end:
                sql+=" AND created_at<DATE_ADD(%s,INTERVAL 1 DAY)"
                values.append(end)
            cur.execute(sql+" ORDER BY created_at DESC,id DESC LIMIT 300",tuple(values))
            return {"events":cur.fetchall()}

    @router.delete("/alliances/{slug}/members/{username}")
    def remove_member(slug: str, username: str, request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        username=normalize_username(username)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug,lock=True)
            require_leader(cur,workspace["id"],actor["id"],r5=True)
            cur.execute("""SELECT a.id,a.display_name,m.role FROM alliance_accounts a
                JOIN alliance_memberships m ON m.account_id=a.id
                WHERE a.username=%s AND m.alliance_id=%s FOR UPDATE""",(username,workspace["id"]))
            target=cur.fetchone()
            if not target:
                raise HTTPException(404,"Leadership member not found.")
            if target["role"] == "R5":
                raise HTTPException(409,"Transfer R5 before removing this membership.")
            cur.execute("DELETE FROM alliance_memberships WHERE alliance_id=%s AND account_id=%s",
                        (workspace["id"],target["id"]))
            audit(cur,workspace["id"],actor,"membership_removed",{"account_id":target["id"],"role":target["role"]})
            conn.commit()
        return {"ok":True}

    @router.post("/alliances/{slug}/transfer-r5")
    def transfer_r5(slug: str, body: TransferR5, request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        target_username=normalize_username(body.username)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug,lock=True)
            role=membership(cur,workspace["id"],actor["id"])
            if role != "R5" and not (actor["platform_admin"] and body.support_reason.strip()):
                raise HTTPException(403,"An R5 or a platform support action can transfer R5.")
            cur.execute("""SELECT a.id,a.display_name,m.role FROM alliance_accounts a
                JOIN alliance_memberships m ON m.account_id=a.id
                WHERE a.username=%s AND m.alliance_id=%s FOR UPDATE""",(target_username,workspace["id"]))
            target=cur.fetchone()
            if not target or target["role"] != "R4":
                raise HTTPException(400,"Choose an existing R4 in this alliance.")
            cur.execute("SELECT account_id FROM alliance_memberships WHERE alliance_id=%s AND role='R5' FOR UPDATE",
                        (workspace["id"],))
            previous=cur.fetchone()
            if not previous:
                raise HTTPException(409,"This alliance has no current R5.")
            cur.execute("UPDATE alliance_memberships SET role='R4' WHERE alliance_id=%s AND account_id=%s",
                        (workspace["id"],previous["account_id"]))
            cur.execute("UPDATE alliance_memberships SET role='R5' WHERE alliance_id=%s AND account_id=%s",
                        (workspace["id"],target["id"]))
            audit(cur,workspace["id"],actor,"r5_transferred",
                  {"previous_account_id":previous["account_id"],"new_account_id":target["id"],
                   "support_reason":body.support_reason.strip() or None})
            if actor["platform_admin"] and role!="R5":
                cur.execute("""INSERT INTO platform_audit_log (actor_id,alliance_id,action,details)
                    VALUES (%s,%s,'r5_transferred',%s)""",
                    (actor["id"],workspace["id"],json.dumps({"target_account_id":target["id"],
                    "reason":body.support_reason.strip()})))
            conn.commit()
        return {"ok":True}

    app.include_router(router)

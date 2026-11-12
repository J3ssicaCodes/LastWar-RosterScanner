"""Accounts, sessions, alliance invitations, and leadership handoffs."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import base64
import hmac
import json
import os
import re
import secrets
from threading import BoundedSemaphore
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from pymysql.err import IntegrityError
from cryptography.fernet import Fernet, InvalidToken


COOKIE_NAME = "alliance_session"
SETUP_COOKIE = "alliance_setup_device"
PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
HASH_LIMIT = BoundedSemaphore(2)
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(20))

ACCOUNT_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS alliance_account_avatars (
        account_id BIGINT UNSIGNED PRIMARY KEY, image MEDIUMBLOB NOT NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_sessions (
        token_hash BINARY(32) PRIMARY KEY, account_id BIGINT UNSIGNED NOT NULL,
        expires_at DATETIME(6) NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_session_account (account_id), KEY idx_session_expiry (expires_at),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_support_sessions (
        session_hash BINARY(32) PRIMARY KEY, target_id BIGINT UNSIGNED NOT NULL,
        alliance_id BIGINT UNSIGNED NOT NULL, expires_at DATETIME(6) NOT NULL,
        FOREIGN KEY (session_hash) REFERENCES alliance_sessions(token_hash) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES alliance_accounts(id),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id)
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
    """CREATE TABLE IF NOT EXISTS alliance_invite_links (
        invite_id BIGINT UNSIGNED PRIMARY KEY, encrypted_token VARBINARY(512) NOT NULL,
        FOREIGN KEY (invite_id) REFERENCES alliance_invites(id) ON DELETE CASCADE
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


def invite_cipher():
    # A dedicated deployment secret can be supplied; otherwise use the existing
    # database credential, which is already stored outside the database itself.
    secret=os.getenv('ALLIANCE_INVITE_LINK_SECRET','')
    if not secret:
        from app import _db_config
        secret=_db_config()['password']
    if not secret:
        raise RuntimeError('Invite link encryption is unavailable.')
    key=hmac.new(secret.encode('utf-8'),b'alliance-tools-invite-links-v1',sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def recover_invite(row):
    encrypted=row.pop('encrypted_token',None)
    digest=row.pop('token_hash',None)
    if not encrypted or row.get('revoked_at') or row['expires_at']<=utcnow() or row.get('used_at'):
        return row
    try:
        token=invite_cipher().decrypt(bytes(encrypted)).decode('utf-8')
        if hmac.compare_digest(token_hash(token),bytes(digest)):
            row['code']=token
    except (InvalidToken,UnicodeDecodeError,TypeError,ValueError,RuntimeError):
        pass
    return row


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
    if actor and actor.get("support_actor_name"):
        details = {**details, "support_actor": actor["support_actor_name"], "support_actor_id": actor["support_actor_id"]}
    cursor.execute("""INSERT INTO alliance_audit_log
        (alliance_id, actor_id, actor_name, action, details) VALUES (%s,%s,%s,%s,%s)""",
        (alliance_id, actor["id"] if actor else None,
         actor["display_name"] if actor else "System", action, json.dumps(details)))


def current_account(connect_db, request):
    token = request.cookies.get(COOKIE_NAME, "")
    if len(token) < 30 or len(token) > 128:
        return None
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT a.id,a.username,a.display_name,a.platform_admin,a.setup_required,
            target.id AS support_target_id,target.username AS support_target_username,
            target.display_name AS support_target_name
            FROM alliance_sessions s JOIN alliance_accounts a ON a.id=s.account_id
            LEFT JOIN alliance_support_sessions ss ON ss.session_hash=s.token_hash AND ss.expires_at>UTC_TIMESTAMP(6)
            LEFT JOIN alliance_accounts target ON target.id=ss.target_id AND a.platform_admin=TRUE
            WHERE s.token_hash=%s AND s.expires_at>UTC_TIMESTAMP(6)""", (token_hash(token),))
        account=cur.fetchone()
        if account and account["support_target_id"]:
            return {"id":account["support_target_id"], "username":account["support_target_username"],
                "display_name":account["support_target_name"], "platform_admin":False, "setup_required":False,
                "support_actor_id":account["id"], "support_actor_name":account["display_name"]}
        return account


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
    cursor.execute("SELECT id,slug,name,tag,server_number,timezone FROM alliance_workspaces WHERE slug=%s" +
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


class SupportRequest(BaseModel):
    username: str = Field(max_length=40)
    support_reason: str = Field(min_length=1, max_length=240)


class SupportPassword(SupportRequest):
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
                "platform_admin":bool(account["platform_admin"]), "workspaces":workspaces,
                "support_actor":account.get("support_actor_name")}

    def platform_admin(request):
        actor=require_account(connect_db,request)
        if not actor["platform_admin"] or actor.get("support_actor_id"):
            raise HTTPException(403,"Platform admin access is required.")
        return actor

    @router.get("/platform/alliances")
    def platform_alliances(request: Request):
        platform_admin(request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT w.slug,w.name,w.tag,w.server_number,a.username,a.display_name,a.platform_admin,m.role
                FROM alliance_workspaces w LEFT JOIN alliance_memberships m ON m.alliance_id=w.id
                LEFT JOIN alliance_accounts a ON a.id=m.account_id
                ORDER BY w.server_number,w.name,a.display_name""")
            return {"alliances":cur.fetchall()}

    @router.post("/platform/alliances/{slug}/support")
    def start_support(slug: str, body: SupportRequest, request: Request):
        csrf_check(request)
        actor=platform_admin(request)
        reason=body.support_reason.strip()
        if not reason: raise HTTPException(422,"Enter a support reason.")
        username=normalize_username(body.username)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            cur.execute("""SELECT a.id,a.display_name FROM alliance_accounts a JOIN alliance_memberships m
                ON m.account_id=a.id WHERE m.alliance_id=%s AND a.username=%s AND a.platform_admin=FALSE""",
                (workspace["id"],username))
            target=cur.fetchone()
            if not target: raise HTTPException(404,"Alliance user not found.")
            cur.execute("""INSERT INTO alliance_support_sessions (session_hash,target_id,alliance_id,expires_at)
                VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE target_id=VALUES(target_id),
                alliance_id=VALUES(alliance_id),expires_at=VALUES(expires_at)""",
                (token_hash(request.cookies[COOKIE_NAME]),target["id"],workspace["id"],utcnow()+timedelta(minutes=30)))
            details={"username":username,"reason":reason}
            cur.execute("INSERT INTO platform_audit_log (actor_id,alliance_id,action,details) VALUES (%s,%s,%s,%s)",
                (actor["id"],workspace["id"],"support_started",json.dumps(details)))
            audit(cur,workspace["id"],actor,"support_started",details)
            conn.commit()
        return {"slug":slug}

    @router.post("/platform/support/stop")
    def stop_support(request: Request):
        csrf_check(request)
        token=request.cookies.get(COOKIE_NAME,"")
        if not token: raise HTTPException(401,"Sign in to continue.")
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT a.id,a.display_name,ss.alliance_id,t.username FROM alliance_sessions s
                JOIN alliance_accounts a ON a.id=s.account_id AND a.platform_admin=TRUE
                JOIN alliance_support_sessions ss ON ss.session_hash=s.token_hash
                JOIN alliance_accounts t ON t.id=ss.target_id
                WHERE s.token_hash=%s AND s.expires_at>UTC_TIMESTAMP(6) FOR UPDATE""",(token_hash(token),))
            row=cur.fetchone()
            if not row: raise HTTPException(403,"No support session is active.")
            cur.execute("DELETE FROM alliance_support_sessions WHERE session_hash=%s",(token_hash(token),))
            details={"username":row["username"]}
            cur.execute("INSERT INTO platform_audit_log (actor_id,alliance_id,action,details) VALUES (%s,%s,%s,%s)",
                (row["id"],row["alliance_id"],"support_ended",json.dumps(details)))
            audit(cur,row["alliance_id"],row,"support_ended",details)
            conn.commit()
        return {"ok":True}

    @router.post("/platform/alliances/{slug}/reset-password")
    def support_password(slug: str, body: SupportPassword, request: Request):
        csrf_check(request)
        actor=platform_admin(request)
        reason=body.support_reason.strip()
        if not reason: raise HTTPException(422,"Enter a support reason.")
        username=normalize_username(body.username)
        validate_password(body.new_password)
        with HASH_LIMIT: encoded=PASSWORDS.hash(body.new_password)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            cur.execute("""SELECT a.id FROM alliance_accounts a JOIN alliance_memberships m ON m.account_id=a.id
                WHERE m.alliance_id=%s AND a.username=%s AND a.platform_admin=FALSE FOR UPDATE""",(workspace["id"],username))
            target=cur.fetchone()
            if not target: raise HTTPException(404,"Alliance user not found.")
            cur.execute("""UPDATE alliance_accounts SET password_hash=%s,setup_required=FALSE,setup_token_hash=NULL,
                setup_expires_at=NULL,setup_device_hash=NULL WHERE id=%s""",(encoded,target["id"]))
            cur.execute("DELETE FROM alliance_sessions WHERE account_id=%s",(target["id"],))
            details={"username":username,"reason":reason}
            cur.execute("INSERT INTO platform_audit_log (actor_id,alliance_id,action,details) VALUES (%s,%s,%s,%s)",
                (actor["id"],workspace["id"],"password_reset",json.dumps(details)))
            audit(cur,workspace["id"],actor,"support_password_reset",details)
            conn.commit()
        return {"ok":True}

    @router.get("/avatar")
    def get_avatar(request: Request):
        account=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("SELECT image FROM alliance_account_avatars WHERE account_id=%s",(account["id"],))
            row=cur.fetchone()
        if not row:
            raise HTTPException(404,"No profile picture yet.")
        return Response(content=row["image"],media_type="image/jpeg",headers={"Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff"})

    @router.post("/avatar")
    def set_avatar(request: Request, picture: UploadFile=File(...)):
        csrf_check(request)
        account=require_account(connect_db,request)
        data=picture.file.read(6*1024*1024+1)
        if not data or len(data)>6*1024*1024:
            raise HTTPException(413,"Choose an image smaller than 6 MB.")
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format not in ("JPEG","PNG","WEBP","GIF") or image.width*image.height>25000000:
                    raise HTTPException(422,"Choose a JPG, PNG, WebP, or GIF image under 25 megapixels.")
                image=ImageOps.exif_transpose(image).convert("RGB")
                image=ImageOps.fit(image,(512,512),method=Image.Resampling.LANCZOS)
                output=BytesIO()
                image.save(output,format="JPEG",quality=88,optimize=True)
        except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError) as exc:
            raise HTTPException(422,"That image could not be opened. Try another picture.") from exc
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO alliance_account_avatars (account_id,image) VALUES (%s,%s)
                ON DUPLICATE KEY UPDATE image=VALUES(image)""",(account["id"],output.getvalue()))
            conn.commit()
        return {"ok":True}

    @router.delete("/avatar")
    def delete_avatar(request: Request):
        csrf_check(request)
        account=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM alliance_account_avatars WHERE account_id=%s",(account["id"],))
            conn.commit()
        return {"ok":True}

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
        if actor.get("support_actor_id"):
            raise HTTPException(403, "Exit support mode to manage passwords.")
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
                allowed = role in ("R4", "R5")
                support = bool(actor["platform_admin"] and body.support_reason.strip())
                if not allowed and not (body.kind == "leadership" and support):
                    raise HTTPException(403, "This invitation requires alliance leadership access.")
            token=secrets.token_urlsafe(24)
            expiry=utcnow()+timedelta(hours=body.hours)
            cur.execute("""INSERT INTO alliance_invites
                (token_hash,kind,alliance_id,issuer_id,expires_at) VALUES (%s,%s,%s,%s,%s)""",
                (token_hash(token),body.kind,workspace["id"] if workspace else None,actor["id"],expiry))
            invite_id=cur.lastrowid
            cur.execute("INSERT INTO alliance_invite_links (invite_id,encrypted_token) VALUES (%s,%s)",
                        (invite_id,invite_cipher().encrypt(token.encode('utf-8'))))
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
    def list_invites(slug: str, request: Request, response: Response):
        response.headers['Cache-Control']='private, no-store'
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("""SELECT i.id,i.kind,i.expires_at,i.revoked_at,i.used_at,i.use_count,i.created_at,
                i.token_hash,l.encrypted_token FROM alliance_invites i
                LEFT JOIN alliance_invite_links l ON l.invite_id=i.id
                WHERE i.alliance_id=%s ORDER BY i.created_at DESC LIMIT 100""",
                (workspace["id"],))
            return {"invites":[recover_invite(row) for row in cur.fetchall()]}

    @router.get("/referrals")
    def list_referrals(request: Request, response: Response):
        response.headers['Cache-Control']='private, no-store'
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT i.id,i.expires_at,i.revoked_at,i.used_at,i.use_count,i.created_at,
                i.token_hash,l.encrypted_token FROM alliance_invites i
                LEFT JOIN alliance_invite_links l ON l.invite_id=i.id
                WHERE i.kind='referral' AND i.issuer_id=%s ORDER BY i.created_at DESC LIMIT 100""",
                (actor["id"],))
            return {"referrals":[recover_invite(row) for row in cur.fetchall()]}

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

"""Opt-in browser push subscriptions and delivery for in-app notifications."""

from hashlib import sha256
import json
import os
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush

from alliance_auth import csrf_check, require_account


PUSH_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS alliance_push_subscriptions (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        endpoint_hash BINARY(32) NOT NULL UNIQUE, account_id BIGINT UNSIGNED NOT NULL,
        subscription_json JSON NOT NULL, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_push_deliveries (
        notification_id BIGINT UNSIGNED NOT NULL, subscription_id BIGINT UNSIGNED NOT NULL,
        sent_at DATETIME(6) NULL, attempts TINYINT UNSIGNED NOT NULL DEFAULT 0,
        PRIMARY KEY (notification_id,subscription_id), KEY idx_push_pending (sent_at,attempts)
    ) ENGINE=InnoDB""",
)


class Subscription(BaseModel):
    endpoint: str = Field(min_length=15,max_length=2048)
    keys: dict[str,str]


def validate_subscription(body):
    endpoint=urlsplit(body.endpoint)
    allowed=("fcm.googleapis.com","updates.push.services.mozilla.com","push.apple.com","notify.windows.com")
    host=endpoint.hostname or ""
    try:
        port=endpoint.port
    except ValueError as exc:
        raise HTTPException(422,"This browser's push service is not supported.") from exc
    if (endpoint.scheme!="https" or endpoint.username or endpoint.password or port not in (None,443)
            or not any(host==item or host.endswith("."+item) for item in allowed)):
        raise HTTPException(422,"This browser's push service is not supported.")
    if set(body.keys)!={"auth","p256dh"} or any(not 10<=len(value)<=200 for value in body.keys.values()):
        raise HTTPException(422,"Invalid push subscription.")


def dispatch_push(cursor):
    private=os.getenv("ALLIANCE_VAPID_PRIVATE_KEY","")
    if not private:
        return
    cursor.execute("""INSERT IGNORE INTO alliance_push_deliveries (notification_id,subscription_id)
        SELECT n.id,s.id FROM alliance_notifications n JOIN alliance_push_subscriptions s ON s.account_id=n.account_id
        WHERE n.created_at>=UTC_TIMESTAMP(6)-INTERVAL 1 DAY""")
    cursor.execute("""SELECT n.id,n.title,n.target,s.id AS subscription_id,s.subscription_json
        FROM alliance_push_deliveries d JOIN alliance_notifications n ON n.id=d.notification_id
        JOIN alliance_push_subscriptions s ON s.id=d.subscription_id
        WHERE d.sent_at IS NULL AND d.attempts<5 ORDER BY n.id LIMIT 20 FOR UPDATE SKIP LOCKED""")
    for row in cursor.fetchall():
        try:
            info=row["subscription_json"]
            if isinstance(info,str):info=json.loads(info)
            webpush(subscription_info=info,data=json.dumps({"title":"Alliance Tools",
                "body":row["title"],"target":row["target"]}),vapid_private_key=private,
                vapid_claims={"sub":os.getenv("ALLIANCE_VAPID_SUBJECT","https://lastwar-rosterscanner.onrender.com/")},
                ttl=86400,headers={"Topic":"alliance-"+str(row["id"])},timeout=8)
            cursor.execute("""UPDATE alliance_push_deliveries SET sent_at=UTC_TIMESTAMP(6)
                WHERE notification_id=%s AND subscription_id=%s""",(row["id"],row["subscription_id"]))
        except WebPushException as exc:
            status=exc.response.status_code if exc.response is not None else None
            if status in (404,410):
                cursor.execute("DELETE FROM alliance_push_subscriptions WHERE id=%s",(row["subscription_id"],))
            cursor.execute("""UPDATE alliance_push_deliveries SET attempts=attempts+1
                WHERE notification_id=%s AND subscription_id=%s""",(row["id"],row["subscription_id"]))


def install_push(app,connect_db):
    router=APIRouter(prefix="/api/auth/push")

    @router.get("/config")
    def config(request: Request):
        require_account(connect_db,request)
        public=os.getenv("ALLIANCE_VAPID_PUBLIC_KEY","")
        return {"configured":bool(public and os.getenv("ALLIANCE_VAPID_PRIVATE_KEY","")),"public_key":public}

    @router.post("/subscribe")
    def subscribe(body: Subscription,request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        if not os.getenv("ALLIANCE_VAPID_PUBLIC_KEY") or not os.getenv("ALLIANCE_VAPID_PRIVATE_KEY"):
            raise HTTPException(503,"Phone notifications are being set up.")
        validate_subscription(body)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO alliance_push_subscriptions (endpoint_hash,account_id,subscription_json)
                VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE account_id=VALUES(account_id),
                subscription_json=VALUES(subscription_json)""",
                (sha256(body.endpoint.encode()).digest(),actor["id"],body.model_dump_json()))
            conn.commit()
        return {"ok":True}

    @router.post("/unsubscribe")
    def unsubscribe(body: Subscription,request: Request):
        csrf_check(request)
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM alliance_push_subscriptions WHERE endpoint_hash=%s AND account_id=%s",
                        (sha256(body.endpoint.encode()).digest(),actor["id"]))
            conn.commit()
        return {"ok":True}

    app.include_router(router)

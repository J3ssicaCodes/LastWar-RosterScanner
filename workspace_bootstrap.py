"""Initialize the verified workspace copy before serving workspace traffic."""

import os

from alliance_auth import ACCOUNT_SCHEMA
from alliance_claims import CLAIM_SCHEMA
from alliance_leadership import LEADERSHIP_SCHEMA
from alliance_push import PUSH_SCHEMA
from workspace_migration import MIGRATION_KEY, migrate_rubies


def initialize_workspace(connect_db):
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'alliance_migrations'")
        exists=bool(cur.fetchone())
        complete=False
        if exists:
            cur.execute("SELECT 1 FROM alliance_migrations WHERE migration_key=%s",(MIGRATION_KEY,))
            complete=bool(cur.fetchone())
        if not complete:
            if os.getenv("ALLIANCE_WRITE_PAUSED","").lower()!="true":
                raise RuntimeError("Pause legacy writes before activating alliance workspaces.")
            database=os.getenv("ALLIANCE_EXPECTED_DATABASE","")
            backup=os.getenv("ALLIANCE_BACKUP_SHA256","")
            password=os.getenv("ALLIANCE_BOOTSTRAP_PASSWORD","")
            code=os.getenv("ALLIANCE_BOOTSTRAP_CODE","")
            if not all((database,backup,password,code)):
                raise RuntimeError("Workspace bootstrap settings are incomplete.")
            migrate_rubies(conn,database,backup,password,setup_token=code)
        for statement in (*ACCOUNT_SCHEMA,*CLAIM_SCHEMA,*LEADERSHIP_SCHEMA,*PUSH_SCHEMA):
            cur.execute(statement)
        conn.commit()

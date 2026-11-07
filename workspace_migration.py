"""Prepare and verify the Rubies workspace from a backed-up legacy database.

Run the cutover copy with uploads drained and all legacy writes paused. The live
legacy tables remain available as a recovery source. Schema changes use shadow
tables because MySQL DDL commits separately from the transactional data copy.
"""

import argparse
from datetime import datetime, timedelta, timezone
from getpass import getpass
from hashlib import sha256
import json
import re
from secrets import token_urlsafe

from argon2 import PasswordHasher


RUBIES_SLUG = "1439-rvby"
MIGRATION_KEY = "rubies_legacy_v1"
TABLES = {
    "alliance_admin_state": "workspace_admin_state",
    "media_missing_players": "workspace_media_missing_players",
    "media_scan_audit": "workspace_media_scan_audit",
    "media_scan_jobs": "workspace_media_scan_jobs",
    "player list": "workspace_players",
    "player_list_imports": "workspace_player_imports",
    "player_name_dictionary": "workspace_name_dictionary",
    "player_stat_history": "workspace_player_stat_history",
    "roster upload": "workspace_roster_reviews",
    "storm roster": "workspace_storm_roster",
    "train_assignments": "workspace_train_assignments",
    "train_email_templates": "workspace_train_email_templates",
    "train_emails": "workspace_train_emails",
    "train_event_names": "workspace_train_event_names",
    "train_restrictions": "workspace_train_restrictions",
    "train_settings": "workspace_train_settings",
    "vs_points": "workspace_vs_points",
    "vs_scan_rows": "workspace_vs_scan_rows",
}

# This table accounts for one provider quota shared by the service.
SHARED_TABLES = {"vs_vision_usage"}

WORKSPACE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS alliance_workspaces (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        slug VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
        server_number INT UNSIGNED NOT NULL,
        tag VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
        name VARCHAR(80) NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        UNIQUE KEY uq_alliance_server_tag (server_number, tag)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_accounts (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(40) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
        display_name VARCHAR(80) NOT NULL,
        password_hash VARCHAR(255) CHARACTER SET ascii NOT NULL,
        platform_admin BOOLEAN NOT NULL DEFAULT FALSE,
        setup_required BOOLEAN NOT NULL DEFAULT FALSE,
        setup_token_hash BINARY(32) NULL,
        setup_expires_at DATETIME(6) NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_memberships (
        alliance_id BIGINT UNSIGNED NOT NULL,
        account_id BIGINT UNSIGNED NOT NULL,
        role ENUM('R4', 'R5') NOT NULL,
        r5_alliance_id BIGINT UNSIGNED AS (IF(role='R5', alliance_id, NULL)) STORED,
        PRIMARY KEY (alliance_id, account_id),
        UNIQUE KEY uq_alliance_r5 (r5_alliance_id),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id),
        FOREIGN KEY (account_id) REFERENCES alliance_accounts(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_audit_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL,
        actor_id BIGINT UNSIGNED NULL,
        actor_name VARCHAR(80) NOT NULL,
        action VARCHAR(60) NOT NULL,
        details JSON NOT NULL,
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_alliance_audit_time (alliance_id, created_at, id),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_migrations (
        migration_key VARCHAR(60) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL,
        backup_sha256 CHAR(64) CHARACTER SET ascii NOT NULL,
        completed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
)


def identifier(name):
    return "`" + name.replace("`", "``") + "`"


def inventory(cursor):
    cursor.execute("SHOW FULL TABLES WHERE Table_type='BASE TABLE'")
    existing = {next(iter(row.values())) for row in cursor.fetchall()}
    managed = set(TABLES.values()) | {
        "alliance_workspaces", "alliance_accounts", "alliance_memberships",
        "alliance_audit_log", "alliance_migrations",
    }
    expected = set(TABLES) | SHARED_TABLES
    missing = expected - existing
    extra = existing - expected - managed
    if missing or extra:
        raise RuntimeError(f"Review the migration inventory. Missing={sorted(missing)}, additional={sorted(extra)}")
    counts = {}
    for name in sorted(expected):
        cursor.execute(f"SELECT COUNT(*) AS n FROM {identifier(name)}")
        counts[name] = cursor.fetchone()["n"]
    return counts


def indexes(cursor, table):
    cursor.execute(f"SHOW INDEX FROM {identifier(table)}")
    keys = {}
    for row in sorted(cursor.fetchall(), key=lambda row: (row["Key_name"], row["Seq_in_index"])):
        if not row["Non_unique"]:
            if row["Sub_part"] or not row["Column_name"]:
                raise RuntimeError(f"Review unique index {row['Key_name']} in {table}.")
            keys.setdefault(row["Key_name"], []).append(row["Column_name"])
    return keys


def columns(cursor, table):
    cursor.execute(f"SHOW FULL COLUMNS FROM {identifier(table)}")
    return {row["Field"]: row for row in cursor.fetchall()}


def prepare_shadow(cursor, source, target):
    cursor.execute(f"CREATE TABLE IF NOT EXISTS {identifier(target)} LIKE {identifier(source)}")
    original = columns(cursor, source)
    original_keys = indexes(cursor, source)
    expected_keys = {key: ["alliance_id", *fields] for key, fields in original_keys.items()}
    primary = original_keys["PRIMARY"]
    auto_increment = "auto_increment" in original[primary[0]]["Extra"]
    if auto_increment:
        expected_keys["uq_workspace_record"] = expected_keys["PRIMARY"]
        expected_keys["PRIMARY"] = primary
    target_columns = columns(cursor, target)
    if "alliance_id" not in target_columns:
        clauses = [
            "ADD COLUMN alliance_id BIGINT UNSIGNED NOT NULL",
            "ADD FOREIGN KEY (alliance_id) REFERENCES alliance_workspaces(id)",
            "COMMENT='alliance_tools:workspace_data'",
        ]
        for key, fields in expected_keys.items():
            if original_keys.get(key) == fields:
                continue
            field_sql = ", ".join(map(identifier, fields))
            if key == "PRIMARY":
                clauses.extend(("DROP PRIMARY KEY", f"ADD PRIMARY KEY ({field_sql})"))
                continue
            if key in original_keys:
                clauses.append(f"DROP INDEX {identifier(key)}")
            clauses.append(f"ADD UNIQUE KEY {identifier(key)} ({field_sql})")
        cursor.execute(f"ALTER TABLE {identifier(target)} " + ", ".join(clauses))
        target_columns = columns(cursor, target)
    if set(target_columns) != set(original) | {"alliance_id"}:
        raise RuntimeError(f"Shadow schema for {source} differs from the source columns.")
    for field, definition in original.items():
        comparable = ("Type", "Collation", "Null", "Default", "Extra")
        if any(definition[key] != target_columns[field][key] for key in comparable):
            raise RuntimeError(f"Shadow column {source}.{field} differs from the source.")
    if indexes(cursor, target) != expected_keys:
        raise RuntimeError(f"Unique keys in {target} are incompatible with alliance scoping.")
    if auto_increment:
        cursor.execute("""SELECT AUTO_INCREMENT AS next_id FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s""", (source,))
        next_id = int(cursor.fetchone()["next_id"])
        cursor.execute(f"ALTER TABLE {identifier(target)} AUTO_INCREMENT={next_id}")
    return list(original), primary


def copy_and_verify(cursor, source, target, fields, primary, alliance_id):
    legacy, scoped = identifier(source), identifier(target)
    names = ", ".join(map(identifier, fields))
    cursor.execute(f"""INSERT INTO {scoped} (alliance_id, {names})
        SELECT %s, {names} FROM {legacy}""", (alliance_id,))
    inserted = cursor.rowcount
    on_key = " AND ".join(f"old.{identifier(name)}=new.{identifier(name)}" for name in primary)
    equal = " AND ".join(
        f"CAST(old.{identifier(name)} AS BINARY) <=> CAST(new.{identifier(name)} AS BINARY)"
        for name in fields
    )
    cursor.execute(f"""SELECT COUNT(*) AS n FROM {legacy} old
        LEFT JOIN {scoped} new ON new.alliance_id=%s AND {on_key}
        WHERE new.alliance_id IS NULL OR NOT ({equal})""", (alliance_id,))
    if cursor.fetchone()["n"]:
        raise RuntimeError(f"Values in {source} differed after copying.")
    cursor.execute(f"SELECT COUNT(*) AS n FROM {scoped} WHERE alliance_id=%s", (alliance_id,))
    if cursor.fetchone()["n"] != inserted:
        raise RuntimeError(f"Unexpected rows were already present in {target}.")
    return inserted


def migrate_rubies(connection, expected_database, backup_sha256, temporary_password):
    """Copy legacy rows and seed the owner in one transaction after shadow DDL."""
    if not re.fullmatch(r"[0-9a-f]{64}", backup_sha256):
        raise ValueError("Provide the verified backup's lowercase SHA-256 digest.")
    setup_token = token_urlsafe(32)
    password_hash = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(temporary_password)
    setup_hash = sha256(setup_token.encode()).digest()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS name")
        if cursor.fetchone()["name"] != expected_database:
            raise RuntimeError("The connected database does not match the expected database.")
        lock = "alliance_migration_" + sha256(expected_database.encode()).hexdigest()[:32]
        cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (lock,))
        if cursor.fetchone()["acquired"] != 1:
            raise RuntimeError("Another workspace migration is running.")
        try:
            inventory(cursor)
            for statement in WORKSPACE_SCHEMA:
                cursor.execute(statement)
            cursor.execute("SELECT alliance_id FROM alliance_migrations WHERE migration_key=%s", (MIGRATION_KEY,))
            if cursor.fetchone():
                return None
            shadows = {source: prepare_shadow(cursor, source, target) for source, target in TABLES.items()}
            connection.commit()
            cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            cursor.execute("SELECT COUNT(*) AS n FROM media_scan_jobs WHERE status IN ('queued','scanning')")
            if cursor.fetchone()["n"]:
                raise RuntimeError("Drain running and queued scans before the migration.")
            cursor.execute("""INSERT INTO alliance_workspaces (slug, server_number, tag, name)
                VALUES (%s, 1439, 'RVBY', 'Rubies')""", (RUBIES_SLUG,))
            alliance_id = cursor.lastrowid
            copied = {
                source: copy_and_verify(cursor, source, target, *shadows[source], alliance_id)
                for source, target in TABLES.items()
            }
            cursor.execute("""INSERT INTO alliance_accounts
                (username, display_name, password_hash, platform_admin, setup_required, setup_token_hash, setup_expires_at)
                VALUES ('admin', 'Lady Jess', %s, TRUE, TRUE, %s, %s)""", (password_hash, setup_hash, expires_at))
            cursor.execute("""INSERT INTO alliance_memberships (alliance_id, account_id, role)
                VALUES (%s, %s, 'R5')""", (alliance_id, cursor.lastrowid))
            cursor.execute("""INSERT INTO alliance_audit_log (alliance_id, actor_name, action, details)
                VALUES (%s, 'System', 'legacy_migration', %s)""",
                (alliance_id, json.dumps({"copied": copied, "owner_role": "R5"})))
            cursor.execute("""INSERT INTO alliance_migrations (migration_key, alliance_id, backup_sha256)
                VALUES (%s, %s, %s)""", (MIGRATION_KEY, alliance_id, backup_sha256))
            connection.commit()
            return copied, setup_token
        except Exception:
            connection.rollback()
            raise
        finally:
            cursor.execute("SELECT RELEASE_LOCK(%s)", (lock,))


def main():
    from app import db_connect

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Exact expected database name")
    parser.add_argument("--apply", action="store_true", help="Copy Rubies into the workspace tables")
    parser.add_argument("--writes-paused", action="store_true", help="Confirm legacy writes are paused")
    parser.add_argument("--backup-sha256", help="Digest of the verified pre-cutover backup")
    args = parser.parse_args()
    if args.apply and (not args.writes_paused or not args.backup_sha256):
        parser.error("--apply requires --writes-paused and --backup-sha256")
    with db_connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE() AS name")
        if cursor.fetchone()["name"] != args.database:
            parser.error("The connected database does not match --database")
        if not args.apply:
            print(json.dumps(inventory(cursor), indent=2))
            return
        password = getpass("Private setup password: ")
        if not password:
            parser.error("A setup password is required")
        result = migrate_rubies(connection, args.database, args.backup_sha256, password)
        if result is None:
            print("The Rubies migration already completed.")
            return
        copied, setup_token = result
        print(json.dumps(copied, indent=2))
        print("One-time setup code, valid for 24 hours. Keep private.")
        print(setup_token)


if __name__ == "__main__":
    main()

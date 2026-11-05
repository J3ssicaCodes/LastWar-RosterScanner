"""Authorized, transactional reset for Alliance Tools data."""
import os
import secrets
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Literal

# Legacy tables predate the reset marker. Mark new user tables with
# COMMENT='alliance_tools:user_data' in their CREATE TABLE statement.
USER_TABLES = {
    'storm roster', 'player list', 'vs_points', 'roster upload', 'vs_scan_rows',
    'player_list_imports', 'player_stat_history', 'media_scan_audit', 'media_scan_jobs',
}
PRESERVED_TABLES = {'player_name_dictionary', 'alliance_admin_state'}
USER_DATA_MARKER = 'alliance_tools:user_data'
CLEAR_PATH = '/api/admin/clear-data'


def initialize_admin_state(cursor):
    cursor.execute("""CREATE TABLE IF NOT EXISTS alliance_admin_state (
        id TINYINT UNSIGNED PRIMARY KEY,
        reset_id CHAR(36) NOT NULL
    ) ENGINE=InnoDB""")
    # Keep the reset marker even when all the user tables are empty.


def reset_order(cursor):
    cursor.execute("""SELECT TABLE_NAME AS name, TABLE_COMMENT AS comment, ENGINE AS engine
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE'""")
    tables = {
        row['name']: row['engine'] for row in cursor.fetchall()
        if row['name'].lower() not in PRESERVED_TABLES
        and (row['name'] in USER_TABLES or row['comment'] == USER_DATA_MARKER)
    }
    if any(engine != 'InnoDB' for engine in tables.values()):
        raise HTTPException(409, 'Every reset table must support transactional deletion.')
    cursor.execute("""SELECT TABLE_SCHEMA AS child_schema, TABLE_NAME AS child,
        REFERENCED_TABLE_NAME AS parent, DATABASE() AS app_schema
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE REFERENCED_TABLE_SCHEMA=DATABASE()""")
    children = {name: set() for name in tables}
    for row in cursor.fetchall():
        if row['parent'] not in tables:
            continue
        if row['child_schema'] != row['app_schema'] or row['child'] not in tables:
            raise HTTPException(409, 'A table outside the reset depends on app data.')
        if row['child'] != row['parent']:
            children[row['parent']].add(row['child'])
    ordered = []
    while children:
        leaves = sorted(name for name, deps in children.items() if not deps)
        if not leaves:
            raise HTTPException(409, 'Reset tables have circular dependencies.')
        ordered.extend(leaves)
        for name in leaves:
            del children[name]
        for deps in children.values():
            deps.difference_update(leaves)
    return ordered


class ClearData(BaseModel):
    confirmation: Literal['CLEAR DATA']


def install_admin_reset(app, connect_db, jobs, job_lock):
    active_writes = 0
    clearing = False

    @app.middleware('http')
    async def exclude_writes_during_reset(request, call_next):
        nonlocal active_writes, clearing
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return await call_next(request)
        is_reset = request.method == 'POST' and request.url.path.rstrip('/') == CLEAR_PATH
        if clearing or (is_reset and active_writes):
            return JSONResponse({'detail': 'An upload or save is in progress. Try again shortly.'}, status_code=409)
        if is_reset:
            clearing = True
        else:
            active_writes += 1
        try:
            return await call_next(request)
        finally:
            if is_reset:
                clearing = False
            else:
                active_writes -= 1

    @app.get('/api/admin/status')
    def status():
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('SELECT reset_id FROM alliance_admin_state WHERE id=1')
            row = cur.fetchone()
        return JSONResponse({
            'enabled': len(os.getenv('ADMIN_RESET_PASSWORD', '')) >= 16,
            'reset_id': row['reset_id'] if row else None,
        }, headers={'Cache-Control': 'no-store'})

    @app.post(CLEAR_PATH)
    def clear_data(body: ClearData, request: Request):
        password = os.getenv('ADMIN_RESET_PASSWORD', '')
        if len(password) < 16:
            raise HTTPException(503, 'Set an admin password in Render before using Clear Data.')
        expected = ('Bearer ' + password).encode()
        supplied = request.headers.get('authorization', '').encode()
        if not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, 'Incorrect admin password.')
        with job_lock:
            if any(job['status'] in ('queued', 'scanning') for job in jobs.values()):
                raise HTTPException(409, 'Wait for active and queued scans to finish.')
            reset_id = str(uuid.uuid4())
            try:
                with connect_db() as conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT id FROM media_scan_jobs WHERE status IN ('queued','scanning') LIMIT 1 FOR UPDATE")
                            if cur.fetchone():
                                raise HTTPException(409, 'Wait for active and queued scans to finish.')
                            ordered = reset_order(cur)
                            deleted = 0
                            for name in ordered:
                                identifier = '`' + name.replace('`', '``') + '`'
                                cur.execute(f'DELETE FROM {identifier}')
                                deleted += cur.rowcount
                            cur.execute("""INSERT INTO alliance_admin_state (id,reset_id) VALUES (1,%s)
                                ON DUPLICATE KEY UPDATE reset_id=VALUES(reset_id)""", (reset_id,))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(503, 'Clear Data failed. No changes were committed.') from exc
            jobs.clear()
        return {'reset_id': reset_id, 'tables_cleared': len(ordered), 'rows_deleted': deleted}

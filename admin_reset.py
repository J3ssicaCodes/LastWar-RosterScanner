"""Transactional reset for Alliance Tools data."""
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal

# Legacy tables predate the reset marker. Mark new user tables with
# COMMENT='alliance_tools:user_data' in their CREATE TABLE statement.
USER_TABLES = {
    'storm roster': 'Storm Roster',
    'player list': 'Player List',
    'vs_points': 'VS Points',
    'roster upload': 'Pending Player List Reviews',
    'vs_scan_rows': 'Pending VS Reviews',
    'player_list_imports': 'Player List Import History',
    'player_stat_history': 'Player Stat History',
    'media_scan_audit': 'Scan Diagnostics',
    'media_scan_jobs': 'Scan Queue and History',
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


def reset_tables(cursor):
    cursor.execute("""SELECT TABLE_NAME AS name, TABLE_COMMENT AS comment, ENGINE AS engine
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE'""")
    return {
        row['name']: row['engine'] for row in cursor.fetchall()
        if row['name'].lower() not in PRESERVED_TABLES
        and (row['name'] in USER_TABLES or row['comment'] == USER_DATA_MARKER)
    }


def table_label(name):
    return USER_TABLES.get(name, name.replace('_', ' ').title())


def reset_order(cursor, selected):
    available = reset_tables(cursor)
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= available.keys():
        raise HTTPException(400, 'Select valid tables to clear.')
    tables = {name: available[name] for name in selected}
    # These review rows join against scan jobs even without a database foreign key.
    if 'media_scan_jobs' in tables and 'roster upload' in available and 'roster upload' not in tables:
        cursor.execute('SELECT 1 FROM `roster upload` LIMIT 1')
        if cursor.fetchone():
            raise HTTPException(409, 'Pending Player List Reviews depend on Scan Queue and History. Finish the reviews or select both tables.')
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
    tables: list[str] = Field(min_length=1, max_length=500)


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
            'reset_id': row['reset_id'] if row else None,
        }, headers={'Cache-Control': 'no-store'})

    @app.get('/api/admin/tables')
    def list_tables():
        with connect_db() as conn, conn.cursor() as cur:
            tables = reset_tables(cur)
        return {'tables': [
            {'id': name, 'label': table_label(name)}
            for name in sorted(tables, key=table_label)
        ]}

    @app.post(CLEAR_PATH)
    def clear_data(body: ClearData):
        with job_lock:
            if any(job['status'] in ('queued', 'scanning') for job in jobs.values()):
                raise HTTPException(409, 'Wait for active and queued scans to finish.')
            try:
                with connect_db() as conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute("SELECT id FROM media_scan_jobs WHERE status IN ('queued','scanning') LIMIT 1 FOR UPDATE")
                            if cur.fetchone():
                                raise HTTPException(409, 'Wait for active and queued scans to finish.')
                            ordered = reset_order(cur, body.tables)
                            deleted = 0
                            for name in ordered:
                                identifier = '`' + name.replace('`', '``') + '`'
                                cur.execute(f'DELETE FROM {identifier}')
                                deleted += cur.rowcount
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(503, 'Clear Data failed. No changes were committed.') from exc
            if 'media_scan_jobs' in ordered:
                jobs.clear()
        return {'tables_cleared': len(ordered), 'rows_deleted': deleted}

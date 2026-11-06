"""Backend for the Player List tab.

Only the Player List API accesses MySQL. Roster Scanner remains client-side.
Database passwords are read from Render environment variables,
never returned to the browser or written to the frontend source.
"""
from __future__ import annotations

import base64
import binascii
import csv
import io
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import Any

import pymysql
from pymysql.cursors import DictCursor
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from player_identity import match_master, name_key, corrected_name, load_name_corrections, learn_name_correction
from player_list_ocr import scan_upload
from admin_reset import initialize_admin_state, install_admin_reset

BASE = Path(__file__).parent
app = FastAPI(title='Alliance Tools Player List API', docs_url=None, redoc_url=None)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='member-list-ocr')
jobs: dict[str, dict[str, Any]] = {}
job_lock = threading.RLock()
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
MAX_FILES = 75
DATABASE_OK = False
DATABASE_ERROR = ''
scan_logger = logging.getLogger('uvicorn.error')


def _env(*names: str) -> str | None:
    # Environment keys vary between Render database providers.
    mapping = {k.upper(): v for k, v in os.environ.items() if v}
    return next((mapping.get(n.upper()) for n in names if mapping.get(n.upper())), None)


def _db_config():
    """Read a cPanel / MySQL connection from Render environment variables.

    Secrets stay on the server. Never include configuration values in errors,
    logs, or HTTP responses.
    """
    uri = _env('DATABASE_URL', 'MYSQL_URL', 'MYSQL_DATABASE_URL', 'DB_URL', 'DB_CONNECTION_STRING')
    if uri:
        parts = urlsplit(uri)
        if parts.scheme not in ('mysql', 'mysql+pymysql'):
            raise RuntimeError('The database URL must use MySQL, not PostgreSQL.')
        return {
            'host': parts.hostname,
            'port': parts.port or 3306,
            'user': unquote(parts.username or ''),
            'password': unquote(parts.password or ''),
            'database': unquote(parts.path.lstrip('/')),
        }
    host = _env('DB_HOST', 'MYSQL_HOST', 'DBHOST', 'DB_HOSTNAME', 'DATABASE_HOST')
    user = _env('DB_USER', 'DB_USERNAME', 'MYSQL_USER', 'DATABASE_USER', 'DBUSER')
    password = _env('DB_PASSWORD', 'MYSQL_PASSWORD', 'DATABASE_PASSWORD', 'DBPASSWORD')
    database = _env('DB_NAME', 'MYSQL_DATABASE', 'DATABASE_NAME', 'DB_DATABASE', 'DB_DATABASE_NAME', 'DBNAME')
    port = _env('DB_PORT', 'MYSQL_PORT', 'DATABASE_PORT', 'DBPORT') or '3306'
    if not all((host, user, password, database)):
        missing = [name for name, present in (
            ('DB_HOST', host), ('DB_USER', user), ('DB_PASSWORD', password),
            ('DB_NAME', database)) if not present]
        print('Player List missing MySQL environment variable names:', missing, flush=True)
        raise RuntimeError('MySQL connection settings are incomplete.')
    try:
        parsed_port = int(port)
    except ValueError as exc:
        raise RuntimeError('DB_PORT must be numeric.') from exc
    return {'host': host, 'port': parsed_port, 'user': user,
            'password': password, 'database': database}


def db_connect():
    params = _db_config()
    return pymysql.connect(**params, charset='utf8mb4', cursorclass=DictCursor,
                           connect_timeout=9, read_timeout=20, write_timeout=20,
                           autocommit=False)


def initialize_db():
    """Idempotent MySQL schema creation; never deletes existing player data."""
    global DATABASE_OK, DATABASE_ERROR
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                initialize_admin_state(cur)
                cur.execute("""CREATE TABLE IF NOT EXISTS `player list` (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    player_name VARCHAR(255) NOT NULL,
                    normalized_name VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    player_rank VARCHAR(2) NOT NULL,
                    power_millions DECIMAL(12,1) NULL,
                    power BIGINT UNSIGNED NULL,
                    kills BIGINT UNSIGNED NULL,
                    donations BIGINT UNSIGNED NULL,
                    hq_level SMALLINT UNSIGNED NULL,
                    birthday DATE NULL,
                    avatar_image MEDIUMBLOB NULL,
                    avatar_mime VARCHAR(24) NOT NULL DEFAULT 'image/webp',
                    avatar_updated_at DATETIME(6) NULL,
                    is_new BOOLEAN NOT NULL DEFAULT TRUE,
                    possibly_removed BOOLEAN NOT NULL DEFAULT FALSE,
                    first_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    last_import_id CHAR(36) NULL,
                    UNIQUE KEY uq_player_list_normalized_name (normalized_name),
                    KEY idx_player_list_rank_power (player_rank,power_millions),
                    KEY idx_player_list_import (last_import_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
                cur.execute("""CREATE TABLE IF NOT EXISTS player_list_imports (
                    id CHAR(36) NOT NULL PRIMARY KEY,
                    imported_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    is_full_capture BOOLEAN NOT NULL,
                    scanned_count INT UNSIGNED NOT NULL,
                    created_count INT UNSIGNED NOT NULL,
                    updated_count INT UNSIGNED NOT NULL,
                    possible_removed_count INT UNSIGNED NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""")
                # Existing installations need additional columns, not a new table.
                for field, definition in (
                    ('birthday', 'DATE NULL'),
                    ('avatar_image', 'MEDIUMBLOB NULL'),
                    ('avatar_updated_at', 'DATETIME(6) NULL'),
                    ('avatar_mime', "VARCHAR(24) NOT NULL DEFAULT 'image/webp'"),
                    ('power','BIGINT UNSIGNED NULL'),
                    ('kills','BIGINT UNSIGNED NULL'),
                    ('donations','BIGINT UNSIGNED NULL'),
                ):
                    cur.execute('SHOW COLUMNS FROM `player list` LIKE %s',(field,))
                    if not cur.fetchone():
                        cur.execute(f'ALTER TABLE `player list` ADD COLUMN `{field}` {definition}')
                cur.execute('''CREATE TABLE IF NOT EXISTS player_stat_history (
                    player_id BIGINT UNSIGNED NOT NULL,
                    import_id VARCHAR(36) NOT NULL,
                    recorded_at DATETIME(6) NOT NULL,
                    power BIGINT UNSIGNED NULL,
                    power_millions DECIMAL(12,1) NULL,
                    kills BIGINT UNSIGNED NULL,
                    donations BIGINT UNSIGNED NULL,
                    power_upload_date DATE NULL,
                    power_millions_upload_date DATE NULL,
                    kills_upload_date DATE NULL,
                    donations_upload_date DATE NULL,
                    PRIMARY KEY (player_id, import_id),
                    KEY idx_player_stat_history_time (player_id, recorded_at)
                ) ENGINE=InnoDB''')
                for field, definition in (
                    ('donations','BIGINT UNSIGNED NULL'),
                    *((f'{stat}_upload_date','DATE NULL') for stat in ('power','power_millions','kills','donations')),
                ):
                    cur.execute('SHOW COLUMNS FROM player_stat_history LIKE %s',(field,))
                    if not cur.fetchone():
                        cur.execute(f'ALTER TABLE player_stat_history ADD COLUMN {field} {definition}')
                cur.execute("""INSERT IGNORE INTO player_stat_history
                    (player_id,import_id,recorded_at,power,power_millions,kills)
                    SELECT p.id,'baseline',UTC_TIMESTAMP(6),p.power,p.power_millions,p.kills
                    FROM `player list` p
                    WHERE (p.power IS NOT NULL OR p.power_millions IS NOT NULL OR p.kills IS NOT NULL)
                      AND NOT EXISTS (SELECT 1 FROM player_stat_history h WHERE h.player_id=p.id AND
                          (h.power IS NOT NULL OR h.power_millions IS NOT NULL OR h.kills IS NOT NULL))""")
                cur.execute("""INSERT IGNORE INTO player_stat_history
                    (player_id,import_id,recorded_at,donations,donations_upload_date)
                    SELECT p.id,'donation-baseline',UTC_TIMESTAMP(6),p.donations,UTC_DATE()
                    FROM `player list` p WHERE p.donations IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM player_stat_history h WHERE h.player_id=p.id AND h.donations IS NOT NULL)""")
                cur.execute('SHOW TABLES LIKE %s', ('player list',))
                if cur.fetchone() is None:
                    raise RuntimeError('Player List table not visible after creation.')
            conn.commit()
        DATABASE_OK, DATABASE_ERROR = True, ''
        print('Player List MySQL tables initialized', flush=True)
    except Exception as exc:
        DATABASE_OK, DATABASE_ERROR = False, type(exc).__name__
        # Only log class name, never secret-bearing exception text.
        print('Player List MySQL initialization unavailable:', type(exc).__name__, flush=True)


@app.on_event('startup')
def startup():
    # Never interrupt the original Roster Scanner if the DB is unavailable.
    initialize_db()


@app.get('/')
@app.head('/')
def home():
    return FileResponse(BASE / 'index.html', media_type='text/html', headers={'Cache-Control': 'no-cache'})


@app.get('/api/player-list/health')
def health():
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SHOW TABLES LIKE %s', ('player list',))
                if not cur.fetchone():
                    raise RuntimeError('Player List table is missing.')
                cur.execute('SELECT COUNT(*) AS n FROM `player list`')
                count = int(cur.fetchone()['n'])
        return {'online': True, 'database': 'connected', 'table_ready': True,
                'player_count': count}
    except Exception:
        return JSONResponse(status_code=503, content={
            'online': True, 'database': 'disconnected', 'table_ready': False,
            'hint': 'Check the MySQL connection settings and Render logs.'})


@app.get('/api/player-list')
def list_players():
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT id, player_name AS name, player_rank AS `rank`,
                    power_millions, power, kills, donations, hq_level, birthday, is_new, possibly_removed,
                    avatar_image IS NOT NULL AS has_avatar, avatar_updated_at,
                    first_seen_at, last_seen_at, updated_at
                    FROM `player list`
                    ORDER BY player_rank DESC, power IS NULL, power DESC,
                    power_millions IS NULL, power_millions DESC, LOWER(player_name)
                    LIMIT 10000""")
                rows=cur.fetchall()
        for row in rows:
            if isinstance(row['power_millions'], Decimal):
                row['power_millions'] = float(row['power_millions'])
            row['is_new'] = bool(row['is_new'])
            row['possibly_removed'] = bool(row['possibly_removed'])
            row['has_avatar'] = bool(row['has_avatar'])
            row['birthday'] = row['birthday'].strftime('%m-%d') if row['birthday'] else None
            row['avatar_url'] = f"/api/player-list/avatar/{row['id']}" if row['has_avatar'] else None
        return {'players': rows, 'total': len(rows)}
    except Exception as exc:
        raise HTTPException(503, detail='Database unavailable. Check Render MySQL connection settings.') from exc


@app.get('/api/player-list/avatar/{player_id}')
def player_avatar(player_id:int):
    if player_id<1:raise HTTPException(404,detail='Portrait not found.')
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT avatar_image,avatar_mime FROM `player list` WHERE id=%s',(player_id,))
                row=cur.fetchone()
        if not row or not row['avatar_image']:
            raise HTTPException(404,detail='Portrait not found.')
        return Response(bytes(row['avatar_image']),media_type=row.get('avatar_mime') or 'image/webp',
                        headers={'Cache-Control':'public, max-age=3600','X-Content-Type-Options':'nosniff'})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,detail='Portrait temporarily unavailable.') from exc


class BirthdayUpdate(BaseModel):
    birthday: str | None = None

    @field_validator('birthday')
    @classmethod
    def month_day(cls, value):
        if not value:return None
        # Accept old clients as well, but discard their year.
        if not re.fullmatch(r'(?:\d{4}-)?\d{2}-\d{2}',value):
            raise ValueError('Use month and day (MM-DD).')
        value=value[-5:]
        try:
            parsed=datetime.strptime('2000-'+value,'%Y-%m-%d').date()
        except ValueError:
            raise ValueError('Use a valid month and day (MM-DD).')
        return parsed.strftime('%m-%d')


@app.patch('/api/player-list/{player_id}/birthday')
def update_birthday(player_id:int, body:BirthdayUpdate):
    # Manual birthday edits never affect rank, power, HQ or import statuses.
    if player_id<1:raise HTTPException(404,detail='Player not found.')
    try:
        with db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id FROM `player list` WHERE id=%s',(player_id,))
                if cur.fetchone() is None:
                    conn.rollback()
                    raise HTTPException(404,detail='Player not found.')
                cur.execute('UPDATE `player list` SET birthday=%s,updated_at=UTC_TIMESTAMP(6) WHERE id=%s',
                            ('2000-'+body.birthday if body.birthday else None,player_id))
            conn.commit()
        return {'id':player_id,'birthday':body.birthday}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503,detail='Could not save birthday.') from exc


@app.get('/api/player-list/export.csv')
def export_csv():
    from fastapi.responses import StreamingResponse
    data=list_players()['players']
    buf=io.StringIO()
    writer=csv.writer(buf)
    writer.writerow(['Rank','Player Name','Power','Kills','Donations','Power (M) from Profile','HQ Level','Birthday','Status'])
    for p in data:
        status='Possibly removed' if p['possibly_removed'] else 'New player' if p['is_new'] else 'Active'
        writer.writerow([p['rank'],p['name'],p.get('power') if p.get('power') is not None else '',p.get('kills') if p.get('kills') is not None else '',p.get('donations') if p.get('donations') is not None else '',p['power_millions'] if p['power_millions'] is not None else '',p['hq_level'] or '',p.get('birthday') or '',status])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="lastwar_player_list.csv"'})


def _update_job(job_id, **fields):
    with job_lock:
        job=jobs.get(job_id)
        if job:job.update(fields)


@app.get('/api/player-list/name-dictionary')
def player_name_dictionary():
    with db_connect() as conn:
        with conn.cursor() as cur:
            corrections = load_name_corrections(cur)
            cur.execute('SELECT player_name FROM `player list`')
            master_keys = {name_key(row['player_name']) for row in cur.fetchall()}
    return {key: corrected_name(key, corrections, master_keys)
            for key in corrections if key not in master_keys}


class DictionaryEntry(BaseModel):
    original: str = Field(min_length=1,max_length=255)
    corrected: str = Field(min_length=1,max_length=80)


@app.get('/api/admin/name-dictionary')
def admin_name_dictionary():
    with db_connect() as conn, conn.cursor() as cur:
        corrections=load_name_corrections(cur)
    return {'entries':[{'original':key,'corrected':corrections[key]} for key in sorted(corrections)]}


@app.put('/api/admin/name-dictionary')
def save_dictionary_entry(entry: DictionaryEntry):
    key=name_key(entry.original)
    target=entry.corrected.strip()
    if not key or len(key)>255 or not target or key==name_key(target):
        raise HTTPException(422,'Enter two different names.')
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT player_name FROM `player list`')
        roster={name_key(row['player_name']) for row in cur.fetchall()}
        if key in roster:
            raise HTTPException(409,'This OCR name already belongs to a player. Rename the player from their profile if needed.')
        corrections=load_name_corrections(cur)
        cursor_key=name_key(target)
        visited={key}
        while cursor_key in corrections and cursor_key not in roster:
            if cursor_key in visited:
                raise HTTPException(409,'That correction would create a dictionary loop.')
            visited.add(cursor_key)
            cursor_key=name_key(corrections[cursor_key])
        if cursor_key in visited:
            raise HTTPException(409,'That correction would create a dictionary loop.')
        learn_name_correction(cur,key,target)
        conn.commit()
    return {'original':key,'corrected':target}


@app.delete('/api/admin/name-dictionary')
def delete_dictionary_entry(original: str):
    key=name_key(original)
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute('DELETE FROM player_name_dictionary WHERE ocr_key=%s',(key,))
        conn.commit()
    return {'original':key}


def _run_scan(job_id, paths, tempdir):
    try:
        _update_job(job_id,status='scanning',message='Reading cached media on Render. Safe to lock your screen.',progress=2)
        scan_logger.info('Player List OCR job %s started for %d cached file(s).',job_id,len(paths))
        def progress(current,total,message):
            _update_job(job_id,progress=round(current/max(total,1)*100),message=message)
        result=scan_upload(paths,progress)
        corrections=player_name_dictionary()
        for player in result['players']:
            player['name']=corrections.get(name_key(player['name']),player['name'])
        scan_logger.info('Player List OCR job %s finished: %d candidates, %d frames.',job_id,len(result['players']),result['frames_examined'])
        _update_job(job_id,status='ready',message='Review extracted players before syncing.',progress=100,
                    players=result['players'],frames_examined=result['frames_examined'],warnings=result['warnings'])
    except Exception as exc:
        scan_logger.exception('Player List OCR job %s failed.',job_id)
        _update_job(job_id,status='error',message=str(exc)[:350],progress=1)
    finally:
        shutil.rmtree(tempdir,ignore_errors=True)
        scan_logger.info('Player List temporary upload cache deleted for job %s.',job_id)


@app.post('/api/player-list/scan',status_code=202)
async def upload_scan(files:list[UploadFile]=File(...)):
    if not files or len(files)>MAX_FILES:
        raise HTTPException(400,detail='Upload between 1 and 75 screenshots or one video.')
    if sum(1 for f in files if Path(f.filename or '').suffix.lower() in ('.mp4','.mov','.m4v','.webm'))>1:
        raise HTTPException(400,detail='Upload one video at a time. Screenshots may be uploaded as a batch.')
    with job_lock:
        busy=sum(1 for j in jobs.values() if j['status'] in ('queued','scanning'))
        if busy>=3:raise HTTPException(429,detail='The scanner is busy. Try again shortly.')
        # Evict old scan previews; never persist uploaded images or videos.
        for jid,j in list(jobs.items()):
            if time.time()-j['created_at']>3600:jobs.pop(jid,None)
    tempdir=Path(tempfile.mkdtemp(prefix='lastwar_player_upload_'))
    saved=[];size=0
    try:
        for index,upload in enumerate(files):
            ext=Path(upload.filename or '').suffix.lower()
            if ext not in ('.png','.jpg','.jpeg','.webp','.mp4','.mov','.m4v','.webm'):
                raise HTTPException(400,detail='Use PNG, JPG, WebP or MP4 video files.')
            dest=tempdir / f'upload_{index:03d}{ext}'
            with open(dest,'wb') as out:
                while chunk:=await upload.read(1024*1024):
                    size+=len(chunk)
                    if size>MAX_UPLOAD_BYTES:raise HTTPException(413,detail='Upload exceeds the 150 MB limit.')
                    out.write(chunk)
            saved.append(dest)
    except Exception:
        shutil.rmtree(tempdir,ignore_errors=True)
        raise
    job_id=str(uuid.uuid4())
    with job_lock:
        jobs[job_id]={'created_at':time.time(),'status':'queued','message':'Video cached on server; waiting for OCR worker…','progress':1,'players':[], 'warnings':[]}
    executor.submit(_run_scan,job_id,saved,tempdir)
    return {'job_id':job_id}


@app.get('/api/player-list/scan/{job_id}')
def scan_status(job_id:str):
    with job_lock:
        job=jobs.get(job_id)
        if not job:raise HTTPException(404,detail='Scan not found or expired.')
        return {k:v for k,v in job.items() if k!='created_at'}


def normalize_name(name):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',name).casefold())


def portraits_match(left: str | None, right: str | None) -> bool:
    # Never combine categories on username alone. Require matching rank and a
    # second independent signal, a perceptually matching avatar.
    if not left or not right:
        return False
    if left == right:
        return True
    try:
        import cv2
        import numpy as np
        a = cv2.imdecode(np.frombuffer(base64.b64decode(left,validate=True),np.uint8),cv2.IMREAD_GRAYSCALE)
        b = cv2.imdecode(np.frombuffer(base64.b64decode(right,validate=True),np.uint8),cv2.IMREAD_GRAYSCALE)
        if a is None or b is None:
            return False
        aa = cv2.resize(a,(24,24),interpolation=cv2.INTER_AREA)
        bb = cv2.resize(b,(24,24),interpolation=cv2.INTER_AREA)
        return float(np.mean(cv2.absdiff(aa,bb))) < 12.0
    except Exception:
        return False


class Member(BaseModel):
    name: str = Field(min_length=2,max_length=80)
    rank: str = Field(pattern=r'^R[1-5]$')
    power_millions: float | None = Field(default=None,ge=0,le=1000000)
    hq_level: int | None = Field(default=None,ge=1,le=99)
    avatar_base64: str | None = Field(default=None,max_length=400000)
    power: int | None = Field(default=None,ge=0,le=18446744073709551615)
    kills: int | None = Field(default=None,ge=0,le=18446744073709551615)
    donations: int | None = Field(default=None,ge=0,le=18446744073709551615)
    upload_date: date | None = None

    @field_validator('name')
    @classmethod
    def safe_name(cls,v:str):
        v=re.sub(r'\s+',' ',v.strip())
        if not v or not any(ch.isalpha() for ch in v):raise ValueError('A player name must contain letters.')
        return v


class PlayerProfileUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    rank: str = Field(pattern=r'^R[1-5]$')
    expected_name: str
    expected_rank: str

    @field_validator('name')
    @classmethod
    def validate_name(cls, value):
        return Member.safe_name(value)


@app.patch('/api/player-list/{player_id}/profile')
def update_player_profile(player_id: int, body: PlayerProfileUpdate):
    try:
        with db_connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute('SELECT id,player_name,player_rank FROM `player list` FOR UPDATE')
                    players = cur.fetchall()
                    player = next((p for p in players if p['id'] == player_id), None)
                    if not player:
                        raise HTTPException(404, detail='Player not found.')
                    if (player['player_name'], player['player_rank']) != (body.expected_name, body.expected_rank):
                        raise HTTPException(409, detail='This player changed. Close and reopen the profile before editing.')
                    if match_master({'name': body.name}, [p for p in players if p['id'] != player_id]):
                        raise HTTPException(409, detail='That name already belongs to another player.')
                    key = normalize_name(body.name)
                    cur.execute('SELECT id FROM vs_points WHERE normalized_name=%s AND (player_id IS NULL OR player_id<>%s)', (key, player_id))
                    if cur.fetchone() and key != normalize_name(player['player_name']):
                        raise HTTPException(409, detail='That name has separate VS records. Resolve those records before renaming.')
                    cur.execute('UPDATE `player list` SET player_name=%s,normalized_name=%s,player_rank=%s,updated_at=UTC_TIMESTAMP(6) WHERE id=%s',
                                (body.name, key, body.rank, player_id))
                    cur.execute('UPDATE vs_points SET player_name=%s,normalized_name=%s WHERE player_id=%s', (body.name, key, player_id))
                    if body.name != player['player_name']:
                        cur.execute('UPDATE player_name_dictionary SET player_name=%s WHERE player_name=%s', (body.name, player['player_name']))
                        learn_name_correction(cur, player['player_name'], body.name)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {'id': player_id, 'name': body.name, 'rank': body.rank}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail='Could not save player profile.') from exc


class ImportData(BaseModel):
    players: list[Member] = Field(min_length=1,max_length=2000)
    full_capture: bool = False


@app.post('/api/player-list/import')
def import_players(body: ImportData):
    return merge_players(body)


def merge_players(body: ImportData, connection=None):
    batch_id = str(uuid.uuid4())
    # Canonicalize against the shared master directory before combining categories.
    with (nullcontext(connection) if connection is not None else db_connect()) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id,player_name,player_rank,normalized_name FROM `player list`")
            master=cur.fetchall()
    unique = {}
    observed_dates = {}
    stats = ('power','power_millions','kills','donations')
    for member in body.players:
        current=match_master({'name':member.name},master)
        if current:
            member=member.model_copy(update={'name':current['player_name']})
        key=normalize_name(member.name)
        dates = observed_dates.setdefault(key, {})
        for field in stats:
            if getattr(member,field) is not None:
                dates[field] = member.upload_date
        if key in unique:
            other=unique[key]
            # Power and Kills observations can have different portraits/rank OCR.
            # Same canonical player, independent fields; retain every known stat.
            unique[key]=other.model_copy(update={
                field:getattr(member,field) if getattr(member,field) is not None else getattr(other,field)
                for field in ('power','kills','donations','power_millions','hq_level','avatar_base64')
            })
        else:
            unique[key]=member
    if not unique:
        raise HTTPException(400, detail='No valid players were provided.')
    created = updated = removed = 0
    try:
        with (nullcontext(connection) if connection is not None else db_connect()) as conn:
            try:
                # One transaction, including new/removed status changes.
                with conn.cursor() as cur:
                    cur.execute('UPDATE `player list` SET is_new=FALSE WHERE is_new=TRUE')
                    for key, member in unique.items():
                        portrait=None
                        if member.avatar_base64:
                            try:
                                portrait=base64.b64decode(member.avatar_base64,validate=True)
                            except (ValueError,binascii.Error) as exc:
                                raise HTTPException(400,detail='Invalid portrait image.') from exc
                            if len(portrait)>256000 or len(portrait)<50 or not (
                                (portrait.startswith(b'RIFF') and portrait[8:12]==b'WEBP') or
                                portrait.startswith(b'\x89PNG\r\n\x1a\n')
                            ):
                                raise HTTPException(400,detail='Invalid PNG or WebP portrait image.')
                        avatar_mime = ('image/png' if portrait and portrait.startswith(b'\x89PNG') else 'image/webp')
                        cur.execute('SELECT id,power,power_millions,kills,donations FROM `player list` WHERE normalized_name=%s FOR UPDATE', (key,))
                        existing = cur.fetchone()
                        if existing:
                            cur.execute("""UPDATE `player list` SET
                                player_name=%s, player_rank=%s,
                                power_millions=COALESCE(%s,power_millions),
                                power=COALESCE(%s,power),
                                kills=COALESCE(%s,kills),
                                donations=COALESCE(%s,donations),
                                hq_level=COALESCE(%s,hq_level),
                                avatar_image=COALESCE(%s,avatar_image),
                                avatar_mime=IF(%s IS NOT NULL,%s,avatar_mime),
                                avatar_updated_at=IF(%s IS NOT NULL,UTC_TIMESTAMP(6),avatar_updated_at),
                                is_new=FALSE, possibly_removed=FALSE,
                                last_seen_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6),last_import_id=%s
                                WHERE id=%s""",
                                (member.name, member.rank, member.power_millions,
                                 member.power,member.kills,member.donations,
                                 member.hq_level,portrait,portrait,avatar_mime,
                                 portrait,batch_id,existing['id']))
                            updated += 1
                        else:
                            cur.execute("""INSERT INTO `player list` (
                                player_name,normalized_name,player_rank,power_millions,power,kills,donations,hq_level,
                                avatar_image,avatar_mime,avatar_updated_at,is_new,possibly_removed,last_import_id)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,IF(%s IS NOT NULL,UTC_TIMESTAMP(6),NULL),TRUE,FALSE,%s)""",
                                (member.name,key,member.rank,member.power_millions,member.power,member.kills,member.donations,
                                 member.hq_level,portrait,avatar_mime,portrait,batch_id))
                            created += 1
                        dates = observed_dates[key]
                        changed = any(
                            existing is None or (Decimal(str(getattr(member,field))) if field == 'power_millions'
                                                 else getattr(member,field)) != existing[field]
                            for field in dates)
                        if changed or any(dates.values()):
                            samples = [dates[field] or date.today() if field in dates else None for field in stats]
                            cur.execute("""INSERT INTO player_stat_history
                                (player_id,import_id,recorded_at,power,power_millions,kills,donations,
                                 power_upload_date,power_millions_upload_date,kills_upload_date,donations_upload_date)
                                SELECT id,%s,UTC_TIMESTAMP(6),power,power_millions,kills,donations,%s,%s,%s,%s
                                FROM `player list` WHERE normalized_name=%s""",(batch_id,*samples,key))
                    if body.full_capture:
                        cur.execute("""UPDATE `player list` SET possibly_removed=TRUE,
                            updated_at=UTC_TIMESTAMP(6) WHERE (last_import_id IS NULL OR last_import_id<>%s)
                            AND possibly_removed=FALSE""", (batch_id,))
                        cur.execute('SELECT COUNT(*) AS n FROM `player list` WHERE possibly_removed=TRUE')
                        removed = int(cur.fetchone()['n'])
                    cur.execute("""INSERT INTO player_list_imports (
                        id,is_full_capture,scanned_count,created_count,updated_count,possible_removed_count)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                        (batch_id,body.full_capture,len(unique),created,updated,removed))
                if connection is None:conn.commit()
            except Exception:
                if connection is None:conn.rollback()
                raise
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, detail='Could not sync Player List. Check the MySQL connection and Render logs.') from exc
    return {'import_id':batch_id,'scanned_count':len(unique),'new_count':created,
            'updated_count':updated,'possibly_removed_count':removed,'full_capture':body.full_capture}

@app.get('/api/player-list/{player_id}/history')
def player_history(player_id: int):
    with db_connect() as conn, conn.cursor() as cur:
        cur.execute('SELECT normalized_name FROM `player list` WHERE id=%s',(player_id,))
        player = cur.fetchone()
        if not player:
            raise HTTPException(404,detail='Player not found.')
        cur.execute("""SELECT DATE(recorded_at) AS recorded_date,power,power_millions,kills,donations,
                       power_upload_date,power_millions_upload_date,kills_upload_date,donations_upload_date
                       FROM player_stat_history WHERE player_id=%s ORDER BY recorded_at,import_id""",(player_id,))
        history = cur.fetchall()
        cur.execute("""SELECT score_date,points FROM vs_points
                       WHERE player_id=%s OR (player_id IS NULL AND normalized_name=%s)
                       ORDER BY score_date,player_id IS NULL,updated_at DESC""",
                    (player_id,player['normalized_name']))
        scores = cur.fetchall()

    series = {key:{} for key in ('power','kills','donations','vs')}
    fields = ('power','power_millions','kills','donations')
    previous = {}
    for row in history:
        legacy = all(row[f'{field}_upload_date'] is None for field in fields)
        for field in fields:
            value = row[field]
            sample_date = row[f'{field}_upload_date']
            if legacy and value is not None and previous.get(field) != value:
                sample_date = row['recorded_date']
            if value is not None:
                previous[field] = value
            if value is None or sample_date is None:
                continue
            key = 'power' if field == 'power_millions' else field
            if field == 'power_millions':
                # Exact power wins when both formats were recorded on the same date.
                series[key].setdefault(str(sample_date),int(value * 1000000))
            else:
                series[key][str(sample_date)] = int(value)
    seen = set()
    for row in scores:
        day = row['score_date']
        if day in seen or day.weekday() == 6:
            continue
        seen.add(day)
        sunday = (day + timedelta(days=6-day.weekday())).isoformat()
        series['vs'][sunday] = series['vs'].get(sunday,0) + int(row['points'])
    return {key:[{'date':day,'value':value} for day,value in sorted(values.items())]
            for key,values in series.items()}


@app.get('/media_ui.js')
def shared_media_script():
    return FileResponse(BASE / 'media_ui.js', media_type='application/javascript',
                        headers={'Cache-Control': 'no-cache'})

@app.get('/google_drive_picker.js')
def google_drive_picker_script():
    return FileResponse(BASE / 'google_drive_picker.js', media_type='application/javascript',
                        headers={'Cache-Control':'no-cache'})

# Register the common Media Upload API after the Player List models and import
# transaction are defined, avoiding circular startup dependencies.
from vs_points import install_vs_points
install_vs_points(app, db_connect)

from media_upload import install_media_upload
install_media_upload(app, db_connect, merge_players, ImportData)

@app.get('/vs_ui.js')
def vs_ui_script():
    return FileResponse(BASE / 'vs_ui.js', media_type='application/javascript', headers={'Cache-Control':'no-cache'})

@app.get('/review_validation.js')
def review_validation_script():
    return FileResponse(BASE / 'review_validation.js', media_type='application/javascript', headers={'Cache-Control':'no-cache'})

@app.get('/player_profile.js')
def player_profile_script():
    return FileResponse(BASE / 'player_profile.js', media_type='application/javascript', headers={'Cache-Control':'no-cache'})


install_admin_reset(app, db_connect, jobs, job_lock)

@app.get('/admin_ui.js')
def admin_ui_script():
    return FileResponse(BASE / 'admin_ui.js', media_type='application/javascript', headers={'Cache-Control':'no-cache'})

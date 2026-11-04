"""Backend for the Player List tab.

Only the Player List API accesses PostgreSQL. Roster Scanner remains client-side.
Database passwords and upload keys are read from Render environment variables,
never returned to the browser or written to the frontend source.
"""
from __future__ import annotations

import csv
import io
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from player_list_ocr import scan_upload

BASE = Path(__file__).parent
app = FastAPI(title='Alliance Tools Player List API', docs_url=None, redoc_url=None)
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='member-list-ocr')
jobs: dict[str, dict[str, Any]] = {}
job_lock = threading.RLock()
MAX_UPLOAD_BYTES = 150 * 1024 * 1024
MAX_FILES = 75
DATABASE_OK = False
DATABASE_ERROR = ''


def _env(*names: str) -> str | None:
    # Environment keys vary between Render database providers.
    mapping = {k.upper(): v for k, v in os.environ.items() if v}
    return next((mapping.get(n.upper()) for n in names if mapping.get(n.upper())), None)


def _db_config():
    url = _env('DATABASE_URL', 'POSTGRES_URL', 'DATABASE_URI', 'POSTGRESQL_URL', 'DB_URL', 'POSTGRES_CONNECTION_STRING', 'DB_CONNECTION_STRING')
    if url:
        return {'conninfo': url}
    host = _env('DB_HOST', 'DBHOST', 'DB_HOSTNAME', 'POSTGRES_HOST', 'POSTGRESQL_HOST', 'PGHOST', 'DATABASE_HOST', 'HOST')
    user = _env('DB_USERNAME', 'DB_USER', 'DBUSER', 'POSTGRES_USER', 'POSTGRESQL_USER', 'PGUSER', 'DATABASE_USER', 'USERNAME', 'USER')
    password = _env('DB_PASSWORD', 'DBPASSWORD', 'POSTGRES_PASSWORD', 'POSTGRESQL_PASSWORD', 'PGPASSWORD', 'DATABASE_PASSWORD', 'PASSWORD')
    db_name = _env('DB_NAME', 'POSTGRES_DB', 'POSTGRES_DATABASE', 'DATABASE_NAME', 'DB_DATABASE', 'DB_DATABASE_NAME', 'PGDATABASE', 'DATABASE', 'DBNAME')
    port = _env('DB_PORT', 'DBPORT', 'POSTGRES_PORT', 'POSTGRESQL_PORT', 'DATABASE_PORT', 'PGPORT')
    if not port:
        # Render reserves PORT for the *webserver*. Only accept a conventional
        # PostgreSQL number as a fallback if the user named their variable PORT.
        candidate = _env('PORT')
        port = candidate if candidate in ('5432', '5433', '6543') else '5432'
    if not all((host, user, password, db_name)):
        # Keys only, never values. These diagnostics help match custom Render
        # variable naming conventions without revealing secrets.
        found=[name for name in os.environ if any(term in name.lower() for term in ('db','database','postgres','pg','host','user','password','port','name','conn'))]
        print('Player List database environment keys:', sorted(found), flush=True)
        print('Player List missing connection components:', [name for name,present in [('host',host),('user',user),('password',password),('database name',db_name)] if not present], flush=True)
        raise RuntimeError('PostgreSQL connection settings are incomplete. Configure DATABASE_URL or DB_HOST, DB_PORT, DB_USER, DB_PASSWORD and DB_NAME in Render.')
    return {'host': host, 'port': int(port), 'user': user, 'password': password,
            'dbname': db_name, 'sslmode': _env('DB_SSLMODE', 'PGSSLMODE') or 'prefer'}


def db_connect():
    params = _db_config()
    conn = psycopg.connect(**params, connect_timeout=8, row_factory=dict_row)
    return conn


def initialize_db():
    global DATABASE_OK, DATABASE_ERROR
    try:
        with db_connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS "player list" (
                id BIGSERIAL PRIMARY KEY,
                player_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                player_rank TEXT NOT NULL CHECK (player_rank IN ('R1','R2','R3','R4','R5')),
                power_millions NUMERIC(10,1),
                hq_level INTEGER CHECK (hq_level BETWEEN 1 AND 99),
                is_new BOOLEAN NOT NULL DEFAULT TRUE,
                possibly_removed BOOLEAN NOT NULL DEFAULT FALSE,
                first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_import_id UUID
            )''')
            conn.execute('''CREATE TABLE IF NOT EXISTS player_list_imports (
                id UUID PRIMARY KEY,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                is_full_capture BOOLEAN NOT NULL,
                scanned_count INTEGER NOT NULL,
                created_count INTEGER NOT NULL,
                updated_count INTEGER NOT NULL,
                possible_removed_count INTEGER NOT NULL
            )''')
        DATABASE_OK, DATABASE_ERROR = True, ''
        print('Player List database initialized', flush=True)
    except Exception as exc:
        DATABASE_OK, DATABASE_ERROR = False, type(exc).__name__
        print('Player List database initialization unavailable:', type(exc).__name__, flush=True)


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
            conn.execute('SELECT 1')
        return {'online': True, 'database': 'connected', 'upload_protected': bool(_env('PLAYER_LIST_UPLOAD_KEY'))}
    except Exception:
        return JSONResponse(status_code=503, content={'online': True, 'database': 'disconnected', 'upload_protected': bool(_env('PLAYER_LIST_UPLOAD_KEY')), 'hint': 'Check database environment variables and connection in Render.'})


@app.get('/api/player-list')
def list_players():
    try:
        with db_connect() as conn:
            rows=conn.execute('''SELECT id,player_name AS name,player_rank AS rank,
                power_millions::float8 AS power_millions,hq_level,
                is_new,possibly_removed,first_seen_at,last_seen_at,updated_at
                FROM "player list"
                ORDER BY player_rank DESC,power_millions DESC NULLS LAST,lower(player_name)
                LIMIT 10000''').fetchall()
        return {'players': rows, 'total': len(rows)}
    except Exception as exc:
        raise HTTPException(503, detail='Database unavailable. Check your Render database connection settings.') from exc


@app.get('/api/player-list/export.csv')
def export_csv():
    from fastapi.responses import StreamingResponse
    data=list_players()['players']
    buf=io.StringIO()
    writer=csv.writer(buf)
    writer.writerow(['Rank','Player Name','Power (M)','HQ Level','Status','Last Seen'])
    for p in data:
        status='Possibly removed' if p['possibly_removed'] else 'New player' if p['is_new'] else 'Active'
        writer.writerow([p['rank'],p['name'],p['power_millions'] if p['power_millions'] is not None else '',p['hq_level'] or '',status,p['last_seen_at'].isoformat()])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="lastwar_player_list.csv"'})


def _authorize(key: str | None):
    from secrets import compare_digest
    required=_env('PLAYER_LIST_UPLOAD_KEY')
    if required and not (key and compare_digest(key,required)):
        raise HTTPException(401,detail='Incorrect Player List upload key.')


def _update_job(job_id, **fields):
    with job_lock:
        job=jobs.get(job_id)
        if job:job.update(fields)


def _run_scan(job_id, paths, tempdir):
    try:
        _update_job(job_id,status='scanning',message='Preparing OCR',progress=0)
        def progress(current,total,message):
            _update_job(job_id,progress=round(current/max(total,1)*100),message=message)
        result=scan_upload(paths,progress)
        _update_job(job_id,status='ready',message='Review extracted players before syncing.',progress=100,
                    players=result['players'],frames_examined=result['frames_examined'],warnings=result['warnings'])
    except Exception as exc:
        _update_job(job_id,status='error',message=str(exc)[:350],progress=0)
    finally:
        shutil.rmtree(tempdir,ignore_errors=True)


@app.post('/api/player-list/scan',status_code=202)
async def upload_scan(files:list[UploadFile]=File(...),x_player_list_key:str|None=Header(default=None)):
    _authorize(x_player_list_key)
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
        jobs[job_id]={'created_at':time.time(),'status':'queued','message':'Queued for scanning','progress':0,'players':[], 'warnings':[]}
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


class Member(BaseModel):
    name: str = Field(min_length=2,max_length=80)
    rank: str = Field(pattern=r'^R[1-5]$')
    power_millions: float | None = Field(default=None,ge=0,le=1000000)
    hq_level: int | None = Field(default=None,ge=1,le=99)

    @field_validator('name')
    @classmethod
    def safe_name(cls,v:str):
        v=re.sub(r'\s+',' ',v.strip())
        if not v or not any(ch.isalpha() for ch in v):raise ValueError('A player name must contain letters.')
        return v


class ImportData(BaseModel):
    players: list[Member] = Field(min_length=1,max_length=2000)
    full_capture: bool = False


@app.post('/api/player-list/import')
def import_players(body:ImportData,x_player_list_key:str|None=Header(default=None)):
    _authorize(x_player_list_key)
    batch_id=uuid.uuid4()
    # A capture must have unique names; dedupe here defensively.
    unique={normalize_name(member.name):member for member in body.players}
    if len(unique)<1:raise HTTPException(400,detail='No valid players were provided.')
    created=updated=removed=0
    try:
        with db_connect() as conn:
            # The transaction prevents half-imports and guarantees consistent statuses.
            with conn.transaction():
                conn.execute('UPDATE "player list" SET is_new=FALSE WHERE is_new=TRUE')
                for key,member in unique.items():
                    existing=conn.execute('SELECT id FROM "player list" WHERE normalized_name=%s FOR UPDATE',(key,)).fetchone()
                    if existing:
                        conn.execute('''UPDATE "player list" SET
                            player_name=%s,player_rank=%s,
                            power_millions=COALESCE(%s,power_millions),
                            hq_level=COALESCE(%s,hq_level),
                            is_new=FALSE,possibly_removed=FALSE,
                            last_seen_at=now(),updated_at=now(),last_import_id=%s
                            WHERE id=%s''',
                            (member.name,member.rank,member.power_millions,member.hq_level,batch_id,existing['id']))
                        updated+=1
                    else:
                        conn.execute('''INSERT INTO "player list" (
                            player_name,normalized_name,player_rank,power_millions,hq_level,
                            is_new,possibly_removed,last_import_id)
                            VALUES (%s,%s,%s,%s,%s,TRUE,FALSE,%s)''',
                            (member.name,key,member.rank,member.power_millions,member.hq_level,batch_id))
                        created+=1
                if body.full_capture:
                    conn.execute('''UPDATE "player list" SET possibly_removed=TRUE,updated_at=now()
                        WHERE last_import_id IS DISTINCT FROM %s AND possibly_removed=FALSE''',(batch_id,))
                    removed=conn.execute('''SELECT count(*) AS n FROM "player list"
                        WHERE possibly_removed=TRUE''').fetchone()['n']
                conn.execute('''INSERT INTO player_list_imports (
                    id,is_full_capture,scanned_count,created_count,updated_count,possible_removed_count)
                    VALUES (%s,%s,%s,%s,%s,%s)''',
                    (batch_id,body.full_capture,len(unique),created,updated,removed))
    except Exception as exc:
        # Avoid leaking connection URLs or passwords through API error messages.
        raise HTTPException(503,detail='Could not sync Player List. Check the database connection and Render logs.') from exc
    return {'import_id':str(batch_id),'scanned_count':len(unique),'new_count':created,
            'updated_count':updated,'possibly_removed_count':removed,'full_capture':body.full_capture}

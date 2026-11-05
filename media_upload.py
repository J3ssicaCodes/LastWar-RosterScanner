"""Shared asynchronous Media Upload service for Alliance Tools.

Only temporary uploads are stored on the Render filesystem. Scan metadata,
Storm Roster snapshots, and Player List review rows live in MySQL.
No browser credential or third party media service is required.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from player_list_ocr import scan_upload, extract_profile
from tested_rankings_adapter import scan_tested_rankings_upload
from storm_roster_ocr import scan_storm_upload
import cv2

MAX_MEDIA_BYTES = 150 * 1024 * 1024
MAX_MEDIA_FILES = 75
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.mov', '.m4v', '.webm'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm'}
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='alliance-media-ocr')
job_lock = threading.Lock()


class ReviewEntry(BaseModel):
    id: int = Field(ge=1)
    name: str = Field(max_length=80)
    rank: str = Field(pattern=r'^(R[1-5]|Unknown)$')
    power_millions: float | None = Field(default=None, ge=0, le=1000000)
    hq_level: int | None = Field(default=None, ge=1, le=99)
    power: int | None = Field(default=None, ge=0, le=18446744073709551615)
    kills: int | None = Field(default=None, ge=0, le=18446744073709551615)
    donations: int | None = Field(default=None, ge=0, le=18446744073709551615)
    include: bool = True


class ApprovalRequest(BaseModel):
    rows: list[ReviewEntry] = Field(min_length=1, max_length=2000)
    # Accepted for older clients, but never used to flag absent players.
    full_capture: bool = False


class DismissRows(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)


def install_media_upload(app, connect_db, import_players, import_model):
    router = APIRouter(prefix='/api/media', tags=['Media Upload'])

    def schema():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS `media_scan_jobs` (
                    id CHAR(36) NOT NULL PRIMARY KEY,
                    destination VARCHAR(24) NOT NULL,
                    status VARCHAR(24) NOT NULL DEFAULT 'queued',
                    progress TINYINT UNSIGNED NOT NULL DEFAULT 0,
                    message VARCHAR(350) NOT NULL DEFAULT '',
                    result_count INT UNSIGNED NOT NULL DEFAULT 0,
                    full_capture BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    KEY idx_media_jobs_created (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                cur.execute('''CREATE TABLE IF NOT EXISTS `storm roster` (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    source_job_id CHAR(36) NOT NULL,
                    team CHAR(1) NOT NULL,
                    player_rank VARCHAR(2) NOT NULL,
                    player_name VARCHAR(255) NOT NULL,
                    normalized_name VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    player_power VARCHAR(24) NULL,
                    player_role VARCHAR(32) NOT NULL,
                    scanned_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    UNIQUE KEY uq_storm_team_name (team, normalized_name),
                    KEY idx_storm_team_rank (team, player_rank)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                cur.execute('''CREATE TABLE IF NOT EXISTS `roster upload` (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    job_id CHAR(36) NOT NULL,
                    player_name VARCHAR(255) NOT NULL,
                    player_rank VARCHAR(8) NOT NULL,
                    leaderboard_tab VARCHAR(12) NULL,
                    leaderboard_position SMALLINT UNSIGNED NULL,
                    power BIGINT UNSIGNED NULL,
                    kills BIGINT UNSIGNED NULL,
                    donations BIGINT UNSIGNED NULL,
                    review_required BOOLEAN NOT NULL DEFAULT FALSE,
                    review_reasons TEXT NULL,
                    source_image VARCHAR(255) NULL,
                    audit_json LONGTEXT NULL,
                    power_millions DECIMAL(12,2) NULL,
                    hq_level SMALLINT UNSIGNED NULL,
                    avatar_base64 MEDIUMTEXT NULL,
                    evidence_base64 MEDIUMTEXT NULL,
                    change_kind VARCHAR(16) NOT NULL DEFAULT 'review',
                    source_type VARCHAR(32) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    KEY idx_roster_upload_job (job_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                cur.execute('''CREATE TABLE IF NOT EXISTS media_scan_audit (
                    job_id CHAR(36) NOT NULL PRIMARY KEY,
                    diagnostics_json LONGTEXT NULL,
                    audit_json LONGTEXT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                # Existing staging databases require non-destructive migrations.
                for field,definition in (
                    ('leaderboard_tab','VARCHAR(12) NULL'),
                    ('leaderboard_position','SMALLINT UNSIGNED NULL'),
                    ('power','BIGINT UNSIGNED NULL'),
                    ('kills','BIGINT UNSIGNED NULL'),
                    ('donations','BIGINT UNSIGNED NULL'),
                    ('review_required','BOOLEAN NOT NULL DEFAULT FALSE'),
                    ('review_reasons','TEXT NULL'),
                    ('source_image','VARCHAR(255) NULL'),
                    ('evidence_base64','MEDIUMTEXT NULL'),
                    ('change_kind',"VARCHAR(16) NOT NULL DEFAULT 'review'"),
                    ('audit_json','LONGTEXT NULL'),
                ):
                    cur.execute('SHOW COLUMNS FROM `roster upload` LIKE %s',(field,))
                    if not cur.fetchone():
                        cur.execute(f'ALTER TABLE `roster upload` ADD COLUMN `{field}` {definition}')
                cur.execute('SHOW COLUMNS FROM `roster upload` LIKE %s',('player_rank',))
                rank_column=cur.fetchone()
                if rank_column and rank_column.get('Type','').lower()=='varchar(2)':
                    cur.execute('ALTER TABLE `roster upload` MODIFY COLUMN player_rank VARCHAR(8) NOT NULL')
                # A service restart should never leave a scan appearing to run forever.
                cur.execute("""UPDATE media_scan_jobs
                               SET status='interrupted', message='Server restarted during OCR. Please upload again.',
                                   updated_at=UTC_TIMESTAMP(6)
                               WHERE status IN ('queued','scanning')""")
            conn.commit()
        print('Shared Media Upload MySQL tables initialized', flush=True)

    @app.on_event('startup')
    def startup_media():
        try:
            schema()
        except Exception as exc:
            print('Media Upload schema unavailable:', type(exc).__name__, flush=True)

    def set_job(job_id: str, *, status: str | None = None, progress: int | None = None,
                message: str | None = None, count: int | None = None):
        fields = []
        values: list[Any] = []
        if status is not None: fields.append('status=%s'); values.append(status)
        if progress is not None: fields.append('progress=%s'); values.append(max(0, min(100, progress)))
        if message is not None: fields.append('message=%s'); values.append(message[:350])
        if count is not None: fields.append('result_count=%s'); values.append(count)
        fields.append('updated_at=UTC_TIMESTAMP(6)')
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE media_scan_jobs SET ' + ','.join(fields) + ' WHERE id=%s', (*values, job_id))
            conn.commit()

    def run_job(job_id: str, destination: str, paths: list[Path], tempdir: Path):
        try:
            set_job(job_id, status='scanning', progress=1, message='Preparing media on the server…')
            def report(current, total, message):
                # Avoid issuing a database write for every OCR pixel or video frame.
                pct = round(100 * current / max(total, 1))
                if not hasattr(report, 'last') or pct != report.last or time.monotonic()-report.last_at>4:
                    set_job(job_id, progress=pct, message=message)
                    report.last = pct
                    report.last_at = time.monotonic()
            report.last = None
            report.last_at = 0.0
            if destination == 'storm':
                result = scan_storm_upload(paths, report)
            else:
                # Player profiles have a different layout. The leaderboard branch
                # still uses exactly one shared page pipeline for MP4 and images.
                profiles, leaderboard_files = [], []
                for path in paths:
                    if path.suffix.lower() in VIDEO_EXTENSIONS:
                        leaderboard_files.append(path)
                        continue
                    frame = cv2.imread(str(path))
                    profile = extract_profile(frame) if frame is not None else None
                    if profile is not None:
                        profiles.append(path)
                    else:
                        leaderboard_files.append(path)
                if leaderboard_files:
                    result = scan_tested_rankings_upload(leaderboard_files, report)
                else:
                    result = {'players': [], 'audit': [], 'diagnostics': {},
                              'frames_examined':0,'warnings':[]}
                if profiles:
                    old = scan_upload(profiles,report)
                    result['players'].extend(old['players'])
                    result['frames_examined'] += old.get('frames_examined',0)
                    result['warnings'].extend(old.get('warnings',[]))
            records = result.get('players', [])
            if not records:
                raise ValueError('No readable players were found. The previous list was kept unchanged.')
            if destination == 'storm':
                # Last completed Storm upload becomes the new event snapshot.
                from app import normalize_name
                dedup = {}
                for row in records:
                    name = str(row.get('name') or '').strip()
                    team = str(row.get('team') or '?').upper()
                    if not name or team not in ('A','B'): continue
                    key = (team, normalize_name(name))
                    if key[1] and (key not in dedup or not dedup[key].get('power')):
                        dedup[key] = row
                if not dedup:
                    raise ValueError('Team A or B was not readable. Previous Storm Roster remains unchanged.')
                with connect_db() as conn:
                    try:
                        with conn.cursor() as cur:
                            cur.execute('DELETE FROM `storm roster`')
                            for (team, norm), row in dedup.items():
                                cur.execute('''INSERT INTO `storm roster`
                                    (source_job_id,team,player_rank,player_name,normalized_name,player_power,player_role)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                                    (job_id,team,row.get('rank') or 'R3',str(row['name'])[:255],norm,
                                     str(row.get('power') or '')[:24],row.get('role') or 'Needs review'))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                set_job(job_id,status='ready',progress=100,count=len(dedup),
                        message=f'Storm Roster ready: {len(dedup)} players. Scanner updated.')
            else:
                with connect_db() as conn:
                    try:
                        with conn.cursor() as cur:
                            from app import normalize_name
                            cur.execute("""SELECT normalized_name,player_name,player_rank,
                                power_millions,power,kills,donations,hq_level,
                                avatar_image IS NOT NULL AS has_avatar
                                FROM `player list`""")
                            existing_players = {p['normalized_name']: p for p in cur.fetchall()}
                            unchanged = 0
                            for row in records:
                                name=str(row.get('name') or '').strip()
                                rank=str(row.get('rank') or 'Unknown')
                                if rank not in ('R1','R2','R3','R4','R5'):
                                    rank='Unknown'
                                # Even unrecognized complete rows require review; never silently hide them.
                                reasons=row.get('review_reasons') or []
                                review=bool(row.get('review_required') or not name or rank=='Unknown')
                                audit_fields=row.get('audit') or {}
                                current=existing_players.get(normalize_name(name)) if name else None
                                if current is None:
                                    change_kind='review' if review else 'new'
                                else:
                                    differences=(
                                        (rank!='Unknown' and rank!=current['player_rank']) or
                                        (name!=current['player_name']) or
                                        any(
                                            row.get(field) is not None and
                                            (
                                                current[field] is None or
                                                (abs(float(row[field])-float(current[field])) > 0.049
                                                 if field=='power_millions'
                                                 else int(row[field])!=int(current[field]))
                                            )
                                            for field in ('power_millions','power','kills','donations','hq_level')
                                        ) or
                                        (bool(row.get('avatar_base64')) and not current['has_avatar'])
                                    )
                                    if not differences and not review:
                                        unchanged += 1
                                        continue
                                    change_kind='review' if review else 'updated'
                                cur.execute("""INSERT INTO `roster upload`
                                    (job_id,player_name,player_rank,power_millions,hq_level,avatar_base64,
                                     source_type,leaderboard_tab,leaderboard_position,power,kills,donations,
                                     review_required,review_reasons,source_image,audit_json,evidence_base64,change_kind)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (job_id,name[:255],rank,row.get('power_millions'),row.get('hq_level'),
                                     row.get('avatar_base64'),row.get('source_type','leaderboard'),
                                     row.get('tab'),row.get('internal_position'),row.get('power'),
                                     row.get('kills'),row.get('donations'),review,
                                     json.dumps(reasons,ensure_ascii=False)[:8000],
                                     str(row.get('source_image') or '')[:255],
                                     json.dumps(audit_fields,ensure_ascii=False)[:60000],
                                     str(row.get('evidence_base64') or '')[:320000] or None,change_kind))
                            cur.execute('SELECT COUNT(*) AS n FROM `roster upload` WHERE job_id=%s',(job_id,))
                            inserted=int(cur.fetchone()['n'])
                            details=result.get('diagnostics') or {
                                'input_files':len(paths),
                                'distinct_entries':inserted,
                                'tabs':[],
                                'missing_positions':{},
                                'review_count':0}
                            cur.execute("""INSERT INTO media_scan_audit
                                (job_id,diagnostics_json,audit_json) VALUES (%s,%s,%s)
                                ON DUPLICATE KEY UPDATE diagnostics_json=VALUES(diagnostics_json),
                                    audit_json=VALUES(audit_json)""",
                                (job_id,json.dumps(details,ensure_ascii=False)[:250000],
                                 json.dumps(result.get('audit',[]),ensure_ascii=False)[:1000000]))
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                if inserted:
                    set_job(job_id,status='review',progress=100,count=inserted,
                            message=f'{inserted} new or changed Player List entries waiting for approval.')
                else:
                    set_job(job_id,status='ready',progress=100,count=0,
                            message='Scan finished. No changes detected; player list remains as it was.')
        except Exception as exc:
            # Exception details are shown in the UI only if they contain no secrets.
            print('Media OCR scan failed:',type(exc).__name__, flush=True)
            try: set_job(job_id,status='error',progress=0,message=str(exc)[:240])
            except Exception: pass
        finally:
            # Keep upload media in a temporary location only until processing ends.
            shutil.rmtree(tempdir, ignore_errors=True)

    @router.post('/upload', status_code=202)
    async def upload_media(destination: str, files: list[UploadFile] = File(...), full_capture: bool = False):
        if destination not in ('storm','players'):
            raise HTTPException(400, detail='Choose Storm Roster or Player List. VS Scores is coming soon.')
        if not files or len(files) > MAX_MEDIA_FILES:
            raise HTTPException(400,detail='Choose one recording or up to 75 screenshots.')
        if destination == 'storm' and any(Path(f.filename or '').suffix.lower() in VIDEO_EXTENSIONS for f in files):
            raise HTTPException(400,detail='Storm Roster only accepts PNG, JPG and WebP screenshots. Video is not supported.')
        if sum(1 for f in files if Path(f.filename or '').suffix.lower() in VIDEO_EXTENSIONS)>1:
            raise HTTPException(400,detail='Upload one video at a time; additional screenshots are allowed.')
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM media_scan_jobs WHERE status IN ('queued','scanning')")
                if int(cur.fetchone()['n']) >= 3:
                    raise HTTPException(429,detail='The scanner is busy. Please retry shortly.')
        work = Path(tempfile.mkdtemp(prefix='alliance_media_'))
        total_size = 0
        saved = []
        try:
            for i, upload in enumerate(files):
                ext = Path(upload.filename or '').suffix.lower()
                if ext not in ALLOWED_EXTENSIONS or (destination=='storm' and ext in VIDEO_EXTENSIONS):
                    raise HTTPException(400, detail='Storm Roster accepts images only. Player List accepts PNG, JPG, WebP or one video.')
                dest = work / f'upload_{i:03d}{ext}'
                with dest.open('wb') as f:
                    while data := await upload.read(1024 * 1024):
                        total_size += len(data)
                        if total_size > MAX_MEDIA_BYTES:
                            raise HTTPException(413,detail='The upload exceeds 150 MB.')
                        f.write(data)
                saved.append(dest)
            job_id = str(uuid.uuid4())
            with connect_db() as conn:
                with conn.cursor() as cur:
                    cur.execute('''INSERT INTO media_scan_jobs
                        (id,destination,status,progress,message,full_capture)
                        VALUES (%s,%s,'queued',0,'Queued for server scanning',%s)''',
                        (job_id,destination,False))
                conn.commit()
            executor.submit(run_job,job_id,destination,saved,work)
            return {'job_id':job_id,'destination':destination,'message':'Upload cached; OCR will run on the server.'}
        except Exception:
            shutil.rmtree(work,ignore_errors=True)
            raise

    @router.get('/jobs')
    def list_media_jobs():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id,destination,status,progress,message,result_count,
                    full_capture,created_at,updated_at FROM media_scan_jobs
                    ORDER BY created_at DESC LIMIT 35''')
                rows=cur.fetchall()
        for row in rows:
            for field in ('created_at','updated_at'):
                if row.get(field): row[field]=row[field].isoformat()+'Z'
        return {'jobs': rows}

    @router.get('/jobs/{job_id}/diagnostics')
    def get_job_diagnostics(job_id: str):
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT diagnostics_json,audit_json FROM media_scan_audit WHERE job_id=%s',(job_id,))
                row=cur.fetchone()
        if not row:raise HTTPException(404,detail='Scan diagnostics not available.')
        return {'diagnostics':json.loads(row['diagnostics_json'] or '{}'),
                'audit':json.loads(row['audit_json'] or '[]')}

    @router.get('/storm-roster')
    def get_storm_roster():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT team,player_rank AS `rank`,player_name AS name,
                    player_power AS power,player_role AS role,source_job_id
                    FROM `storm roster` ORDER BY team,player_rank DESC,player_name''')
                rows=cur.fetchall()
        return {'players':rows,'job_id':rows[0]['source_job_id'] if rows else None}

    @router.get('/pending')
    def get_pending_review():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT r.id,r.job_id,r.player_name AS name,r.player_rank AS `rank`,
                    r.power_millions,r.hq_level,r.avatar_base64,r.source_type,
                    r.leaderboard_tab AS tab,r.power,r.kills,r.donations,
                    r.review_required,r.review_reasons,r.source_image,r.evidence_base64,r.change_kind,
                    j.full_capture FROM `roster upload` r JOIN media_scan_jobs j ON j.id=r.job_id
                    ORDER BY r.id LIMIT 2000''')
                rows=cur.fetchall()
        for row in rows:
            if row['power_millions'] is not None:row['power_millions']=float(row['power_millions'])
            row['review_required']=bool(row.get('review_required'))
            try:row['review_reasons']=json.loads(row.get('review_reasons') or '[]')
            except (ValueError,TypeError):row['review_reasons']=[]
        return {'players':rows,'count':len(rows)}

    @router.post('/dismiss')
    def dismiss_review(body: DismissRows):
        # Explicit dismissal only. Unselected review rows remain staged.
        ids=list(dict.fromkeys(body.ids))
        if any(i<1 for i in ids):
            raise HTTPException(400,detail='Invalid review row id.')
        with connect_db() as conn:
            with conn.cursor() as cur:
                marks=','.join(['%s']*len(ids))
                cur.execute(f'DELETE FROM `roster upload` WHERE id IN ({marks})',tuple(ids))
                count=cur.rowcount
                cur.execute("""UPDATE media_scan_jobs j SET status='approved',
                    message='All pending Player List rows reviewed',updated_at=UTC_TIMESTAMP(6)
                    WHERE j.destination='players' AND j.status='review'
                    AND NOT EXISTS(SELECT 1 FROM `roster upload` r WHERE r.job_id=j.id)""")
            conn.commit()
        return {'dismissed':count}

    @router.post('/approve')
    def approve_review(body: ApprovalRequest):
        from app import Member
        ids = [item.id for item in body.rows]
        if len(ids) != len(set(ids)):
            raise HTTPException(400, detail='Duplicate review rows.')
        with connect_db() as conn:
            with conn.cursor() as cur:
                marks=','.join(['%s']*len(ids))
                cur.execute(f'''SELECT r.id,r.job_id,r.avatar_base64,j.full_capture FROM `roster upload` r
                    JOIN media_scan_jobs j ON j.id=r.job_id WHERE r.id IN ({marks})''',tuple(ids))
                matching={int(r['id']):r for r in cur.fetchall()}
        if len(matching)!=len(ids):
            raise HTTPException(409,detail='The pending list changed. Refresh before approving.')
        approved=[]
        for item in body.rows:
            if not item.include:continue
            info=matching[item.id]
            approved.append(Member(name=item.name,rank=item.rank,power_millions=item.power_millions,
                                   hq_level=item.hq_level,avatar_base64=info['avatar_base64'],
                                   power=item.power,kills=item.kills,donations=item.donations))
        # An absent player is not evidence of removal. Never mark missing.
        if not approved:
            raise HTTPException(400,detail='Select at least one valid reviewed player to approve.')
        result = import_players(import_model(players=approved,full_capture=False))
        approved_ids=[item.id for item in body.rows if item.include]
        with connect_db() as conn:
            with conn.cursor() as cur:
                marks=','.join(['%s']*len(approved_ids))
                cur.execute(f'DELETE FROM `roster upload` WHERE id IN ({marks})',tuple(approved_ids))
                cur.execute('''UPDATE media_scan_jobs j SET status='approved',
                    message='Player List changes reviewed',updated_at=UTC_TIMESTAMP(6)
                    WHERE j.destination='players' AND j.status='review'
                      AND NOT EXISTS(SELECT 1 FROM `roster upload` r WHERE r.job_id=j.id)''')
            conn.commit()
        return {'approved':len(approved),'remaining':len(ids)-len(approved),**result}

    app.include_router(router)

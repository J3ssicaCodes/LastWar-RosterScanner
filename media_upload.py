"""Shared asynchronous Media Upload service for Alliance Tools.

Only temporary uploads are stored on the Render filesystem. Scan metadata,
Storm Roster snapshots, and Player List review rows live in MySQL.
No browser credential or third party media service is required.
"""
from __future__ import annotations

import json
from datetime import date
import os
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from vs_points_ocr import VIDEO_EXTENSIONS

from player_list_ocr import scan_upload, extract_profile
from player_identity import (match_master, name_key, corrected_name,
                             load_name_corrections, learn_name_correction, exact_player_index, scan_player_ids, record_scan_players,
                             number_needs_review, NUMBER_REVIEW_PREFIX)
from vs_points_ocr import scan_vs_upload
from google_vision_ocr import recheck_vs_rows
from vs_points import stage_vs_result
from tested_rankings_adapter import scan_tested_rankings_upload
from storm_roster_ocr import scan_storm_upload
import cv2
from google_drive_source import download_selected_file, move_selected_file_to_trash, FILE_ID_PATTERN

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
    number_checked: bool = False


class ApprovalRequest(BaseModel):
    auto_match: bool = False
    rows: list[ReviewEntry] = Field(min_length=1, max_length=2000)
    # Accepted for older clients, but never used to flag absent players.
    full_capture: bool = False


class DismissRows(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)


class GoogleDriveImport(BaseModel):
    destination: str = Field(pattern=r'^(storm|players|vs)$')
    file_id: str = Field(min_length=8,max_length=256)
    access_token: str = Field(min_length=10,max_length=8192)
    move_to_trash: bool = False
    upload_date: date | None = None


def google_picker_config():
    return {
        'client_id': os.getenv('GOOGLE_OAUTH_CLIENT_ID','').strip(),
        'api_key': os.getenv('GOOGLE_PICKER_API_KEY','').strip(),
        'project_number': os.getenv('GOOGLE_CLOUD_PROJECT_NUMBER','').strip(),
    }



def install_media_upload(app, connect_db, import_players, import_model, shared_connect_db=None):
    router = APIRouter(prefix='/api/media', tags=['Media Upload'])
    review_match = """p.archived=m.reactivate AND
        (m.reactivate=TRUE OR p.last_import_id=m.last_import_id COLLATE utf8mb4_unicode_ci
         OR (p.last_import_id IS NULL AND m.last_import_id IS NULL))"""

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
                    started_at DATETIME(6) NULL,
                    finished_at DATETIME(6) NULL,
                    updated_player_ids TEXT NULL,
                    upload_date DATE NULL,
                    KEY idx_media_jobs_created (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                cur.execute('''CREATE TABLE IF NOT EXISTS media_missing_players (
                    player_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
                    job_id CHAR(36) NOT NULL,
                    last_import_id CHAR(36) NULL,
                    reactivate BOOLEAN NOT NULL DEFAULT FALSE,
                    KEY idx_media_missing_job (job_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='alliance_tools:user_data' ''')
                cur.execute("SHOW COLUMNS FROM media_missing_players LIKE 'reactivate'")
                if not cur.fetchone():
                    cur.execute('ALTER TABLE media_missing_players ADD COLUMN reactivate BOOLEAN NOT NULL DEFAULT FALSE')
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
                    ocr_name VARCHAR(255) NULL,
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
                cur.execute('''CREATE TABLE IF NOT EXISTS player_name_dictionary (
                    ocr_key VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin PRIMARY KEY,
                    player_name VARCHAR(255) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                cur.execute('''CREATE TABLE IF NOT EXISTS media_scan_audit (
                    job_id CHAR(36) NOT NULL PRIMARY KEY,
                    diagnostics_json LONGTEXT NULL,
                    audit_json LONGTEXT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                # Existing staging databases require non-destructive migrations.
                for field,definition in (
                    ('ocr_name','VARCHAR(255) NULL'),
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
                for field,definition in (('started_at','DATETIME(6)'),('finished_at','DATETIME(6)'),
                                         ('updated_player_ids','TEXT'), ('upload_date','DATE')):
                    cur.execute('SHOW COLUMNS FROM media_scan_jobs LIKE %s',(field,))
                    if not cur.fetchone():
                        cur.execute(f'ALTER TABLE media_scan_jobs ADD COLUMN {field} {definition} NULL')
                # A service restart should never leave a scan appearing to run forever.
                cur.execute("""UPDATE media_scan_jobs
                               SET status='interrupted', message='Scan was interrupted. Please upload again.',
                                   finished_at=IF(started_at IS NULL,NULL,COALESCE(finished_at,updated_at)),
                                   updated_at=UTC_TIMESTAMP(6)
                               WHERE status IN ('queued','scanning')""")
            conn.commit()
        print('Shared Media Upload MySQL tables initialized', flush=True)

    @app.on_event('startup')
    def startup_media():
        if os.getenv('ALLIANCE_WORKSPACE_MODE','').lower() == 'true':
            return
        try:
            schema()
        except Exception as exc:
            print('Media Upload schema unavailable:', type(exc).__name__, flush=True)

    def set_job(job_id: str, *, status: str | None = None, progress: int | None = None,
                message: str | None = None, count: int | None = None):
        fields = []
        values: list[Any] = []
        if status is not None: fields.append('status=%s'); values.append(status)
        if status == 'scanning':
            fields.append('started_at=COALESCE(started_at,UTC_TIMESTAMP(6))')
        if status in ('ready','review','error'):
            fields.append('finished_at=COALESCE(finished_at,UTC_TIMESTAMP(6))')
        if progress is not None: fields.append('progress=%s'); values.append(max(0, min(100, progress)))
        if message is not None: fields.append('message=%s'); values.append(message[:350])
        if count is not None: fields.append('result_count=%s'); values.append(count)
        fields.append('updated_at=UTC_TIMESTAMP(6)')
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('UPDATE media_scan_jobs SET ' + ','.join(fields) + ' WHERE id=%s', (*values, job_id))
            from scoped_db import current_workspace
            scope=current_workspace.get()
            if scope and scope.get('worker') and status in ('review','error'):
                from pymysql.cursors import DictCursor
                from alliance_claims import notify
                with conn.cursor(DictCursor) as cur:
                    notify(cur,scope['id'],scope['uploader_id'],'scan',
                           'Scan needs review' if status=='review' else 'Upload failed',
                           '/a/'+scope['slug']+'/manage?job='+job_id)
            conn.commit()

    def run_job(job_id: str, destination: str, paths: list[Path], tempdir: Path, drive=None, upload_date=None):
        finished_successfully=False
        try:
            set_job(job_id, status='scanning', progress=1, message='Preparing scan…')
            if drive:
                set_job(job_id,progress=2,message='Retrieving selected Google Drive file…')
                media_path=download_selected_file(drive['file_id'],drive['access_token'],destination,tempdir)
                paths=[media_path]
            def report(current, total, message):
                # Avoid issuing a database write for every OCR pixel or video frame.
                pct = round(100 * current / max(total, 1))
                if not hasattr(report, 'last') or pct != report.last or time.monotonic()-report.last_at>4:
                    set_job(job_id, progress=pct, message=message)
                    report.last = pct
                    report.last_at = time.monotonic()
            report.last = None
            report.last_at = 0.0
            with connect_db() as conn:
                with conn.cursor() as cur:
                    corrections = load_name_corrections(cur)
                    cur.execute('SELECT id,player_name,possibly_removed,archived,last_import_id FROM `player list`')
                    roster = cur.fetchall()
                    master_keys = {name_key(row['player_name']) for row in roster}
            if destination == 'vs':
                if upload_date is None:raise ValueError('Upload date is missing. Select the file again.')
                recheck_keys = {name_key(row['player_name']) for row in roster if not row['possibly_removed'] and not row.get('archived')}
                result=scan_vs_upload(paths,report,upload_date,corrections,master_keys,recheck_keys=recheck_keys)
                recheck_vs_rows(result,os.getenv('GOOGLE_VISION_API_KEY'),shared_connect_db or connect_db,corrections,master_keys,report)
                counts=stage_vs_result(connect_db,job_id,result)
                saved,pending=counts['saved'],counts['pending']
                gaps = result.get('missing_positions', [])
                coverage = f" · {len(gaps)} ranks not captured" if gaps else ''
                skipped = counts['skipped']
                if skipped:
                    coverage += f" · {skipped} unchanged"
                found = result.get('found_missing_players',0)
                if found:
                    coverage += f" · {found} found on recheck"
                set_job(job_id,status='review' if pending else 'ready',progress=100,count=saved+pending+skipped,
                        message=(f"{result['weekday']} · {result['score_date']} · {saved} VS scores saved · {pending} to review{coverage}"
                                 if result['score_date'] else f"Choose the VS weekday to review {pending} captured scores.{coverage}"))
                finished_successfully=True
                return
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
                    result = scan_tested_rankings_upload(leaderboard_files, report, corrections, master_keys)
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
                    name = corrected_name(str(row.get('name') or '').strip(),corrections,master_keys)
                    team = str(row.get('team') or '?').upper()
                    if not name or team not in ('A','B'): continue
                    key = (team, normalize_name(name))
                    if key[1] and (key not in dedup or not dedup[key].get('power')):
                        dedup[key] = {**row,'name':name}
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
                from app import Member, ImportData, normalize_name
                with connect_db() as conn, conn.cursor() as cur:
                    cur.execute('SELECT id,player_name,player_rank FROM `player list`')
                    exact=exact_player_index(cur.fetchall())
                    corrections=load_name_corrections(cur)
                merge=[];needs_review=[]; merged_ids=set()
                prepared=[]
                numbers_by_player={}
                for row in records:
                    raw_name=row.get('ocr_name',row.get('name')) or ''
                    row={**row,'ocr_name':raw_name,'name':corrected_name(raw_name,corrections,exact.keys())}
                    stat=row.get('tab')
                    key=(name_key(row['name']),stat)
                    if key[0] and stat and type(row.get(stat)) is int:
                        numbers_by_player.setdefault(key,set()).add(row[stat])
                    prepared.append(row)
                for row in prepared:
                    current=exact.get(name_key(row['name']))
                    stat=row.get('tab')
                    readings=numbers_by_player.get((name_key(row['name']),stat),set())
                    if len(readings)>1:
                        row['review_required']=True
                        row['review_reasons']=[*(row.get('review_reasons') or []),
                            f'{NUMBER_REVIEW_PREFIX} conflicting {stat} values: '+', '.join(map(str,sorted(readings)))+'.']
                    valid_stat=not stat or (type(row.get(stat)) is int and 0 <= row[stat] <= 18446744073709551615)
                    if stat and not valid_stat:
                        row[stat]=None
                        row['review_required']=True
                        row['review_reasons']=[*(row.get('review_reasons') or []),f'Check {stat}']
                    if current:
                        row['name']=current['player_name']
                        if stat:
                            row['rank']=current['player_rank']
                        if valid_stat and not number_needs_review(row.get('review_reasons') or []) and (stat or (not row.get('review_required') and row.get('rank') in ('R1','R2','R3','R4','R5'))):
                            fields={field:row.get(field) for field in
                                    ('power_millions','power','kills','donations','hq_level','avatar_base64')}
                            merge.append(Member(name=current['player_name'],
                                rank=row['rank'],upload_date=upload_date,
                                **fields))
                            merged_ids.add(current['id'])
                            continue
                    needs_review.append(row)
                records=needs_review
                observed_ids={exact[name_key(row['name'])]['id'] for row in prepared if exact.get(name_key(row['name']))}
                video_ids={exact[name_key(row['name'])]['id'] for row in prepared
                           if row.get('seen_in_video') and exact.get(name_key(row['name']))}
                video_capture=any(Path(path).suffix.lower() in VIDEO_EXTENSIONS for path in paths)
                missing=[(row['id'],job_id,row.get('last_import_id'),False) for row in roster
                         if video_capture and prepared and row.get('id') is not None and
                         row['id'] not in observed_ids and not row.get('archived')]
                returning=[(row['id'],job_id,None,True) for row in roster
                           if video_capture and row.get('id') in video_ids and row.get('archived')]
                with connect_db() as conn:
                    try:
                        with conn.cursor() as cur:
                            from app import normalize_name
                            if merge:
                                updated_ids=scan_player_ids(cur,job_id,lock=True)
                                import_players(import_model(players=merge,full_capture=False),connection=conn)
                                record_scan_players(cur,job_id,merged_ids,updated_ids)
                            if observed_ids:
                                marks=','.join(['%s']*len(observed_ids))
                                cur.execute(f'DELETE FROM media_missing_players WHERE reactivate=FALSE AND player_id IN ({marks})',
                                            tuple(sorted(observed_ids)))
                            if missing or returning:
                                cur.executemany("""INSERT INTO media_missing_players (player_id,job_id,last_import_id,reactivate)
                                    VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                                    job_id=VALUES(job_id),last_import_id=VALUES(last_import_id),reactivate=VALUES(reactivate)""",
                                    missing+returning)
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
                                cur.execute('SELECT id,player_name,player_rank,review_reasons FROM `roster upload` WHERE job_id=%s ORDER BY id',(job_id,))
                                previous=exact_player_index(cur.fetchall()).get(name_key(name))
                                earlier_reasons=json.loads(previous.get('review_reasons') or '[]') if previous else []
                                if previous and name and not number_needs_review(earlier_reasons+reasons):
                                    combined=list(dict.fromkeys(earlier_reasons+reasons))
                                    cur.execute("""UPDATE `roster upload` SET
                                        power=COALESCE(%s,power),kills=COALESCE(%s,kills),
                                        donations=COALESCE(%s,donations),power_millions=COALESCE(%s,power_millions),
                                        hq_level=COALESCE(%s,hq_level),
                                        player_rank=IF(%s='Unknown',player_rank,%s),
                                        review_required=TRUE,review_reasons=%s,evidence_base64=COALESCE(%s,evidence_base64)
                                        WHERE id=%s""",
                                        (row.get('power'),row.get('kills'),row.get('donations'),row.get('power_millions'),
                                         row.get('hq_level'),rank,rank,json.dumps(combined),row.get('evidence_base64'),previous['id']))
                                    continue
                                cur.execute("""INSERT INTO `roster upload`
                                    (job_id,player_name,player_rank,power_millions,hq_level,avatar_base64,
                                     source_type,leaderboard_tab,leaderboard_position,power,kills,donations,
                                     review_required,review_reasons,source_image,audit_json,evidence_base64,change_kind,ocr_name)
                                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                    (job_id,name[:255],rank,row.get('power_millions'),row.get('hq_level'),
                                     row.get('avatar_base64'),row.get('source_type','leaderboard'),
                                     row.get('tab'),row.get('internal_position'),row.get('power'),
                                     row.get('kills'),row.get('donations'),review,
                                     json.dumps(reasons,ensure_ascii=False)[:8000],
                                     str(row.get('source_image') or '')[:255],
                                     json.dumps(audit_fields,ensure_ascii=False)[:60000],
                                     str(row.get('evidence_base64') or '')[:320000] or None,change_kind,row['ocr_name'][:255]))
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
                if inserted or missing or returning:
                    set_job(job_id,status='review',progress=100,count=len(merge)+inserted,
                            message=(f'{len(merge)} records updated · {inserted} scan rows · '
                                     f'{len(missing)} missing and {len(returning)} archived players to review.'))
                else:
                    set_job(job_id,status='ready',progress=100,count=len(merge),
                            message=f'Scan finished. {len(merge)} master records merged.')
            finished_successfully=True
        except Exception as exc:
            # Exception details are shown in the UI only if they contain no secrets.
            print('Media OCR scan failed:',type(exc).__name__, flush=True)
            try: set_job(job_id,status='error',progress=0,message=str(exc)[:240])
            except Exception: pass
        finally:
            if drive and drive.get('move_to_trash') and finished_successfully:
                try:
                    move_selected_file_to_trash(drive['file_id'],drive['access_token'])
                    set_job(job_id,message='Scan finished. Selected Google Drive original moved to Trash.')
                except Exception:
                    # Never destroy scan results if Google access expired or the
                    # selected source cannot be trashed (e.g. a shared file).
                    try:
                        set_job(job_id,message='Scan finished, but the Google Drive original could not be moved to Trash. Delete it manually if needed.')
                    except Exception:
                        pass
            # Drive tokens are never saved to the DB or a file.
            shutil.rmtree(tempdir, ignore_errors=True)

    @router.get('/google-config')
    def get_google_drive_settings():
        cfg=google_picker_config()
        # OAuth Client ID, restricted Picker API key and project number are
        # explicitly public browser credentials, never a Google client secret.
        configured=all(cfg.values())
        return {'configured':configured,
                'client_id':cfg['client_id'] if configured else '',
                'api_key':cfg['api_key'] if configured else '',
                'project_number':cfg['project_number'] if configured else ''}

    @router.post('/google-import',status_code=202)
    def import_from_google_drive(body: GoogleDriveImport):
        if not all(google_picker_config().values()):
            raise HTTPException(503,detail='Google Drive selection needs one-time app configuration.')
        if body.destination=='vs' and body.upload_date is None:
            raise HTTPException(400,detail='Your local upload date is required for VS points.')
        if not FILE_ID_PATTERN.fullmatch(body.file_id):
            raise HTTPException(400,detail='Invalid selected Google Drive file.')
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM media_scan_jobs WHERE status IN ('queued','scanning')")
                if int(cur.fetchone()['n']) >= 3:
                    raise HTTPException(429,detail='The scan queue is full. Try again when another scan finishes.')
        work=Path(tempfile.mkdtemp(prefix='alliance_drive_media_'))
        job_id=str(uuid.uuid4())
        from scoped_db import current_workspace
        scope=current_workspace.get()
        try:
            with connect_db() as conn:
                with conn.cursor() as cur:
                    if scope:
                        cur.execute("""INSERT INTO media_scan_jobs
                            (id,destination,status,progress,message,full_capture,upload_date,uploaded_by)
                            VALUES (%s,%s,'queued',0,'Waiting to scan selected Google Drive file',FALSE,%s,%s)""",
                            (job_id,body.destination,body.upload_date,scope["actor"]["id"]))
                    else:
                        cur.execute("""INSERT INTO media_scan_jobs
                            (id,destination,status,progress,message,full_capture,upload_date)
                            VALUES (%s,%s,'queued',0,'Waiting to scan selected Google Drive file',FALSE,%s)""",
                            (job_id,body.destination,body.upload_date))
                conn.commit()
            token_data={'file_id':body.file_id,'access_token':body.access_token,
                        'move_to_trash':body.move_to_trash}
            if os.getenv('ALLIANCE_WORKSPACE_MODE','').lower() == 'true':
                from contextvars import copy_context
                from scoped_db import run_worker
                executor.submit(copy_context().run,run_worker,job_id,run_job,job_id,body.destination,[],work,token_data,body.upload_date)
            else:
                executor.submit(run_job,job_id,body.destination,[],work,token_data,body.upload_date)
            return {'job_id':job_id,'destination':body.destination,
                    'message':'Google Drive file queued for scanning.'}
        except Exception:
            shutil.rmtree(work,ignore_errors=True)
            raise

    @router.post('/upload', status_code=202)
    async def upload_media(destination: str, files: list[UploadFile] = File(...), full_capture: bool = False, upload_date: date | None = None):
        if destination not in ('storm','players','vs'):
            raise HTTPException(400, detail='Choose Storm Roster, Player List or VS Points.')
        if destination=='vs' and upload_date is None:
            raise HTTPException(400,detail='Your local upload date is required for VS points.')
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
            from scoped_db import current_workspace
            scope=current_workspace.get()
            with connect_db() as conn:
                with conn.cursor() as cur:
                    if scope:
                        cur.execute('''INSERT INTO media_scan_jobs
                            (id,destination,status,progress,message,full_capture,upload_date,uploaded_by)
                            VALUES (%s,%s,'queued',0,'Waiting to scan',%s,%s,%s)''',
                            (job_id,destination,False,upload_date,scope["actor"]["id"]))
                    else:
                        cur.execute('''INSERT INTO media_scan_jobs
                            (id,destination,status,progress,message,full_capture,upload_date)
                            VALUES (%s,%s,'queued',0,'Waiting to scan',%s,%s)''',
                            (job_id,destination,False,upload_date))
                conn.commit()
            if os.getenv('ALLIANCE_WORKSPACE_MODE','').lower() == 'true':
                from contextvars import copy_context
                from scoped_db import run_worker
                executor.submit(copy_context().run,run_worker,job_id,run_job,job_id,destination,saved,work,upload_date=upload_date)
            else:
                executor.submit(run_job,job_id,destination,saved,work,upload_date=upload_date)
            return {'job_id':job_id,'destination':destination,'message':'Upload accepted. Waiting to scan.'}
        except Exception:
            shutil.rmtree(work,ignore_errors=True)
            raise

    @router.get('/jobs')
    def list_media_jobs():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f'''SELECT j.id,j.destination,j.status,j.progress,j.message,j.result_count,
                    j.full_capture,j.created_at,j.updated_at,j.started_at,j.finished_at,
                    CASE j.destination
                        WHEN 'players' THEN (SELECT COUNT(*) FROM `roster upload` r WHERE r.job_id=j.id)
                           + (SELECT COUNT(*) FROM media_missing_players m JOIN `player list` p ON p.id=m.player_id
                              WHERE m.job_id=j.id AND {review_match})
                        WHEN 'vs' THEN (SELECT COUNT(*) FROM vs_scan_rows v WHERE v.job_id=j.id)
                        ELSE 0
                    END AS pending_count
                    FROM media_scan_jobs j
                    WHERE j.status IN ('queued','scanning','review')
                      OR (j.destination='players' AND EXISTS (SELECT 1 FROM `roster upload` r WHERE r.job_id=j.id))
                      OR (j.destination='vs' AND EXISTS (SELECT 1 FROM vs_scan_rows v WHERE v.job_id=j.id))
                      OR EXISTS (SELECT 1 FROM media_missing_players m JOIN `player list` p ON p.id=m.player_id
                                 WHERE m.job_id=j.id AND {review_match})
                      OR j.id IN (SELECT recent.id FROM
                          (SELECT id FROM media_scan_jobs ORDER BY created_at DESC,id DESC LIMIT 35) recent)
                    ORDER BY j.created_at DESC,j.id DESC''')
                rows=cur.fetchall()
        active,review,history=[],[],[]
        for row in rows:
            for field in ('created_at','updated_at','started_at','finished_at'):
                if row.get(field): row[field]=row[field].isoformat()+'Z'
            if row['status'] in ('queued','scanning'):
                active.append(row)
            elif row['pending_count']:
                review.append(row)
            else:
                if row['status']=='review':
                    row.update(status='approved',message='No rows left to review.')
                history.append(row)
        return {'jobs':active+review+history[:35]}

    @router.delete('/jobs/history')
    def clear_old_scan_history():
        # Keep active scans and Player List jobs with pending review records.
        # This only clears finished historical scan metadata and audit data.
        with connect_db() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(f"""SELECT j.id FROM media_scan_jobs j
                        WHERE j.status IN ('ready','approved','error','interrupted','review')
                          AND NOT EXISTS (SELECT 1 FROM `roster upload` r WHERE r.job_id=j.id)
                          AND NOT EXISTS (SELECT 1 FROM vs_scan_rows v WHERE v.job_id=j.id)
                          AND NOT EXISTS (SELECT 1 FROM media_missing_players m JOIN `player list` p ON p.id=m.player_id
                                          WHERE m.job_id=j.id AND {review_match})""")
                    ids=[r['id'] for r in cur.fetchall()]
                    if ids:
                        marks=','.join(['%s']*len(ids))
                        cur.execute(f'DELETE FROM media_missing_players WHERE job_id IN ({marks})',tuple(ids))
                        cur.execute(f'DELETE FROM media_scan_audit WHERE job_id IN ({marks})',tuple(ids))
                        cur.execute(f'DELETE FROM media_scan_jobs WHERE id IN ({marks})',tuple(ids))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {'cleared':len(ids)}

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

    @router.get('/missing-players')
    def list_missing_players():
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute(f"""SELECT p.id,p.player_name AS name,p.player_rank AS `rank`,m.job_id,m.reactivate
                FROM media_missing_players m JOIN `player list` p ON p.id=m.player_id
                JOIN media_scan_jobs j ON j.id=m.job_id
                WHERE {review_match} ORDER BY m.reactivate DESC,p.player_name,p.id""")
            return {'players':cur.fetchall()}

    @router.put('/missing-players/{player_id}')
    def decide_missing_player(player_id:int, job_id:str, archive:bool=False,
                              action:Literal['restore','keep_archived']|None=None):
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('SELECT id FROM `player list` WHERE id=%s FOR UPDATE',(player_id,))
            if not cur.fetchone():
                raise HTTPException(404,detail='Player is no longer on the roster.')
            cur.execute(f"""SELECT p.id FROM media_missing_players m JOIN `player list` p ON p.id=m.player_id
                JOIN media_scan_jobs j ON j.id=m.job_id
                WHERE m.player_id=%s AND m.job_id=%s AND m.reactivate=%s AND {review_match} FOR UPDATE""",
                        (player_id,job_id,action is not None))
            if not cur.fetchone():
                raise HTTPException(409,detail='This review has changed. Refresh before trying again.')
            if action=='restore':
                restore_player(cur,player_id)
            elif archive and action is None:
                cur.execute('UPDATE `player list` SET archived=TRUE,updated_at=UTC_TIMESTAMP(6) WHERE id=%s',(player_id,))
            cur.execute('DELETE FROM media_missing_players WHERE player_id=%s',(player_id,))
            conn.commit()
        return {'archived':action=='keep_archived' if action else archive}

    def restore_player(cur,player_id):
        cur.execute("""UPDATE `player list` SET archived=FALSE,possibly_removed=FALSE,updated_at=UTC_TIMESTAMP(6)
                       WHERE id=%s AND archived=TRUE""",(player_id,))
        return cur.rowcount

    @router.get('/archived-players')
    def list_archived_players():
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT id,player_name AS name,player_rank AS `rank` FROM `player list`
                           WHERE archived=TRUE ORDER BY player_name,id""")
            return {'players':cur.fetchall()}

    @router.put('/archived-players/{player_id}/restore')
    def restore_archived_player(player_id:int):
        with connect_db() as conn, conn.cursor() as cur:
            if not restore_player(cur,player_id):
                raise HTTPException(409,detail='This player is no longer archived. Refresh and try again.')
            cur.execute('DELETE FROM media_missing_players WHERE player_id=%s',(player_id,))
            conn.commit()
        return {'archived':False}

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
                updated={job:sorted(scan_player_ids(cur,job)) for job in {row['job_id'] for row in rows}}
        for row in rows:
            if row['power_millions'] is not None:row['power_millions']=float(row['power_millions'])
            row['review_required']=bool(row.get('review_required'))
            try:row['review_reasons']=json.loads(row.get('review_reasons') or '[]')
            except (ValueError,TypeError):row['review_reasons']=[]
        return {'players':rows,'count':len(rows),'updated_by_job':updated}

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
        selected = [item for item in body.rows if item.include]
        ids = [item.id for item in selected]
        if not ids or len(ids) != len(set(ids)) or (body.auto_match and len(ids)!=1):
            raise HTTPException(400,detail='Select valid review rows.')
        with connect_db() as conn:
            try:
                with conn.cursor() as cur:
                    marks=','.join(['%s']*len(ids))
                    cur.execute(f'''SELECT r.id,r.job_id,COALESCE(r.ocr_name,r.player_name) AS original_name,
                        r.avatar_base64,r.leaderboard_tab,r.review_reasons,COALESCE(j.upload_date,DATE(j.created_at)) AS upload_date
                        FROM `roster upload` r LEFT JOIN media_scan_jobs j ON j.id=r.job_id
                        WHERE r.id IN ({marks}) FOR UPDATE''',tuple(ids))
                    matching={int(row['id']):row for row in cur.fetchall()}
                    if len(matching)!=len(ids):
                        raise HTTPException(409,detail='The pending list changed. Refresh before approving.')
                    jobs={row['job_id'] for row in matching.values()}
                    updated={job:scan_player_ids(cur,job,lock=True) for job in sorted(jobs)}
                    cur.execute('SELECT id,player_name,player_rank FROM `player list`')
                    roster=cur.fetchall()
                    exact=exact_player_index(roster)
                    approved=[]
                    keys_by_job={job:set() for job in jobs}
                    for item in selected:
                        info=matching[item.id]
                        if number_needs_review(json.loads(info['review_reasons'] or '[]')) and (body.auto_match or not item.number_checked):
                            raise HTTPException(422,detail='Compare the number with the capture and mark it checked before saving.')
                        player=exact.get(name_key(item.name)) if body.auto_match else match_master({'name':item.name},roster)
                        if body.auto_match:
                            if not player:
                                raise HTTPException(409,detail='Choose an exact name from the Player List.')
                            if player['id'] in updated[info['job_id']]:
                                raise HTTPException(409,detail='That player was already updated by this scan.')
                            tab=info['leaderboard_tab']
                            if tab and getattr(item,tab,None) is None:
                                raise HTTPException(422,detail=f'Enter the {tab} value before saving.')
                        name=player['player_name'] if player else item.name.strip()
                        rank=player['player_rank'] if body.auto_match and item.rank=='Unknown' else item.rank
                        approved.append(Member(name=name,rank=rank,power_millions=item.power_millions,
                                               hq_level=item.hq_level,avatar_base64=info['avatar_base64'],
                                               power=item.power,kills=item.kills,donations=item.donations,
                                               upload_date=info['upload_date']))
                        keys_by_job[info['job_id']].add(name_key(name))
                        learn_name_correction(cur,info['original_name'],name)
                    result=import_players(import_model(players=approved,full_capture=False),connection=conn)
                    cur.execute('SELECT id,player_name FROM `player list` WHERE last_import_id=%s',(result['import_id'],))
                    saved=exact_player_index(cur.fetchall())
                    for job,keys in keys_by_job.items():
                        record_scan_players(cur,job,[saved[key]['id'] for key in keys if saved.get(key)],updated[job])
                    cur.execute(f'DELETE FROM `roster upload` WHERE id IN ({marks})',tuple(ids))
                    job_marks=','.join(['%s']*len(jobs))
                    cur.execute(f'''UPDATE media_scan_jobs j SET status='approved',
                        message='Player List changes reviewed',updated_at=UTC_TIMESTAMP(6)
                        WHERE j.id IN ({job_marks}) AND j.status='review'
                          AND NOT EXISTS(SELECT 1 FROM `roster upload` r WHERE r.job_id=j.id)''',tuple(sorted(jobs)))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {'approved':len(approved),'remaining':0,**result}

    app.include_router(router)

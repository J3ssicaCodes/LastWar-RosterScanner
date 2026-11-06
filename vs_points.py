"""Persistent VS daily scores and review, keyed by player and earned date."""
from datetime import date, timedelta
from collections import defaultdict
from difflib import SequenceMatcher
import csv
import io
import json

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from player_identity import (match_master, name_key, learn_name_correction, exact_player_index,
                             corrected_name, load_name_corrections, scan_player_ids, record_scan_players,
                             number_needs_review, NUMBER_REVIEW_PREFIX)
from vs_points_ocr import roster_portrait_match, score_date_for_day
from player_merge import merge_vs_rows


class VSRow(BaseModel):
    id: int = Field(ge=1)
    name: str = Field(min_length=2,max_length=80)
    points: int = Field(ge=0,le=9007199254740991)
    include: bool = True
    number_checked: bool = False


class VSApproval(BaseModel):
    auto_match: bool = False
    job_id: str
    score_date: date
    rows: list[VSRow] = Field(min_length=1,max_length=200)


class VSClaim(BaseModel):
    name_key: str = Field(min_length=1,max_length=255)
    expected_player_name: str


def claimable_vs_names(connect_db, player_id):
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute('SELECT player_name FROM `player list` WHERE id=%s',(player_id,))
        player = cur.fetchone()
        if not player:
            raise HTTPException(404,detail='Player not found.')
        cur.execute("""SELECT v.normalized_name AS name_key,MAX(v.player_name) AS name,COUNT(*) AS score_days
                       FROM vs_points v
                       LEFT JOIN `player list` linked ON linked.id=v.player_id
                       LEFT JOIN `player list` named ON named.normalized_name=v.normalized_name
                       WHERE linked.id IS NULL AND named.id IS NULL GROUP BY v.normalized_name""")
        options = cur.fetchall()
    key = name_key(player['player_name'])
    return sorted(options,key=lambda row: (-SequenceMatcher(None,key,row['name_key']).ratio(),row['name'].casefold()))


def claim_vs_scores(connect_db, player_id, body):
    source_key = name_key(body.name_key)
    with connect_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT id,player_name,normalized_name FROM `player list` ORDER BY id FOR UPDATE')
                players = cur.fetchall()
                target = next((p for p in players if p['id']==player_id),None)
                if not target or target['player_name'] != body.expected_player_name:
                    raise HTTPException(409,detail='The player changed. Reopen the profile and try again.')
                if any(p['normalized_name']==source_key for p in players):
                    raise HTTPException(409,detail='That name belongs to a player. Use Merge duplicate in the player profile.')
                cur.execute("""SELECT id,score_date,player_id,normalized_name,player_name FROM vs_points
                               WHERE player_id=%s OR normalized_name IN (%s,%s)
                               ORDER BY score_date,id FOR UPDATE""",
                            (player_id,target['normalized_name'],source_key))
                rows = cur.fetchall()
                roster_ids = {p['id'] for p in players}
                if any(r['player_id'] in roster_ids and r['player_id']!=player_id for r in rows):
                    raise HTTPException(409,detail='A VS score is linked to another player. Refresh and check the selected name.')
                source = [r for r in rows if r['normalized_name']==source_key and r['player_id']!=player_id]
                if not source:
                    raise HTTPException(409,detail='This VS name has already been claimed or removed. Refresh and try again.')
                target_name = target['player_name']
                overlapping = merge_vs_rows(cur,rows,player_id,target_name)
                learn_name_correction(cur,source[0]['player_name'],target_name)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {'claimed_name':source[0]['player_name'],'score_days':len(source),
            'overlapping_vs_scores':overlapping}



def upsert_vs_score(cursor, date, player_id, name, points, job_id):
    cursor.execute('''INSERT INTO vs_points
        (score_date,player_id,player_name,normalized_name,points,source_job_id)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE player_name=VALUES(player_name),
        player_id=VALUES(player_id),normalized_name=VALUES(normalized_name),
        points=VALUES(points),source_job_id=VALUES(source_job_id),updated_at=UTC_TIMESTAMP(6)''',
        (date,player_id,name,name_key(name),points,job_id))


def vs_scan_player_ids(cursor, job_id, lock=False):
    updated=scan_player_ids(cursor,job_id,lock=lock)
    # Older jobs recorded provenance on the score but had no scan ledger.
    cursor.execute('SELECT player_id FROM vs_points WHERE source_job_id=%s AND player_id IS NOT NULL',(job_id,))
    return updated | {row['player_id'] for row in cursor.fetchall()}


def stage_vs_result(connect_db, job_id, result):
    saved = pending = skipped = 0
    with connect_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT id,player_name FROM `player list` ORDER BY id FOR UPDATE')
                master = exact_player_index(cur.fetchall())
                updated=scan_player_ids(cur,job_id,lock=True)
                corrections = load_name_corrections(cur)
                cur.execute("""SELECT player_id,normalized_name,points FROM vs_points
                               WHERE score_date=%s ORDER BY player_id IS NULL,id FOR UPDATE""",
                            (result['score_date'],))
                existing_scores = {}
                roster_ids = {player['id'] for player in master.values() if player}
                for entry in cur.fetchall():
                    pid = entry['player_id']
                    if pid not in roster_ids:
                        known = master.get(entry['normalized_name'])
                        if not known:
                            continue
                        pid = known['id']
                    existing_scores.setdefault(pid,int(entry['points']))
                portraits = None
                candidates = []
                scores = defaultdict(set)
                for row in result['rows']:
                    raw = row.get('ocr_name', row['name'])
                    name = corrected_name(row['name'], corrections, master.keys())
                    exact = master.get(name_key(name))
                    player = exact
                    if not player and row.get('avatar_base64'):
                        if portraits is None:
                            cur.execute('SELECT player_name,avatar_image FROM `player list` WHERE avatar_image IS NOT NULL')
                            portraits = {}
                            for entry in cur.fetchall():
                                key = name_key(entry['player_name'])
                                if not master.get(key) or not entry['avatar_image']:
                                    continue
                                image = cv2.imdecode(np.frombuffer(entry['avatar_image'], np.uint8), cv2.IMREAD_GRAYSCALE)
                                if image is not None:
                                    portraits[key] = cv2.resize(image,(96,96),interpolation=cv2.INTER_AREA)
                        player = master.get(roster_portrait_match(row,name,portraits))
                    points = row['points']
                    valid = type(points) is int and 0 <= points <= 9007199254740991
                    if player and valid:
                        scores[player['id']].add(points)
                    candidates.append((row, raw, name, player, valid, exact))
                retained, retained_scores, skipped_ids = [], defaultdict(set), set()
                for row, raw, name, player, valid, exact in candidates:
                    pid = player['id'] if player else None
                    concerned = row['review_reasons'] or not valid or pid is None or len(scores[pid]) > 1
                    if concerned and existing_scores:
                        readings = set(row.get('point_readings',[]))
                        if valid:
                            readings.add(row['points'])
                        # An exact selected name wins. Otherwise require one unambiguous
                        # roster name from the alternate frame readings.
                        if exact:
                            matching_ids = {exact['id']}
                        else:
                            names = [master.get(name_key(corrected_name(key,corrections,master.keys())))
                                     for key in (raw,*row.get('name_readings',[]))]
                            matching_ids = {candidate['id'] for candidate in names if candidate}
                        if (len(matching_ids)==1 and (not player or pid in matching_ids)
                                and existing_scores.get(next(iter(matching_ids))) in readings):
                            skipped_ids.update(matching_ids)
                            skipped += 1
                            continue
                    retained.append((row,raw,name,player,valid))
                    if player and valid:
                        retained_scores[pid].add(row['points'])
                scores = retained_scores
                saved_ids = set()
                for row, raw, name, player, valid in retained:
                    points = row['points']
                    if player and valid and len(scores[player['id']]) == 1 and not number_needs_review(row['review_reasons']):
                        if player['id'] not in saved_ids:
                            upsert_vs_score(cur,result['score_date'],player['id'],player['player_name'],points,job_id)
                            saved_ids.add(player['id'])
                            saved += 1
                        continue
                    reasons = list(row['review_reasons'])
                    if not player: reasons.append('Match name to Player List or verify new player')
                    if not valid: reasons.append('Check points')
                    if player and len(scores[player['id']]) > 1:
                        reasons.append(f'{NUMBER_REVIEW_PREFIX} conflicting points for the same player.')
                    cur.execute('''INSERT INTO vs_scan_rows
                        (job_id,score_date,upload_date,weekday,player_name,points,review_reasons,evidence_base64,ocr_name)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (job_id,result['score_date'],result['upload_date'],result['weekday'],
                         player['player_name'] if player else name, points if valid else None,
                         json.dumps(reasons),row.get('evidence_base64'),raw[:255]))
                    pending += 1
                record_scan_players(cur,job_id,saved_ids | skipped_ids,updated)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {'saved':saved,'pending':pending,'skipped':skipped}


def install_vs_points(app, connect_db):
    router=APIRouter(prefix='/api/vs-points')

    @app.on_event('startup')
    def schema_vs():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS vs_points (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    score_date DATE NOT NULL,
                    player_id BIGINT UNSIGNED NULL,
                    player_name VARCHAR(255) NOT NULL,
                    normalized_name VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
                    points BIGINT UNSIGNED NOT NULL,
                    source_job_id CHAR(36) NULL,
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    UNIQUE KEY uq_vs_date_name (score_date,normalized_name),
                    UNIQUE KEY uq_vs_date_player (score_date,player_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                cur.execute('''CREATE TABLE IF NOT EXISTS vs_scan_rows (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    job_id CHAR(36) NOT NULL,
                    score_date DATE NOT NULL,
                    upload_date DATE NOT NULL,
                    weekday CHAR(3) NOT NULL,
                    player_name VARCHAR(255) NOT NULL,
                    points BIGINT UNSIGNED NULL,
                    review_reasons TEXT NULL,
                    evidence_base64 MEDIUMTEXT NULL,
                    ocr_name VARCHAR(255) NULL,
                    KEY idx_vs_pending_job (job_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
                cur.execute('SHOW COLUMNS FROM vs_scan_rows LIKE %s',('ocr_name',))
                if not cur.fetchone():
                    cur.execute('ALTER TABLE vs_scan_rows ADD COLUMN ocr_name VARCHAR(255) NULL')
            conn.commit()

    @router.get('')
    def list_scores(score_date: date | None = None):
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT DISTINCT score_date FROM vs_points ORDER BY score_date DESC LIMIT 400')
                dates=[r['score_date'].isoformat() for r in cur.fetchall()]
                chosen=score_date or (date.fromisoformat(dates[0]) if dates else None)
                if chosen:
                    cur.execute('''SELECT id,score_date,player_id,player_name AS name,points
                        FROM vs_points WHERE score_date=%s ORDER BY points DESC,player_name''',(chosen,))
                    rows=cur.fetchall()
                else:rows=[]
        for row in rows:row['score_date']=row['score_date'].isoformat()
        return {'rows':rows,'dates':dates,'score_date':chosen.isoformat() if chosen else None}

    @router.get('/weekly')
    def weekly_scores(week_start: date):
        monday = week_start - timedelta(days=week_start.weekday())
        days = [monday + timedelta(days=i) for i in range(6)]
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT DISTINCT score_date FROM vs_points')
                dates = [row['score_date'] for row in cur.fetchall()]
                cur.execute('''SELECT v.score_date,v.points,
                    COALESCE(linked.player_name,named.player_name,v.player_name) AS name,
                    COALESCE(linked.normalized_name,named.normalized_name,v.normalized_name) AS player_key,
                    COALESCE(linked.id,named.id) AS roster_id
                    FROM vs_points v
                    LEFT JOIN `player list` linked ON linked.id=v.player_id
                    LEFT JOIN `player list` named ON named.normalized_name=v.normalized_name
                    WHERE v.score_date BETWEEN %s AND %s
                    ORDER BY v.player_id IS NULL,v.updated_at DESC''',(days[0],days[-1]))
                rows = cur.fetchall()
        weeks = {day - timedelta(days=day.weekday()) for day in dates}
        weeks.add(monday)
        for row in rows:
            row['score_date'] = row['score_date'].isoformat()
        return {'rows':rows, 'days':[day.isoformat() for day in days],
                'weeks':[week.isoformat() for week in sorted(weeks,reverse=True)],
                'week_start':monday.isoformat()}

    @router.get('/export.csv')
    def export_scores(score_date: date | None = None, week_start: date | None = None):
        result=weekly_scores(week_start) if week_start else list_scores(score_date)
        buf=io.StringIO();writer=csv.writer(buf);writer.writerow(['Date','Player Name','VS Points'])
        for row in result['rows']:writer.writerow([row['score_date'],row['name'],row['points']])
        return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="vs_points.csv"'})

    @router.get('/claims/{player_id}')
    def list_claims(player_id: int):
        return {'options':claimable_vs_names(connect_db,player_id)}

    @router.post('/claims/{player_id}')
    def claim_scores(player_id: int, body: VSClaim):
        return claim_vs_scores(connect_db,player_id,body)

    @router.get('/pending')
    def pending_scores():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id,job_id,score_date,upload_date,weekday,player_name AS name,
                    points,review_reasons,evidence_base64 FROM vs_scan_rows ORDER BY id LIMIT 2000''')
                rows=cur.fetchall()
                updated={job:sorted(vs_scan_player_ids(cur,job)) for job in {row['job_id'] for row in rows}}
        for row in rows:
            for key in ('score_date','upload_date'):row[key]=row[key].isoformat()
            row['review_reasons']=json.loads(row.get('review_reasons') or '[]')
        return {'rows':rows,'updated_by_job':updated}

    @router.post('/approve')
    def approve_scores(body: VSApproval):
        selected=[r for r in body.rows if r.include]
        if not selected:raise HTTPException(400,detail='Select at least one score.')
        ids=[r.id for r in selected]
        if len(set(ids))!=len(ids) or (body.auto_match and len(ids)!=1):
            raise HTTPException(400,detail='Select valid review rows.')
        if any(not any(c.isalpha() for c in row.name) for row in selected):
            raise HTTPException(400,detail='Each player name must contain letters.')
        with connect_db() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute('SELECT id,player_name,player_rank,normalized_name FROM `player list` ORDER BY id FOR UPDATE')
                    master=cur.fetchall()
                    exact=exact_player_index(master)
                    corrections=load_name_corrections(cur)
                    marks=','.join(['%s']*len(ids))
                    cur.execute(f'SELECT id,upload_date,COALESCE(ocr_name,player_name) AS original_name,review_reasons FROM vs_scan_rows WHERE job_id=%s AND id IN ({marks}) FOR UPDATE', (body.job_id,*ids))
                    staged=cur.fetchall()
                    if len(staged)!=len(ids):raise HTTPException(409,detail='Review changed. Refresh and retry.')
                    staged_by_id={source['id']:source for source in staged}
                    for source in staged:
                        monday=score_date_for_day(source['upload_date'],0)
                        saturday=score_date_for_day(source['upload_date'],5)
                        if not monday<=body.score_date<=min(saturday,source['upload_date']):
                            raise HTTPException(400,detail='Choose an elapsed day, Monday through Saturday, in this upload’s game week.')
                    updated=vs_scan_player_ids(cur,body.job_id,lock=True)
                    seen=set()
                    saved_ids=set()
                    for row in selected:
                        source=staged_by_id[row.id]
                        if number_needs_review(json.loads(source['review_reasons'] or '[]')) and (body.auto_match or not row.number_checked):
                            raise HTTPException(422,detail='Compare the score with the capture and mark it checked before saving.')
                        resolved=corrected_name(row.name,corrections,exact.keys())
                        player=exact.get(name_key(row.name)) if body.auto_match else match_master({'name':resolved},master)
                        if body.auto_match:
                            if not player:
                                raise HTTPException(409,detail='Choose an exact name from the Player List.')
                            if player['id'] in updated:
                                raise HTTPException(409,detail='That player was already updated by this scan.')
                        if player:saved_ids.add(player['id'])
                        name=player['player_name'] if player else row.name.strip()
                        key=name_key(name)
                        if key in seen:raise HTTPException(409,detail='Two reviewed rows match the same player. Keep one score.')
                        seen.add(key)
                        if name_key(source['original_name']) != name_key(row.name):
                            learn_name_correction(cur,source['original_name'],name)
                        upsert_vs_score(cur,body.score_date,player['id'] if player else None,name,row.points,body.job_id)
                    record_scan_players(cur,body.job_id,saved_ids,updated)
                    cur.execute(f'DELETE FROM vs_scan_rows WHERE id IN ({marks})',tuple(ids))
                    cur.execute("""UPDATE media_scan_jobs SET status='approved',message='VS points saved',
                        updated_at=UTC_TIMESTAMP(6) WHERE id=%s
                        AND NOT EXISTS(SELECT 1 FROM vs_scan_rows WHERE job_id=%s)""",(body.job_id,body.job_id))
                conn.commit()
            except Exception:
                conn.rollback();raise
        return {'approved':len(selected),'score_date':body.score_date.isoformat()}

    @router.post('/dismiss')
    def dismiss_scores(body: dict):
        ids=body.get('ids',[])
        if not ids or len(ids)>2000 or any(type(i) is not int or i<1 for i in ids):
            raise HTTPException(400,detail='Choose valid review rows.')
        with connect_db() as conn:
            with conn.cursor() as cur:
                marks=','.join(['%s']*len(ids))
                cur.execute(f'DELETE FROM vs_scan_rows WHERE id IN ({marks})',tuple(ids))
                cur.execute("""UPDATE media_scan_jobs j SET status='approved',message='VS results reviewed'
                    WHERE destination='vs' AND status='review'
                    AND NOT EXISTS(SELECT 1 FROM vs_scan_rows v WHERE v.job_id=j.id)""")
            conn.commit()
        return {'dismissed':len(ids)}

    app.include_router(router)

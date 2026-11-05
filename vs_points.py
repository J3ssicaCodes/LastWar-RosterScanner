"""Persistent VS daily scores and review, keyed by player and earned date."""
from datetime import date
import csv
import io
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from player_identity import match_master, name_key
from vs_points_ocr import score_date_for_day


class VSRow(BaseModel):
    id: int = Field(ge=1)
    name: str = Field(min_length=2,max_length=80)
    points: int = Field(ge=0,le=9007199254740991)
    include: bool = True


class VSApproval(BaseModel):
    job_id: str
    score_date: date
    rows: list[VSRow] = Field(min_length=1,max_length=200)


def stage_vs_result(connect_db, job_id, result):
    with connect_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT id,player_name,player_rank,normalized_name FROM `player list`')
                master=cur.fetchall()
                for row in result['rows']:
                    player=match_master(row,master)
                    name=player['player_name'] if player else row['name']
                    reasons=list(row['review_reasons'])
                    if not player:reasons.append('Match name to Player List or verify new player')
                    cur.execute('''INSERT INTO vs_scan_rows
                        (job_id,score_date,upload_date,weekday,player_name,points,review_reasons,evidence_base64)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
                        (job_id,result['score_date'],result['upload_date'],result['weekday'],name,
                         row['points'],json.dumps(reasons),row.get('evidence_base64')))
            conn.commit()
        except Exception:
            conn.rollback();raise
    return len(result['rows'])


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
                    KEY idx_vs_pending_job (job_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
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

    @router.get('/export.csv')
    def export_scores(score_date: date | None = None):
        result=list_scores(score_date)
        buf=io.StringIO();writer=csv.writer(buf);writer.writerow(['Date','Player Name','VS Points'])
        for row in result['rows']:writer.writerow([row['score_date'],row['name'],row['points']])
        return Response(buf.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="vs_points.csv"'})

    @router.get('/pending')
    def pending_scores():
        with connect_db() as conn:
            with conn.cursor() as cur:
                cur.execute('''SELECT id,job_id,score_date,upload_date,weekday,player_name AS name,
                    points,review_reasons,evidence_base64 FROM vs_scan_rows ORDER BY id LIMIT 2000''')
                rows=cur.fetchall()
        for row in rows:
            for key in ('score_date','upload_date'):row[key]=row[key].isoformat()
            row['review_reasons']=json.loads(row.get('review_reasons') or '[]')
        return {'rows':rows}

    @router.post('/approve')
    def approve_scores(body: VSApproval):
        selected=[r for r in body.rows if r.include]
        if not selected:raise HTTPException(400,detail='Select at least one score.')
        ids=[r.id for r in selected]
        if len(set(ids))!=len(ids):raise HTTPException(400,detail='Duplicate review rows.')
        if any(not any(c.isalpha() for c in row.name) for row in selected):
            raise HTTPException(400,detail='Each player name must contain letters.')
        with connect_db() as conn:
            try:
                with conn.cursor() as cur:
                    marks=','.join(['%s']*len(ids))
                    cur.execute(f'SELECT id,upload_date FROM vs_scan_rows WHERE job_id=%s AND id IN ({marks}) FOR UPDATE', (body.job_id,*ids))
                    staged=cur.fetchall()
                    if len(staged)!=len(ids):raise HTTPException(409,detail='Review changed. Refresh and retry.')
                    for source in staged:
                        monday=score_date_for_day(source['upload_date'],0)
                        saturday=score_date_for_day(source['upload_date'],5)
                        if not monday<=body.score_date<=min(saturday,source['upload_date']):
                            raise HTTPException(400,detail='Choose an elapsed day, Monday through Saturday, in this upload’s game week.')
                    cur.execute('SELECT id,player_name,player_rank,normalized_name FROM `player list`')
                    master=cur.fetchall();seen=set()
                    for row in selected:
                        player=match_master({'name':row.name},master)
                        name=player['player_name'] if player else row.name.strip()
                        key=name_key(name)
                        if key in seen:raise HTTPException(409,detail='Two reviewed rows match the same player. Keep one score.')
                        seen.add(key)
                        cur.execute('''INSERT INTO vs_points
                            (score_date,player_id,player_name,normalized_name,points,source_job_id)
                            VALUES (%s,%s,%s,%s,%s,%s)
                            ON DUPLICATE KEY UPDATE player_name=VALUES(player_name),
                            player_id=VALUES(player_id),normalized_name=VALUES(normalized_name),
                            points=VALUES(points),source_job_id=VALUES(source_job_id),updated_at=UTC_TIMESTAMP(6)''',
                            (body.score_date,player['id'] if player else None,name,key,row.points,body.job_id))
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

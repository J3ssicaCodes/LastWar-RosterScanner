"""Train rotation, award history, and reusable alliance announcements."""
import csv
import io
import logging
from datetime import date, datetime, timedelta
from threading import Event, Thread
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from pymysql.err import IntegrityError

from player_identity import corrected_name, load_name_corrections, name_key

DEFAULTS = {'alliance_name':'Rubies','cooldown_weeks':4,'spin_seconds':3}
EVENTS = ('Desert Storm','Canyon Storm','Zombie Siege','Marshalls','Sky Predator')


class TrainSettings(BaseModel):
    alliance_name: str = Field(min_length=1,max_length=80)
    cooldown_weeks: int = Field(ge=0,le=52)
    spin_seconds: int = Field(ge=1,le=30)

    @field_validator('alliance_name', mode='before')
    @classmethod
    def trim_name(cls,value):
        return value.strip() if isinstance(value,str) else value


class Restriction(BaseModel):
    disabled: bool


class Assignment(BaseModel):
    day: date
    conductor_id: int = Field(gt=0)
    vip_id: int = Field(gt=0)
    goal: Literal['Push','Save'] = 'Push'
    target: int = Field(default=7200000,ge=1,le=9007199254740991)
    wheel_vip: bool = False


class EmailDraft(BaseModel):
    body: str = Field(min_length=1,max_length=12000)


class EventName(BaseModel):
    name: str = Field(min_length=1,max_length=80)


def read_settings(cur):
    cur.execute('SELECT alliance_name,cooldown_weeks,spin_seconds FROM train_settings WHERE id=1')
    return cur.fetchone() or DEFAULTS.copy()


def nominations(players, points, last_awards, day, cooldown, goal, target):
    monday = day - timedelta(days=day.weekday())
    birthdays = {(monday+timedelta(days=i)).strftime('%m-%d'):i for i in range(28)}
    eligible = []
    for row in players:
        pid = row['id']
        birthday = row['birthday']
        offset = birthdays.get(birthday.strftime('%m-%d')) if birthday else None
        this_week = offset is not None and offset < 7
        last = last_awards.get(pid)
        cooling = bool(cooldown and last and last >= day-timedelta(weeks=cooldown))
        blocked = ('Train awards disabled' if row['disabled'] else
                   'Train cooldown' if cooling and not this_week else '')
        eligible.append({'id':pid,'name':row['name'],'rank':row['rank'],
                         'points':points.get(pid),'blocked':blocked,'reason':'Manual selection',
                         'birthday_offset':offset,'birthday':birthday.strftime('%m-%d') if birthday else None,
                         'last_award':last,'possibly_removed':bool(row['possibly_removed'])})
    active = [p for p in eligible if not p['blocked'] and not p['possibly_removed']]
    scored = [p for p in active if p['points'] is not None]
    alpha = lambda p:(p['name'].casefold(),p['id'])
    if goal == 'Push':
        goal_group = sorted(scored,key=lambda p:(-p['points'],*alpha(p)))[:10]
        for index,p in enumerate(goal_group):
            p['reason'] = 'Highest VS score' if index == 0 else f'Top 10 VS score (#{index+1})'
    else:
        exact = sorted((p for p in scored if p['points']==target),key=alpha)
        above = sorted((p for p in scored if p['points']>target),key=lambda p:(p['points']-target,*alpha(p)))[:10]
        for p in exact:
            p['reason'] = 'Hit the save goal exactly'
        for p in above:
            p['reason'] = f'{p["points"]-target:,} above the save goal'
        goal_group = exact + above
    birthday_group = sorted((p for p in active if p['birthday_offset'] is not None and p['birthday_offset']<7),
                            key=lambda p:(p['birthday_offset'],*alpha(p)))
    for p in birthday_group:
        p['reason'] = 'Birthday this week'
    ordered = list(dict.fromkeys(p['id'] for p in birthday_group+goal_group))
    return sorted(eligible,key=alpha), ordered


def load_nominations(cur, day, settings, goal, target):
    cur.execute("""SELECT p.id,p.player_name AS name,p.player_rank AS `rank`,p.birthday,p.possibly_removed,
                          r.player_id IS NOT NULL AS disabled
                   FROM `player list` p LEFT JOIN train_restrictions r ON r.player_id=p.id""")
    players = cur.fetchall()
    previous = day-timedelta(days=1)
    cur.execute("""SELECT COALESCE(linked.id,named.id) AS player_id,v.points
                   FROM vs_points v LEFT JOIN `player list` linked ON linked.id=v.player_id
                   LEFT JOIN `player list` named ON named.normalized_name=v.normalized_name
                   WHERE v.score_date=%s ORDER BY v.player_id IS NULL,v.updated_at DESC,v.id DESC""",(previous,))
    points = {}
    for row in cur.fetchall():
        points.setdefault(row['player_id'],row['points'])
    cur.execute("""SELECT conductor_id,vip_id,assignment_date FROM train_assignments
                   WHERE assignment_date<%s AND assignment_date>=%s ORDER BY assignment_date DESC""",
                (day,day-timedelta(weeks=settings['cooldown_weeks'])))
    last = {}
    for row in cur.fetchall():
        for key in ('conductor_id','vip_id'):
            last.setdefault(row[key],row['assignment_date'])
    return nominations(players,points,last,day,settings['cooldown_weeks'],goal,target)


def parse_train_csv(data):
    try:
        reader = csv.reader(io.StringIO(data.decode('utf-8-sig')),strict=True)
        header = next(reader,[])
        columns = [name_key(value) for value in header]
        required = ['date','conductorplayer','vipplayer']
        if any(columns.count(key)!=1 for key in required):
            raise ValueError('CSV headers must include Date, Conductor Player, and VIP Player.')
        indexes = [columns.index(key) for key in required]
        rows = []
        for line,fields in enumerate(reader,2):
            if not any(value.strip() for value in fields):
                continue
            if len(rows)>=1000:
                raise ValueError('Import at most 1,000 rows at a time.')
            try:
                raw,conductor,vip = [fields[i].strip() for i in indexes]
                if not conductor or not vip:
                    raise ValueError
                day = date.fromisoformat(raw) if '-' in raw else datetime.strptime(raw,'%m/%d/%Y').date()
                rows.append({'line':line,'day':day,'conductor':conductor,'vip':vip})
            except (IndexError,ValueError):
                rows.append({'line':line,'error':'Enter a valid date and both player names.'})
        if not rows:
            raise ValueError('The CSV has no assignment rows.')
        return rows
    except UnicodeDecodeError as exc:
        raise ValueError('Use a UTF-8 CSV file.') from exc
    except csv.Error as exc:
        raise ValueError('Could not read the CSV quoting.') from exc


def install_train_manager(app, connect_db):
    router = APIRouter(prefix='/api/train')
    stop = Event()

    def purge(cur):
        cur.execute('DELETE FROM train_emails WHERE created_at < UTC_TIMESTAMP() - INTERVAL 30 DAY')

    @app.on_event('startup')
    def train_schema():
        with connect_db() as conn, conn.cursor() as cur:
            for sql in (
                """CREATE TABLE IF NOT EXISTS train_settings (
                    id TINYINT PRIMARY KEY, alliance_name VARCHAR(80) NOT NULL,
                    cooldown_weeks TINYINT UNSIGNED NOT NULL, spin_seconds TINYINT UNSIGNED NOT NULL
                ) ENGINE=InnoDB COMMENT='alliance_tools:user_data'""",
                """CREATE TABLE IF NOT EXISTS train_restrictions (
                    player_id BIGINT UNSIGNED PRIMARY KEY
                ) ENGINE=InnoDB COMMENT='alliance_tools:user_data'""",
                """CREATE TABLE IF NOT EXISTS train_assignments (
                    assignment_date DATE PRIMARY KEY,
                    conductor_id BIGINT UNSIGNED NOT NULL, vip_id BIGINT UNSIGNED NOT NULL,
                    conductor_name VARCHAR(255) NOT NULL, vip_name VARCHAR(255) NOT NULL,
                    conductor_reason VARCHAR(140) NOT NULL, vip_reason VARCHAR(140) NOT NULL,
                    goal VARCHAR(10) NOT NULL, target BIGINT UNSIGNED NOT NULL,
                    conductor_points BIGINT UNSIGNED NULL, vip_points BIGINT UNSIGNED NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='alliance_tools:user_data'""",
                """CREATE TABLE IF NOT EXISTS train_event_names (
                    name_key VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin PRIMARY KEY,
                    name VARCHAR(80) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='alliance_tools:user_data'""",
                """CREATE TABLE IF NOT EXISTS train_emails (
                    assignment_date DATE PRIMARY KEY, body TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    KEY idx_train_emails_age(created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='alliance_tools:user_data'""",
            ):
                cur.execute(sql)
            purge(cur)
            conn.commit()

        def cleanup():
            while not stop.wait(3600):
                try:
                    with connect_db() as conn, conn.cursor() as cur:
                        purge(cur)
                        conn.commit()
                except Exception:
                    logging.exception('Could not remove expired train emails')

        stop.clear()
        Thread(target=cleanup,daemon=True,name='train-email-retention').start()

    @app.on_event('shutdown')
    def stop_cleanup():
        stop.set()

    @router.get('/settings')
    def settings():
        with connect_db() as conn, conn.cursor() as cur:
            return read_settings(cur)

    @router.put('/settings')
    def save_settings(body:TrainSettings):
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO train_settings (id,alliance_name,cooldown_weeks,spin_seconds) VALUES (1,%s,%s,%s)
                           ON DUPLICATE KEY UPDATE alliance_name=VALUES(alliance_name),
                           cooldown_weeks=VALUES(cooldown_weeks),spin_seconds=VALUES(spin_seconds)""",
                        (body.alliance_name,body.cooldown_weeks,body.spin_seconds))
            conn.commit()
        return body

    @router.get('/restriction/{player_id}')
    def restriction(player_id:int):
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('SELECT player_id FROM train_restrictions WHERE player_id=%s',(player_id,))
            return {'disabled':bool(cur.fetchone())}

    @router.put('/restriction/{player_id}')
    def set_restriction(player_id:int,body:Restriction):
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('SELECT id FROM `player list` WHERE id=%s FOR UPDATE',(player_id,))
            if not cur.fetchone():
                raise HTTPException(404,detail='Player not found.')
            sql = ('INSERT IGNORE INTO train_restrictions (player_id) VALUES (%s)' if body.disabled
                   else 'DELETE FROM train_restrictions WHERE player_id=%s')
            cur.execute(sql,(player_id,))
            conn.commit()
        return body

    @router.get('/board')
    def board(day:date,goal:Literal['Push','Save']='Push',target:int=7200000):
        with connect_db() as conn, conn.cursor() as cur:
            settings = read_settings(cur)
            players, ordered = load_nominations(cur,day,settings,goal,target)
            cur.execute('SELECT * FROM train_assignments WHERE assignment_date=%s',(day,))
            assignment = cur.fetchone()
            cur.execute('SELECT name FROM train_event_names ORDER BY name')
            names = list(dict.fromkeys([*EVENTS,*(row['name'] for row in cur.fetchall())]))
        return {'settings':settings,'players':players,'nominees':ordered,
                'assignment':assignment,'event_names':names,'score_date':day-timedelta(days=1)}

    def save_assignment(body, historical=False):
        if body.conductor_id==body.vip_id:
            raise HTTPException(422,detail='Choose different players for Conductor and VIP.')
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('SELECT id,player_name AS name FROM `player list` WHERE id IN (%s,%s) ORDER BY id FOR UPDATE',
                        tuple(sorted((body.conductor_id,body.vip_id))))
            locked = cur.fetchall()
            if len(locked)!=2:
                raise HTTPException(422,detail='Both players must be on the Player List.')
            if historical:
                players = [dict(row,reason='Historical import',points=None) for row in locked]
            else:
                players,_ = load_nominations(cur,body.day,read_settings(cur),body.goal,body.target)
            by_id = {p['id']:p for p in players}
            conductor,vip = by_id[body.conductor_id],by_id[body.vip_id]
            for player in (conductor,vip):
                if player.get('blocked'):
                    raise HTTPException(409,detail=f"{player['name']} is ineligible. {player['blocked']}.")
            vip_reason = vip['reason']
            if body.wheel_vip and not historical:
                vip_reason = ('Wheel of Fortune' if vip_reason=='Manual selection'
                              else vip_reason+' and selected by the Wheel of Fortune')
            try:
                cur.execute("""INSERT INTO train_assignments
                    (assignment_date,conductor_id,vip_id,conductor_name,vip_name,conductor_reason,vip_reason,
                     goal,target,conductor_points,vip_points)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (body.day,conductor['id'],vip['id'],conductor['name'],vip['name'],
                     conductor['reason'],vip_reason,body.goal,body.target,conductor['points'],vip['points']))
                conn.commit()
            except IntegrityError as exc:
                conn.rollback()
                raise HTTPException(409,detail='A train assignment is already saved for that date.') from exc
        return {'day':body.day}

    @router.post('/assignments')
    def assign(body:Assignment):
        return save_assignment(body)

    @router.post('/history')
    def import_assignment(body:Assignment):
        return save_assignment(body,historical=True)

    @router.get('/history')
    def history(before:date|None=None):
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute("""SELECT * FROM train_assignments WHERE (%s IS NULL OR assignment_date<%s)
                           ORDER BY assignment_date DESC LIMIT 51""",(before,before))
            rows = cur.fetchall()
        return {'rows':rows[:50],'more':len(rows)>50}

    @router.post('/import-preview')
    def preview(file:UploadFile=File(...)):
        data=file.file.read(512001)
        if len(data)>512000:
            raise HTTPException(413,detail='Choose a CSV smaller than 500 KB.')
        try:
            rows=parse_train_csv(data)
        except ValueError as exc:
            raise HTTPException(422,detail=str(exc)) from exc
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('SELECT id,player_name AS name,normalized_name FROM `player list` ORDER BY player_name')
            players=cur.fetchall()
            aliases=load_name_corrections(cur)
        known={p['normalized_name']:p['id'] for p in players}
        for row in rows:
            if 'error' not in row:
                for role in ('conductor','vip'):
                    row[role+'_id']=known.get(name_key(corrected_name(row[role],aliases,known)))
        return {'rows':rows,'players':players}

    @router.post('/event-names')
    def event_name(body:EventName):
        name=body.name.strip()
        if not name or name.casefold()=='other':
            raise HTTPException(422,detail='Enter the event name.')
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('INSERT IGNORE INTO train_event_names (name_key,name) VALUES (%s,%s)',(name_key(name),name))
            conn.commit()
        return {'name':name}

    @router.get('/emails')
    def emails():
        with connect_db() as conn, conn.cursor() as cur:
            purge(cur)
            cur.execute('SELECT assignment_date,body FROM train_emails ORDER BY assignment_date DESC')
            rows=cur.fetchall()
            conn.commit()
        return {'rows':rows}

    @router.put('/emails/{day}')
    def save_email(day:date,body:EmailDraft):
        text=body.body.strip()
        if not text:
            raise HTTPException(422,detail='Enter email text before saving.')
        with connect_db() as conn, conn.cursor() as cur:
            cur.execute('SELECT assignment_date FROM train_assignments WHERE assignment_date=%s FOR UPDATE',(day,))
            if not cur.fetchone():
                raise HTTPException(404,detail='Save a train assignment for this date first.')
            purge(cur)
            cur.execute("""INSERT INTO train_emails (assignment_date,body,created_at) VALUES (%s,%s,UTC_TIMESTAMP())
                           ON DUPLICATE KEY UPDATE body=VALUES(body)""",(day,text))
            conn.commit()
        return {'day':day,'body':text}

    app.include_router(router)

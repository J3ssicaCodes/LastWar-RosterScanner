"""Behavioral tests of canonical player merges, month/day birthdays and daily upserts."""
from datetime import date
import re
import sqlite3
import unittest
from unittest.mock import Mock, patch
from fastapi import HTTPException
import app
from vs_points import install_vs_points, VSApproval, stage_vs_result
from vs_points_ocr import score_date_for_day
from player_identity import match_master


class SQLDatabase:
    """Exercise production transactions on SQLite with MySQL syntax translated."""
    def __init__(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.create_function('IF',3,lambda condition,a,b:a if condition else b)
        self.db.executescript('''CREATE TABLE `player list` (
            id INTEGER PRIMARY KEY AUTOINCREMENT,player_name TEXT,normalized_name TEXT UNIQUE,
            player_rank TEXT,power_millions REAL,power INTEGER,kills INTEGER,donations INTEGER,
            hq_level INTEGER,avatar_image BLOB,avatar_mime TEXT,avatar_updated_at TEXT,
            birthday TEXT,is_new INTEGER,possibly_removed INTEGER,first_seen_at TEXT,
            last_seen_at TEXT,updated_at TEXT,last_import_id TEXT);
            CREATE TABLE player_list_imports (id TEXT,is_full_capture INTEGER,scanned_count INTEGER,
            created_count INTEGER,updated_count INTEGER,possible_removed_count INTEGER);
            CREATE TABLE vs_points (id INTEGER PRIMARY KEY,score_date TEXT,player_id INTEGER,
            player_name TEXT,normalized_name TEXT,points INTEGER,source_job_id TEXT,updated_at TEXT,
            UNIQUE(score_date,normalized_name),UNIQUE(score_date,player_id));
            CREATE TABLE vs_scan_rows (id INTEGER PRIMARY KEY,job_id TEXT,upload_date TEXT,
            score_date TEXT,weekday TEXT,player_name TEXT,points INTEGER,review_reasons TEXT,evidence_base64 TEXT);
            CREATE TABLE media_scan_jobs (id TEXT PRIMARY KEY,status TEXT,message TEXT,updated_at TEXT);
        ''')
    def __call__(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def cursor(self):return Cursor(self.db)
    def commit(self):self.db.commit()
    def rollback(self):self.db.rollback()


class Cursor:
    def __init__(self,db):self.cursor_=db.cursor()
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,args=()):
        sql=sql.replace('%s','?').replace(' FOR UPDATE','').replace('UTC_TIMESTAMP(6)',"datetime('now')")
        sql=sql.replace('ON DUPLICATE KEY UPDATE','ON CONFLICT DO UPDATE SET')
        sql=re.sub(r'VALUES\((\w+)\)',r'excluded.\1',sql)
        self.cursor_.execute(sql,tuple(args))
    def fetchone(self):
        row=self.cursor_.fetchone()
        if row is None:return None
        row=dict(row)
        if 'upload_date' in row:row['upload_date']=date.fromisoformat(row['upload_date'])
        return row
    def fetchall(self):
        rows=[]
        for row in self.cursor_.fetchall():
            row=dict(row)
            if 'upload_date' in row:row['upload_date']=date.fromisoformat(row['upload_date'])
            if row.get('birthday'):row['birthday']=date.fromisoformat(row['birthday'])
            rows.append(row)
        return rows
    @property
    def rowcount(self):return self.cursor_.rowcount


class PlayerMergeTests(unittest.TestCase):
    def setUp(self):
        self.db=SQLDatabase()
        p=patch.object(app,'db_connect',self.db);p.start();self.addCleanup(p.stop)
    def test_power_then_kills_preserves_identity_stats_and_birthday(self):
        app.import_players(app.ImportData(players=[app.Member(name='Lady Jess',rank='R4',power=123456789)]))
        self.db.db.execute("UPDATE `player list` SET birthday='2000-02-29',hq_level=35")
        original=self.db.db.execute('SELECT id FROM `player list`').fetchone()[0]
        app.import_players(app.ImportData(players=[app.Member(name='LadyJess',rank='R4',kills=987654)]))
        rows=self.db.db.execute('SELECT * FROM `player list`').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['id'],rows[0]['power'],rows[0]['kills'],rows[0]['birthday'],rows[0]['hq_level']),
                         (original,123456789,987654,'2000-02-29',35))
    def test_same_player_two_categories_in_one_approval(self):
        app.import_players(app.ImportData(players=[app.Member(name='Jessica',rank='R4',power=123),
                                                 app.Member(name='Jessica',rank='R3',kills=456)]))
        row=self.db.db.execute('SELECT * FROM `player list`').fetchone()
        self.assertEqual((row['power'],row['kills']),(123,456))
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM `player list`').fetchone()[0],1)
    def test_punctuation_variant_updates_canonical_player(self):
        app.import_players(app.ImportData(players=[app.Member(name='LadyJess',rank='R4',power=100)]))
        app.import_players(app.ImportData(players=[app.Member(name='Lady Jess!',rank='R4',kills=200)]))
        rows=self.db.db.execute('SELECT * FROM `player list`').fetchall()
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['player_name'],'LadyJess')
        self.assertEqual(rows[0]['kills'],200)
    def test_ambiguous_names_are_not_automatically_matched(self):
        self.assertIsNone(match_master({'name':'A B C'},[{'name':'A-BC'},{'name':'AB.C'}]))
    def test_birthday_save_repeat_and_clear_without_changing_stats(self):
        app.import_players(app.ImportData(players=[app.Member(name='Jess',rank='R4',power=123)]))
        player_id=self.db.db.execute('SELECT id FROM `player list`').fetchone()[0]
        for _ in range(2):
            self.assertEqual(app.update_birthday(player_id,app.BirthdayUpdate(birthday='02-29'))['birthday'],'02-29')
        self.assertEqual(app.list_players()['players'][0]['birthday'],'02-29')
        app.update_birthday(player_id,app.BirthdayUpdate(birthday=None))
        row=self.db.db.execute('SELECT birthday,power FROM `player list`').fetchone()
        self.assertEqual(tuple(row),(None,123))

    def test_birthday_has_no_year_and_supports_leap_day(self):
        self.assertEqual(app.BirthdayUpdate(birthday='02-29').birthday,'02-29')
        self.assertEqual(app.BirthdayUpdate(birthday='1999-05-07').birthday,'05-07')
        with self.assertRaises(ValueError):app.BirthdayUpdate(birthday='02-30')


class VSDateAndPersistenceTests(unittest.TestCase):
    def test_selected_day_uses_current_week_ending_sunday(self):
        self.assertEqual(score_date_for_day(date(2026,11,5),2),date(2026,11,4))
        self.assertEqual(score_date_for_day(date(2026,11,8),5),date(2026,11,7))
        self.assertEqual(score_date_for_day(date(2026,11,7),5),date(2026,11,7))
        self.assertEqual(score_date_for_day(date(2026,11,9),0),date(2026,11,9))
        self.assertEqual(score_date_for_day(date(2027,1,1),0),date(2026,12,28))
        with self.assertRaises(ValueError):score_date_for_day(date(2026,11,8),6)
    def test_scanned_scores_stage_canonical_name_and_local_upload_date(self):
        db=SQLDatabase()
        db.db.execute("INSERT INTO `player list` (player_name,normalized_name,player_rank) VALUES ('Lady Jess','ladyjess','R4')")
        count=stage_vs_result(db,'job',{'score_date':'2026-11-04','upload_date':'2026-11-05',
            'weekday':'Wed','rows':[{'name':'LadyJess','points':13624900,'review_reasons':[]}]})
        self.assertEqual(count,1)
        row=db.db.execute('SELECT player_name,score_date,upload_date,points FROM vs_scan_rows').fetchone()
        self.assertEqual(tuple(row),('Lady Jess','2026-11-04','2026-11-05',13624900))

    def test_rescan_updates_one_player_date_and_keeps_other_dates(self):
        db=SQLDatabase();web=Mock();install_vs_points(web,db)
        router=web.include_router.call_args.args[0]
        approve=next(r.endpoint for r in router.routes if r.path=='/api/vs-points/approve')
        for idx,(when,points) in enumerate([('2026-11-04',100),('2026-11-04',200),('2026-11-04',50),('2026-11-03',300)],1):
            db.db.execute('INSERT INTO vs_scan_rows (id,job_id,upload_date) VALUES (?,?,?)',(idx,'job','2026-11-05'))
            approve(VSApproval(job_id='job',score_date=when,rows=[{'id':idx,'name':'Lady Jess','points':points}]))
        rows=db.db.execute('SELECT score_date,points FROM vs_points ORDER BY score_date').fetchall()
        self.assertEqual([tuple(r) for r in rows],[('2026-11-03',300),('2026-11-04',50)])
        db.db.execute('INSERT INTO vs_scan_rows (id,job_id,upload_date) VALUES (5,?,?)',('job','2026-11-05'))
        with self.assertRaises(HTTPException):
            approve(VSApproval(job_id='job',score_date='2026-10-28',rows=[{'id':5,'name':'Lady Jess','points':10}]))
        with self.assertRaises(HTTPException):
            approve(VSApproval(job_id='job',score_date='2026-11-06',rows=[{'id':5,'name':'Lady Jess','points':10}]))


if __name__=='__main__':unittest.main()

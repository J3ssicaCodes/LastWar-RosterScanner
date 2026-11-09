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
            birthday TEXT,is_new INTEGER,possibly_removed INTEGER,archived INTEGER NOT NULL DEFAULT 0,
            has_t11 INTEGER NOT NULL DEFAULT 0,away_start TEXT,away_end TEXT,player_notes TEXT,first_seen_at TEXT,
            last_seen_at TEXT,updated_at TEXT,last_import_id TEXT);
            CREATE TABLE player_list_imports (id TEXT,is_full_capture INTEGER,scanned_count INTEGER,
            created_count INTEGER,updated_count INTEGER,possible_removed_count INTEGER);
            CREATE TABLE player_name_dictionary (ocr_key TEXT PRIMARY KEY,player_name TEXT);
            CREATE TABLE player_stat_history (player_id INTEGER,import_id TEXT,recorded_at TEXT,
            power INTEGER,power_millions REAL,kills INTEGER,donations INTEGER,
            power_upload_date TEXT,power_millions_upload_date TEXT,kills_upload_date TEXT,donations_upload_date TEXT);
            CREATE TABLE vs_points (id INTEGER PRIMARY KEY,score_date TEXT,player_id INTEGER,
            player_name TEXT,normalized_name TEXT,points INTEGER,source_job_id TEXT,updated_at TEXT,
            UNIQUE(score_date,normalized_name),UNIQUE(score_date,player_id));
            CREATE TABLE vs_scan_rows (id INTEGER PRIMARY KEY,job_id TEXT,upload_date TEXT,
            score_date TEXT,weekday TEXT,player_name TEXT,points INTEGER,review_reasons TEXT,evidence_base64 TEXT,ocr_name TEXT);
            CREATE TABLE media_scan_jobs (id TEXT PRIMARY KEY,status TEXT,message TEXT,updated_at TEXT,updated_player_ids TEXT);
            CREATE TABLE train_assignments (assignment_date TEXT,conductor_id INTEGER,vip_id INTEGER);
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
        if row.get('score_date'):row['score_date']=date.fromisoformat(row['score_date'])
        return row
    def fetchall(self):
        rows=[]
        for row in self.cursor_.fetchall():
            row=dict(row)
            if 'upload_date' in row:row['upload_date']=date.fromisoformat(row['upload_date'])
            if row.get('score_date'):row['score_date']=date.fromisoformat(row['score_date'])
            if row.get('birthday'):row['birthday']=date.fromisoformat(row['birthday'])
            if row.get('last_date'):row['last_date']=date.fromisoformat(row['last_date'])
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

    def test_last_train_dates_are_separate_and_exclude_future(self):
        app.import_players(app.ImportData(players=[app.Member(name='Jess',rank='R4'),
                                                  app.Member(name='Fur',rank='R3')]))
        ids={row['player_name']:row['id'] for row in self.db.db.execute('SELECT id,player_name FROM `player list`')}
        self.db.db.executemany('INSERT INTO train_assignments VALUES (?,?,?)',[
            ('2020-01-01',ids['Jess'],ids['Fur']),('2020-02-02',ids['Fur'],ids['Jess']),
            ('2020-03-03',ids['Jess'],None),('2999-01-01',ids['Jess'],ids['Jess'])])
        rows={p['name']:p for p in app.list_players()['players']}
        self.assertEqual((rows['Jess']['last_train_conductor'],rows['Jess']['last_train_vip']),('2020-03-03','2020-02-02'))
        self.assertEqual((rows['Fur']['last_train_conductor'],rows['Fur']['last_train_vip']),('2020-02-02','2020-01-01'))

    def test_power_and_kills_history_keeps_changes(self):
        for power,kills in [(100,None),(None,20),(125,None),(125,None)]:
            app.import_players(app.ImportData(players=[app.Member(name='Jess',rank='R4',power=power,kills=kills)]))
        snapshots=self.db.db.execute('SELECT power,kills FROM player_stat_history ORDER BY rowid').fetchall()
        self.assertEqual([tuple(row) for row in snapshots],[(100,None),(100,20),(125,20)])

    def test_chart_history_uses_each_upload_date_and_sunday_vs_totals(self):
        app.import_players(app.ImportData(players=[
            app.Member(name='Jess',rank='R4',power=123,upload_date=date(2026,11,2)),
            app.Member(name='Jess',rank='R4',kills=45,donations=500,upload_date=date(2026,11,3)),
        ]))
        app.import_players(app.ImportData(players=[
            app.Member(name='Jess',rank='R4',donations=750,upload_date=date(2026,11,5)),
        ]))
        player_id=self.db.db.execute('SELECT id FROM `player list`').fetchone()[0]
        self.db.db.executemany('INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)',
                               [(day,player_id,'Jess','jess',points) for day,points in
                                [('2026-11-02',10),('2026-11-07',20),('2026-11-09',50)]])
        history=app.player_history(player_id)
        self.assertEqual(history['power'],[{'date':'2026-11-02','value':123}])
        self.assertEqual(history['kills'],[{'date':'2026-11-03','value':45}])
        self.assertEqual(history['donations'],[{'date':'2026-11-03','value':500},{'date':'2026-11-05','value':750}])
        self.assertEqual(history['vs'],[{'date':'2026-11-08','value':30},{'date':'2026-11-15','value':50}])

    def test_birthday_has_no_year_and_supports_leap_day(self):
        self.assertEqual(app.BirthdayUpdate(birthday='02-29').birthday,'02-29')
        self.assertEqual(app.BirthdayUpdate(birthday='1999-05-07').birthday,'05-07')
        with self.assertRaises(ValueError):app.BirthdayUpdate(birthday='02-30')


class VSDateAndPersistenceTests(unittest.TestCase):
    def test_vs_video_keeps_rows_when_the_weekday_is_unreadable(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        import numpy as np
        from vs_points_ocr import scan_vs_upload
        row={'name':'Lady Jess','value':'123','name_conf':90,'value_conf':90,'page':1}
        with TemporaryDirectory() as temp, \
                patch('vs_points_ocr.detect'), patch('vs_points_ocr.include_readable_opening',return_value=()), \
                patch('vs_points_ocr.extract',return_value={'kept':[{'image':'frame.png','frame':0}]}), \
                patch('vs_points_ocr.cv2.imread',return_value=np.zeros((100,60,3),np.uint8)), \
                patch('vs_points_ocr.selected_weekday',side_effect=ValueError('Unreadable weekday')), \
                patch('vs_points_ocr.estimate_row_height',return_value=10), \
                patch('vs_points_ocr.read_page',return_value=[row]), patch('vs_points_ocr.best_start',return_value=1), \
                patch('vs_points_ocr.recheck_neighbors',return_value={}):
            result=scan_vs_upload([Path(temp)/'clip.mp4'],Mock(),date(2026,11,6),master_keys={'ladyjess'},recheck_keys=set())
        self.assertIsNone(result['score_date'])
        self.assertEqual(result['rows'][0]['points'],123)

    def test_adjacent_frames_require_agreement_for_each_field(self):
        from unittest.mock import patch
        from vs_points_ocr import recheck_neighbors
        original={'internal_position':4,'page':1,'name':'Ladv Jess','value':'123',
                  'name_conf':85,'value_conf':85}
        frames={37:{'name':'Lady Jess','value':'124'}, 39:{'name':'Lady Jess','value':'124'},
                41:{'name':'Lady Jess','value':'124'}, 43:{'name':'Lady Jess','value':'123'}}
        capture=Mock()
        cursor=0

        def seek(_, frame):
            nonlocal cursor
            cursor=frame
            return True

        def grab():
            nonlocal cursor
            cursor+=1
            return True

        def read():
            frame=frames[cursor]
            grab()
            return True,frame

        capture.set.side_effect=seek
        capture.grab.side_effect=grab
        capture.read.side_effect=read
        def read_frame(image,anchors):
            return [{**image,'matched_position':4,'name_conf':90,'value_conf':90}]
        with patch('vs_points_ocr.cv2.VideoCapture',return_value=capture):
            confirmed=recheck_neighbors({4:original},{1:('sample.mp4',40)},{4:['Check']},
                                       str,Mock(),read_frame)
        self.assertEqual([call.args[1] for call in capture.set.call_args_list],[39,37,43])
        capture.grab.assert_called_once_with()
        self.assertEqual(confirmed[4],{'name','value'})
        self.assertEqual(original['name'],'Lady Jess')
        self.assertEqual(original['value'],'124')
        self.assertNotIn('neighbor_conflicts',original)

    def test_video_accepts_three_matching_reads_even_with_low_confidence(self):
        from vs_points_ocr import recheck_neighbors
        for neighboring, expected in [(['100','100'],True), (['100','','100',''],True),
                                      (['','','',''],False), (['100','101','102',''],False)]:
            with self.subTest(neighboring=neighboring):
                row={'internal_position':1,'page':1,'name':'Lady Jess','value':'100',
                     'name_conf':90,'value_conf':10}
                capture=Mock()
                capture.read.side_effect=[(True,value) for value in neighboring]
                def read_frame(value, anchors):
                    return [{'matched_position':1,'value':value,'value_conf':10}]
                with patch('vs_points_ocr.cv2.VideoCapture',return_value=capture):
                    confirmed=recheck_neighbors({1:row},{1:('sample.mp4',40)},{1:[]},str,Mock(),read_frame)
                self.assertEqual('value' in confirmed[1],expected)
                self.assertEqual(bool(row.get('neighbor_conflicts')),not expected)

    def test_score_reader_rejects_truncated_numbers(self):
        import numpy as np
        from vs_points_ocr import read_score

        crop = np.full((69,285,3), 255, dtype=np.uint8)
        line = crop[:,:,0]
        with patch('vs_points_ocr.score_line', return_value=(8,line,line)), \
             patch('vs_points_ocr.text_with_conf', side_effect=[
                 ('35',95), ('25,160,590',0), ('25,160,590',0), ('',0),
             ]):
            self.assertEqual(read_score(crop), ('25160590',65))

    def test_score_reader_keeps_conflicting_full_readings_uncertain(self):
        import numpy as np
        from vs_points_ocr import read_score

        crop = np.full((69,285,3), 255, dtype=np.uint8)
        line = crop[:,:,0]
        with patch('vs_points_ocr.score_line', return_value=(8,line,line)), \
             patch('vs_points_ocr.text_with_conf', side_effect=[
                 ('35',95), ('25,160,590',93), ('25,760,590',93), ('',0),
             ]):
            self.assertLess(read_score(crop)[1],55)

    def test_portrait_match_requires_text_and_a_unique_image(self):
        import base64
        import cv2
        import numpy as np
        from vs_points_ocr import roster_portrait_match

        image = np.random.default_rng(1).integers(0,256,(96,96),dtype=np.uint8)
        ok, data = cv2.imencode('.png', image)
        self.assertTrue(ok)
        row = {'avatar_base64':base64.b64encode(data).decode()}
        self.assertEqual(roster_portrait_match(row,'Lady Jes5',{'ladyjess':image}), 'ladyjess')
        self.assertIsNone(roster_portrait_match(row,'Different name',{'ladyjess':image}))
        self.assertIsNone(roster_portrait_match(row,'Lady Jes5',{'ladyjess':image,'differentname':image}))

    def test_sort_warning_stays_on_the_unverified_score(self):
        from vs_points_ocr import review_flags
        rows = {
            1: {'name':'First Player','name_conf':90,'value':'100','value_conf':90},
            2: {'name':'Second Player','name_conf':90,'value':'200','value_conf':90},
        }
        reasons = review_flags(rows, {}, {}, str, None, {1})
        self.assertEqual(reasons[1], [])
        self.assertIn('Points exceed the row above', reasons[2])

    def test_weekly_scores_keep_dates_and_learn_reviewed_names(self):
        from player_identity import corrected_name, load_name_corrections
        db=SQLDatabase(); web=Mock(); install_vs_points(web,db)
        routes={r.path:r.endpoint for r in web.include_router.call_args.args[0].routes}
        for idx, day in enumerate(('2026-10-31','2026-11-02','2026-11-04'),1):
            db.db.execute('INSERT INTO vs_scan_rows (id,job_id,upload_date,player_name) VALUES (?,?,?,?)',
                          (idx,'job',day,'Ladv Jess'))
            routes['/api/vs-points/approve'](VSApproval(job_id='job',score_date=day,
                rows=[{'id':idx,'name':'Lady Jess','points':idx}]))
        week=routes['/api/vs-points/weekly'](date(2026,11,5))
        self.assertEqual(week['days'],[f'2026-11-0{day}' for day in range(2,8)])
        self.assertEqual(week['weeks'],['2026-11-02','2026-10-26'])
        self.assertEqual({(r['score_date'],r['points']) for r in week['rows']},
                         {('2026-11-02',2),('2026-11-04',3)})
        self.assertEqual(corrected_name('Ladv Jess',load_name_corrections(db.cursor()),set()),'Lady Jess')

    def test_selected_day_uses_current_week_ending_sunday(self):
        self.assertEqual(score_date_for_day(date(2026,11,5),2),date(2026,11,4))
        self.assertEqual(score_date_for_day(date(2026,11,8),5),date(2026,11,7))
        self.assertEqual(score_date_for_day(date(2026,11,7),5),date(2026,11,7))
        self.assertEqual(score_date_for_day(date(2026,11,9),0),date(2026,11,9))
        self.assertEqual(score_date_for_day(date(2027,1,1),0),date(2026,12,28))
        with self.assertRaises(ValueError):score_date_for_day(date(2026,11,8),6)
    def test_scanned_scores_auto_save_exact_matches_and_dictionary_aliases(self):
        db=SQLDatabase()
        db.db.execute("INSERT INTO `player list` (player_name,normalized_name,player_rank) VALUES ('Lady Jess','ladyjess','R4'),('Other Player','otherplayer','R3')")
        db.db.execute("INSERT INTO player_name_dictionary VALUES ('ladvjess','Lady Jess')")
        result=stage_vs_result(db,'job',{'score_date':'2026-11-04','upload_date':'2026-11-05',
            'weekday':'Wed','rows':[
                {'name':'Ladv Jess','points':13624900,'review_reasons':['Check player name']},
                {'name':'OtherPlayer','points':0,'review_reasons':[]},
                {'name':'Someone New','points':100,'review_reasons':[]},
                {'name':'Lady Jess','points':None,'review_reasons':[]},
            ]})
        self.assertEqual(result,{'saved':2,'pending':2,'skipped':0})
        rows=db.db.execute('SELECT player_name,score_date,points FROM vs_points ORDER BY id')
        self.assertEqual([tuple(r) for r in rows],[('Lady Jess','2026-11-04',13624900),('Other Player','2026-11-04',0)])
        rows=db.db.execute('SELECT player_name,upload_date,points FROM vs_scan_rows ORDER BY id')
        self.assertEqual([tuple(r) for r in rows],[('Someone New','2026-11-05',100),('Lady Jess','2026-11-05',None)])

    def test_conflicting_exact_scores_stay_in_review(self):
        db=SQLDatabase()
        db.db.execute("INSERT INTO `player list` (player_name,normalized_name,player_rank) VALUES ('Lady Jess','ladyjess','R4')")
        result=stage_vs_result(db,'job',{'score_date':'2026-11-04','upload_date':'2026-11-05',
            'weekday':'Wed','rows':[
                {'name':'Lady Jess','points':100,'review_reasons':[]},
                {'name':'Lady Jess','points':200,'review_reasons':[]},
            ]})
        self.assertEqual(result,{'saved':0,'pending':2,'skipped':0})
        self.assertEqual(db.db.execute('SELECT count(*) FROM vs_points').fetchone()[0],0)

    def test_flagged_number_requires_explicit_verification(self):
        db=SQLDatabase();web=Mock();install_vs_points(web,db)
        router=web.include_router.call_args.args[0]
        approve=next(r.endpoint for r in router.routes if r.path=='/api/vs-points/approve')
        db.db.execute("INSERT INTO media_scan_jobs (id,status) VALUES ('job','scanning')")
        db.db.execute("INSERT INTO `player list` (player_name,normalized_name,player_rank) VALUES ('Lady Jess','ladyjess','R4')")
        result=stage_vs_result(db,'job',{'score_date':'2026-11-04','upload_date':'2026-11-05',
            'weekday':'Wed','rows':[{'name':'Lady Jess','points':123,
                'review_reasons':['Number needs verification: frame readings were 123 / 124 / 124.']}]})
        self.assertEqual(result,{'saved':0,'pending':1,'skipped':0})
        row={'id':1,'name':'Lady Jess','points':124}
        for auto in (True,False):
            with self.assertRaises(HTTPException) as failure:
                approve(VSApproval(job_id='job',score_date=date(2026,11,4),auto_match=auto,rows=[row]))
            self.assertEqual(failure.exception.status_code,422)
        approve(VSApproval(job_id='job',score_date=date(2026,11,4),rows=[{**row,'number_checked':True}]))
        self.assertEqual(db.db.execute('SELECT points FROM vs_points').fetchone()[0],124)

    def test_blur_save_records_player_and_blocks_repeat_for_same_scan(self):
        db=SQLDatabase();web=Mock();install_vs_points(web,db)
        router=web.include_router.call_args.args[0]
        approve=next(r.endpoint for r in router.routes if r.path=='/api/vs-points/approve')
        pending=next(r.endpoint for r in router.routes if r.path=='/api/vs-points/pending')
        db.db.execute("INSERT INTO media_scan_jobs (id,status) VALUES ('job','review')")
        db.db.execute("INSERT INTO `player list` (player_name,normalized_name,player_rank) VALUES ('Lady Jess','ladyjess','R4')")
        for idx in (1,2):
            db.db.execute("INSERT INTO vs_scan_rows (id,job_id,upload_date,score_date,player_name,ocr_name) VALUES (?,'job','2026-11-05','2026-11-04','Lady Jesso','Ladv Jess')",(idx,))
        first=VSApproval(job_id='job',score_date=date(2026,11,4),auto_match=True,
                         rows=[{'id':1,'name':'Lady Jess','points':100}])
        self.assertEqual(approve(first)['approved'],1)
        self.assertEqual(pending()['updated_by_job'],{'job':[1]})
        second=VSApproval(job_id='job',score_date=date(2026,11,4),auto_match=True,
                          rows=[{'id':2,'name':'Lady Jess','points':999}])
        with self.assertRaises(HTTPException) as failure:
            approve(second)
        self.assertEqual(failure.exception.status_code,409)
        self.assertEqual(db.db.execute('SELECT points FROM vs_points').fetchone()[0],100)
        self.assertEqual(db.db.execute('SELECT id FROM vs_scan_rows').fetchone()[0],2)
        self.assertEqual(db.db.execute("SELECT player_name FROM player_name_dictionary WHERE ocr_key='ladvjess'").fetchone()[0],'Lady Jess')

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

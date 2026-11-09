from datetime import date
import json
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException

import app
from player_merge import PlayerMerge, merge_player_records
from test_player_merge_vs import Cursor, SQLDatabase
from vs_points import VSClaim, claim_vs_scores, install_vs_points, stage_vs_result


class PerformanceDB(SQLDatabase):
    def __init__(self):
        super().__init__()
        self.db.create_function('JSON_CONTAINS',2,lambda values,item: int(json.loads(item) in json.loads(values or '[]')))
        self.db.create_function('LEAST',2,lambda a,b: min(a,b) if a and b else a or b)
        self.db.create_function('GREATEST',2,lambda a,b: max(a,b) if a and b else a or b)
        self.db.executescript('''CREATE TABLE `roster upload` (player_name TEXT,ocr_name TEXT);
            CREATE TABLE train_assignments (conductor_id INTEGER,vip_id INTEGER);
            CREATE TABLE train_restrictions (player_id INTEGER PRIMARY KEY,reason TEXT);''')

    def cursor(self):
        return MergeCursor(self.db)


class MergeCursor(Cursor):
    def execute(self, sql, args=()):
        super().execute(sql.replace('BINARY player_name','player_name').replace('INSERT IGNORE','INSERT OR IGNORE'),args)

    def executemany(self, sql, args):
        for parameters in args:
            self.execute(sql,parameters)


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.db = PerformanceDB()
        patcher = patch.object(app,'db_connect',self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_players(self):
        app.import_players(app.ImportData(players=[
            app.Member(name='DonPepon',rank='R4',kills=100,upload_date=date(2026,11,2)),
            app.Member(name='ep',rank='R3',power=200,kills=999,upload_date=date(2026,11,2)),
        ]))
        return [row[0] for row in self.db.db.execute('SELECT id FROM `player list` ORDER BY id')]

    def test_merge_transfers_history_pending_rows_and_ocr_alias(self):
        keep, duplicate = self.add_players()
        self.db.db.execute("INSERT INTO player_name_dictionary VALUES ('oldocr','ep')")
        self.db.db.execute('INSERT INTO train_restrictions VALUES (?,?)',(duplicate,'Carry over this reason'))
        self.db.db.execute("INSERT INTO media_scan_jobs (id,updated_player_ids) VALUES ('job',?)",(json.dumps([duplicate]),))
        self.db.db.execute("INSERT INTO `roster upload` (player_name) VALUES ('ep')")
        self.db.db.execute("INSERT INTO vs_scan_rows (job_id,player_name) VALUES ('job','ep')")
        self.db.db.executemany('INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)',[
            ('2026-11-02',keep,'DonPepon','donpepon',100),
            ('2026-11-02',duplicate,'ep','ep',300),
            ('2026-11-03',duplicate,'ep','ep',400),
            ('2026-11-04',None,'ep','ep',500),
        ])
        result = merge_player_records(self.db,keep,PlayerMerge(duplicate_id=duplicate,expected_name='DonPepon',expected_duplicate_name='ep'))
        self.assertEqual(result['overlapping_vs_scores'],1)
        self.assertEqual(tuple(self.db.db.execute('SELECT * FROM train_restrictions').fetchone()),
                         (keep,'Carry over this reason'))
        self.assertEqual([tuple(row) for row in self.db.db.execute('SELECT id,player_name,power,kills FROM `player list`')],
                         [(keep,'DonPepon',200,100)])
        self.assertEqual([tuple(row) for row in self.db.db.execute('SELECT player_id,points FROM vs_points ORDER BY score_date')],
                         [(keep,100),(keep,400),(keep,500)])
        self.assertEqual([row[0] for row in self.db.db.execute('SELECT player_id FROM player_stat_history')],[keep,keep])
        self.assertEqual(dict(self.db.db.execute('SELECT ocr_key,player_name FROM player_name_dictionary')),
                         {'ep':'DonPepon','oldocr':'DonPepon'})
        self.assertEqual(tuple(self.db.db.execute('SELECT player_name,ocr_name FROM vs_scan_rows').fetchone()),('DonPepon','ep'))
        self.assertEqual(tuple(self.db.db.execute('SELECT player_name,ocr_name FROM `roster upload`').fetchone()),('DonPepon','ep'))
        self.assertEqual(json.loads(self.db.db.execute('SELECT updated_player_ids FROM media_scan_jobs').fetchone()[0]),[keep])
        app.import_players(app.ImportData(players=[app.Member(name='ep',rank='R4',power=300)]))
        self.assertEqual(tuple(self.db.db.execute('SELECT COUNT(*),MAX(power) FROM `player list`').fetchone()),(1,300))

    def test_merge_checks_names_before_mutating(self):
        keep, duplicate = self.add_players()
        with self.assertRaises(HTTPException) as failure:
            merge_player_records(self.db,keep,PlayerMerge(duplicate_id=duplicate,expected_name='DonPepon',expected_duplicate_name='Changed'))
        self.assertEqual(failure.exception.status_code,409)
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM `player list`').fetchone()[0],2)

    def test_merge_queries_are_accepted_by_workspace_scoping(self):
        from scoped_db import scoped_query
        keep, duplicate = self.add_players()
        self.db.db.execute("INSERT INTO train_restrictions VALUES (?,?)",(duplicate,'Reason'))
        self.db.db.execute("INSERT INTO media_scan_jobs (id,updated_player_ids) VALUES ('job',?)",(json.dumps([duplicate]),))
        self.db.db.execute("INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)",
                           ('2026-11-03',duplicate,'ep','ep',400))
        original_execute = MergeCursor.execute
        def scoped_execute(cursor, sql, args=()):
            scoped_query(sql, 17)
            return original_execute(cursor, sql, args)
        with patch.object(MergeCursor, 'execute', scoped_execute):
            merge_player_records(self.db,keep,PlayerMerge(duplicate_id=duplicate,
                expected_name='DonPepon',expected_duplicate_name='ep'))
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM `player list`').fetchone()[0],1)

    def test_claim_moves_unmatched_scores_and_keeps_existing_daily_score(self):
        keep, other = self.add_players()
        self.db.db.executemany('INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)',[
            ('2026-11-02',keep,'DonPepon','donpepon',100),
            ('2026-11-02',None,'DonPepon OCR','donpeponocr',300),
            ('2026-11-03',None,'DonPepon OCR','donpeponocr',400),
            ('2026-10-26',None,'DonPepon OCR','donpeponocr',500),
            ('2026-11-02',None,'ep','ep',600),
        ])
        web=Mock()
        install_vs_points(web,self.db)
        routes={route.path:route.endpoint for route in web.include_router.call_args.args[0].routes
                if 'GET' in route.methods}
        options=routes['/api/vs-points/claims/{player_id}'](keep)['options']
        self.assertEqual([(row['name_key'],row['score_days']) for row in options],[('donpeponocr',3)])
        weekly=routes['/api/vs-points/weekly'](date(2026,11,2))['rows']
        self.assertEqual({row['name']:row['roster_id'] for row in weekly},
                         {'DonPepon':keep,'DonPepon OCR':None,'ep':other})
        claim=VSClaim(name_key='donpeponocr',expected_player_name='DonPepon')
        result=claim_vs_scores(self.db,keep,claim)
        self.assertEqual(result,{'claimed_name':'DonPepon OCR','score_days':3,'overlapping_vs_scores':1})
        self.assertEqual([tuple(row) for row in self.db.db.execute(
            'SELECT player_id,points FROM vs_points WHERE normalized_name=? ORDER BY score_date',('donpepon',))],
            [(keep,500),(keep,100),(keep,400)])
        self.assertEqual(routes['/api/vs-points/claims/{player_id}'](keep)['options'],[])
        self.assertEqual(stage_vs_result(self.db,'later',{'score_date':'2026-11-04','upload_date':'2026-11-05',
            'weekday':'Wed','rows':[{'name':'DonPepon OCR','points':800,'review_reasons':[]}]}),{'saved':1,'pending':0,'skipped':0})
        with self.assertRaises(HTTPException) as failure:
            claim_vs_scores(self.db,keep,claim)
        self.assertEqual(failure.exception.status_code,409)
        with self.assertRaises(HTTPException) as failure:
            claim_vs_scores(self.db,keep,VSClaim(name_key='ep',expected_player_name='DonPepon'))
        self.assertEqual(failure.exception.status_code,409)

    def test_repeat_upload_skips_flagged_rows_matching_saved_readings(self):
        keep, other = self.add_players()
        self.db.db.executemany('INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)',[
            ('2026-11-02',keep,'DonPepon','donpepon',100),
            ('2026-11-02',other,'ep','ep',200),
        ])
        self.db.db.execute("INSERT INTO media_scan_jobs (id,updated_player_ids) VALUES ('repeat','[]')")
        rows = [
            {'name':'DonPepon','points':111,'point_readings':[100,111],'review_reasons':['Check points']},
            {'name':'Unknown','name_readings':['ep'],'points':222,'point_readings':[200,222],'review_reasons':['Check points']},
            {'name':'DonPepon','points':130,'point_readings':[130],'review_reasons':[]},
            {'name':'ep','points':203,'point_readings':[203,204],'review_reasons':['Check points']},
            {'name':'Other OCR','name_readings':['donpepon','ep'],'points':200,'review_reasons':['Check points']},
        ]
        result=stage_vs_result(self.db,'repeat',{'score_date':'2026-11-02','upload_date':'2026-11-05','weekday':'Mon','rows':rows})
        self.assertEqual(result,{'saved':1,'pending':2,'skipped':2})
        self.assertEqual([tuple(row) for row in self.db.db.execute('SELECT player_id,points FROM vs_points ORDER BY player_id')],
                         [(keep,130),(other,200)])
        self.assertEqual([row[0] for row in self.db.db.execute('SELECT player_name FROM vs_scan_rows ORDER BY id')],
                         ['ep','Other OCR'])
        self.assertEqual(json.loads(self.db.db.execute('SELECT updated_player_ids FROM media_scan_jobs').fetchone()[0]),
                         [keep,other])

    def test_dashboard_distinguishes_zero_missing_and_comparable_weekdays(self):
        keep, duplicate = self.add_players()
        app.import_players(app.ImportData(players=[app.Member(name='ep',rank='R3',power=220,upload_date=date(2026,11,5))]))
        self.db.db.executemany('INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)',[
            ('2026-10-26',keep,'DonPepon','donpepon',10),
            ('2026-10-28',keep,'DonPepon','donpepon',999),
            ('2026-11-02',keep,'DonPepon','donpepon',0),
            ('2026-11-03',keep,'DonPepon','donpepon',30),
        ])
        result = app.player_performance(date(2026,11,4))
        players = {row['id']:row for row in result['players']}
        self.assertEqual((result['week_start'],result['week_end']),('2026-11-02','2026-11-08'))
        self.assertEqual((players[keep]['vs_points'],players[keep]['vs_change'],players[keep]['days_compared']),('30','-10',1))
        self.assertEqual(result['daily'][0]['players'],1)
        self.assertEqual(result['daily'][0]['points'],'0')
        self.assertIsNone(players[duplicate]['vs_points'])
        self.assertEqual(players[duplicate]['power_change'],'20')

    def test_profile_compares_matching_weekdays_and_keeps_missing_scores_blank(self):
        keep, other = self.add_players()
        self.db.db.executemany('INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)',[
            ('2026-10-26',keep,'DonPepon','donpepon',10),
            ('2026-10-31',keep,'DonPepon','donpepon',60),
            ('2026-11-01',keep,'DonPepon','donpepon',99),
            ('2026-11-02',keep,'DonPepon','old-name',0),
            ('2026-11-02',None,'DonPepon','donpepon',88),
            ('2026-11-04',keep,'DonPepon','donpepon',30),
            ('2026-11-09',keep,'DonPepon','donpepon',70),
            ('2026-11-03',other,'ep','ep',80),
        ])
        history = app.player_history(keep,date(2026,11,6))
        self.assertEqual(history['vs_comparison'], {
            'current':[0,None,30,None,None,None],
            'previous':[10,None,None,None,None,60],
        })
        self.assertEqual([row['value'] for row in history['vs']],[70,30,70])

    def test_unreadable_vs_weekday_stages_even_known_scores_without_writing_them(self):
        keep,_=self.add_players()
        self.db.db.execute("INSERT INTO media_scan_jobs (id,updated_player_ids) VALUES ('undated','[]')")
        counts=stage_vs_result(self.db,'undated',{'score_date':None,'upload_date':'2026-11-06','weekday':'',
            'rows':[{'name':'DonPepon','points':240,'review_reasons':[]}]})
        self.assertEqual(counts,{'saved':0,'pending':1,'skipped':0})
        self.assertEqual(self.db.db.execute('SELECT COUNT(*) FROM vs_points').fetchone()[0],0)
        self.assertEqual(tuple(self.db.db.execute('SELECT score_date,points FROM vs_scan_rows').fetchone()),(None,240))

    def test_archived_players_are_excluded_from_alliance_totals_but_keep_history(self):
        keep,other=self.add_players()
        self.db.db.execute('UPDATE `player list` SET archived=TRUE WHERE id=?',(other,))
        self.db.db.executemany('INSERT INTO vs_points (score_date,player_id,player_name,normalized_name,points) VALUES (?,?,?,?,?)',[
            ('2026-11-02',keep,'DonPepon','donpepon',100),
            ('2026-11-02',other,'ep','ep',200),
        ])
        result=app.player_performance(date(2026,11,2))
        self.assertEqual([row['id'] for row in result['players']],[keep])
        self.assertEqual(result['daily'][0]['points'],'100')
        self.assertEqual(app.player_history(other)['vs'],[{'date':'2026-11-08','value':200}])

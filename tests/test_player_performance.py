from datetime import date
import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app
from player_merge import PlayerMerge, merge_player_records
from test_player_merge_vs import Cursor, SQLDatabase


class PerformanceDB(SQLDatabase):
    def __init__(self):
        super().__init__()
        self.db.create_function('JSON_CONTAINS',2,lambda values,item: int(json.loads(item) in json.loads(values or '[]')))
        self.db.create_function('LEAST',2,lambda a,b: min(a,b) if a and b else a or b)
        self.db.create_function('GREATEST',2,lambda a,b: max(a,b) if a and b else a or b)
        self.db.executescript('CREATE TABLE `roster upload` (player_name TEXT,ocr_name TEXT);')

    def cursor(self):
        return MergeCursor(self.db)


class MergeCursor(Cursor):
    def execute(self, sql, args=()):
        super().execute(sql.replace('BINARY player_name','player_name'),args)

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

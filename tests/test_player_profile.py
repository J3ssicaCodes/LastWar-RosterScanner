import unittest
from unittest.mock import patch
from fastapi import HTTPException
import app
from test_player_merge_vs import SQLDatabase

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.db=SQLDatabase()
        p=patch.object(app,'db_connect',self.db);p.start();self.addCleanup(p.stop)
        app.import_players(app.ImportData(players=[app.Member(name='Jess',rank='R4',power=123,kills=99)]))
        self.id=self.db.db.execute('SELECT id FROM `player list`').fetchone()[0]
    def update(self,name='Jessica',rank='R5',**extra):
        return app.update_player_profile(self.id,app.PlayerProfileUpdate(name=name,rank=rank,expected_name=extra.get('expected_name','Jess'),expected_rank='R4'))
    def test_rename_keeps_stats_history_vs_and_teaches_dictionary(self):
        self.db.db.execute("INSERT INTO vs_points(score_date,player_id,player_name,normalized_name,points) VALUES ('2026-11-03',?,'Jess','jess',456)",(self.id,))
        self.db.db.execute("INSERT INTO player_name_dictionary VALUES ('jcss','Jess')")
        self.db.commit()
        self.update()
        row=self.db.db.execute('SELECT id,player_name,player_rank,power,kills FROM `player list`').fetchone()
        self.assertEqual(tuple(row),(self.id,'Jessica','R5',123,99))
        self.assertEqual(tuple(self.db.db.execute('SELECT player_id,player_name,normalized_name,points FROM vs_points').fetchone()),(self.id,'Jessica','jessica',456))
        self.assertEqual(dict(self.db.db.execute('SELECT * FROM player_name_dictionary').fetchall()),{'jcss':'Jessica','jess':'Jessica'})
        self.assertEqual(self.db.db.execute('SELECT count(*) FROM player_stat_history').fetchone()[0],1)
    def test_duplicate_and_stale_edits_are_rejected(self):
        app.import_players(app.ImportData(players=[app.Member(name='Other',rank='R2')]))
        for kwargs in ({'name':'Other'},{'expected_name':'Old Jess'}):
            with self.assertRaises(HTTPException) as error:self.update(**kwargs)
            self.assertEqual(error.exception.status_code,409)
        self.assertEqual(self.db.db.execute('SELECT player_name FROM `player list` WHERE id=?',(self.id,)).fetchone()[0],'Jess')
    def test_rank_only_does_not_learn_alias(self):
        self.update(name='Jess')
        self.assertEqual(self.db.db.execute('SELECT count(*) FROM player_name_dictionary').fetchone()[0],0)
    def test_missing_player(self):
        self.id=999
        with self.assertRaises(HTTPException) as error:self.update()
        self.assertEqual(error.exception.status_code,404)

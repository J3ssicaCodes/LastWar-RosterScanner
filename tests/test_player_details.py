import unittest
from unittest.mock import patch
from pydantic import ValidationError
from fastapi import HTTPException
import app
from test_player_merge_vs import SQLDatabase


class PlayerDetailsTests(unittest.TestCase):
    def setUp(self):
        self.db=SQLDatabase()
        patcher=patch.object(app,'db_connect',self.db)
        patcher.start();self.addCleanup(patcher.stop)
        app.import_players(app.ImportData(players=[app.Member(name='Jess',rank='R4')]))
        self.player_id=self.db.db.execute('SELECT id FROM `player list`').fetchone()[0]
        self.base=dict(has_t11=True,away_start=None,away_end=None,notes='Away from game',
            expected_has_t11=False,expected_away_start=None,expected_away_end=None,expected_notes='')

    def test_saves_details_preserves_them_when_scanning_and_detects_stale_edits(self):
        body=app.PlayerDetailsUpdate(**self.base)
        app.update_player_details(self.player_id,body)
        app.import_players(app.ImportData(players=[app.Member(name='Jess',rank='R4',power=500)]))
        row=self.db.db.execute('SELECT has_t11,player_notes,power FROM `player list`').fetchone()
        self.assertEqual(tuple(row),(1,'Away from game',500))
        with self.assertRaises(HTTPException) as error:
            app.update_player_details(self.player_id,body)
        self.assertEqual(error.exception.status_code,409)

    def test_dates_must_be_complete_and_ordered(self):
        for changes in ({'away_start':'2026-01-01'},
                        {'away_start':'2026-01-02','away_end':'2026-01-01'}):
            with self.assertRaises(ValidationError):app.PlayerDetailsUpdate(**{**self.base,**changes})
        app.PlayerDetailsUpdate(**{**self.base,'away_start':'2026-01-01','away_end':'2026-01-01'})

    def test_missing_player_is_not_created(self):
        with self.assertRaises(HTTPException) as error:
            app.update_player_details(999,app.PlayerDetailsUpdate(**self.base))
        self.assertEqual(error.exception.status_code,404)

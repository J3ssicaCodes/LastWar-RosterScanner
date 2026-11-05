import sqlite3
import unittest
from threading import RLock
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import HTTPException
from admin_reset import ClearData, USER_DATA_MARKER, install_admin_reset


class ResetTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE "player list" (id INT);
            CREATE TABLE "roster upload" (id INT);
            CREATE TABLE media_scan_jobs (id INT, status TEXT);
            CREATE TABLE player_name_dictionary (id INT);
            CREATE TABLE unrelated (id INT);
            CREATE TABLE future_scores (id INT);
            CREATE TABLE alliance_admin_state (id INT PRIMARY KEY, reset_id TEXT);
            INSERT INTO "player list" VALUES (1);
            INSERT INTO "roster upload" VALUES (1);
            INSERT INTO media_scan_jobs VALUES (1, 'review');
            INSERT INTO player_name_dictionary VALUES (1);
            INSERT INTO unrelated VALUES (1);
            INSERT INTO future_scores VALUES (1);
        """)
        self.jobs = {'legacy': {'status': 'ready'}}
        self.deps = []
        self.routes = {}
        def register(path):
            def decorate(func):
                self.routes[path] = func
                return func
            return decorate

        app = SimpleNamespace(post=register, get=register, middleware=register)
        install_admin_reset(app, self.connect, self.jobs, RLock())

    @contextmanager
    def connect(self):
        yield SimpleNamespace(cursor=self.cursor, commit=self.db.commit, rollback=self.db.rollback)

    @contextmanager
    def cursor(self):
        owner = self
        sql_cursor = self.db.cursor()
        class Cursor:
            results = None
            rowcount = 0

            def execute(self, sql, args=()):
                if 'information_schema.TABLES' in sql:
                    self.results = [
                        {'name': row[0], 'engine': 'InnoDB',
                         'comment': USER_DATA_MARKER if row[0] in ('future_scores', 'player_name_dictionary') else ''}
                        for row in owner.db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    ]
                elif 'information_schema.KEY_COLUMN_USAGE' in sql:
                    self.results = owner.deps
                else:
                    sql = sql.replace(' FOR UPDATE', '').replace('%s', '?').replace(
                        'ON DUPLICATE KEY UPDATE reset_id=VALUES(reset_id)',
                        'ON CONFLICT(id) DO UPDATE SET reset_id=excluded.reset_id',
                    )
                    sql_cursor.execute(sql, args)
                    self.results = sql_cursor.fetchall()
                    self.rowcount = sql_cursor.rowcount

            def fetchall(self):
                return self.results

            def fetchone(self):
                return next(iter(self.results), None)

        yield Cursor()
        sql_cursor.close()

    def clear(self):
        return self.routes['/api/admin/clear-data'](ClearData(confirmation='CLEAR DATA'))

    def count(self, table):
        return self.db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

    def test_clears_current_and_future_data_but_preserves_dictionary_and_unrelated_tables(self):
        result = self.clear()
        self.assertEqual(result['rows_deleted'], 4)
        self.assertEqual(self.count('player list'), 0)
        self.assertEqual(self.count('roster upload'), 0)
        self.assertEqual(self.count('future_scores'), 0)
        self.assertEqual(self.count('player_name_dictionary'), 1)
        self.assertEqual(self.count('unrelated'), 1)
        self.assertEqual(self.count('alliance_admin_state'), 1)
        self.assertFalse(self.jobs)

    def test_rejects_active_scan(self):
        self.db.execute("UPDATE media_scan_jobs SET status='scanning'")
        self.db.commit()
        with self.assertRaises(HTTPException) as error:
            self.clear()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.count('player list'), 1)

    def test_deletion_error_rolls_back_earlier_deletes(self):
        self.db.executescript("""CREATE TRIGGER abort_reset BEFORE DELETE ON "player list"
            BEGIN SELECT RAISE(ABORT,'test failure'); END;""")
        with self.assertRaises(HTTPException) as error:
            self.clear()
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.count('future_scores'), 1)
        self.assertEqual(self.count('media_scan_jobs'), 1)
        self.assertEqual(self.count('alliance_admin_state'), 0)

    def test_rejects_a_dependency_from_a_preserved_table(self):
        self.deps = [{'child_schema': 'alliance', 'app_schema': 'alliance',
                      'child': 'player_name_dictionary', 'parent': 'player list'}]
        with self.assertRaises(HTTPException) as error:
            self.clear()
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.count('player_name_dictionary'), 1)

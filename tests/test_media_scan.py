"""Worker regressions using isolated OCR and database substitutes."""
import asyncio
import io
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException, UploadFile
import media_upload as media
from app import ImportData
from test_player_merge_vs import SQLDatabase


class Database:
    def __init__(self):
        self.statements = []
        self.fail_staging = False
        self.players = []
        self.query = ''

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self):
        return self

    def execute(self, query, args=()):
        # Exercise DB-API parameter binding, including the review-row INSERT.
        query % tuple(args)
        self.query = query
        if self.fail_staging and 'INSERT INTO `roster upload`' in query:
            raise RuntimeError('staging unavailable')
        self.statements.append((query, args))

    def executemany(self, query, args):
        for row in args:
            self.execute(query,row)

    def fetchone(self):
        return {'n': 1}

    def fetchall(self):
        return [dict(p) for p in self.players] if 'FROM `player list`' in self.query else []

    def commit(self):
        self.statements.append(('COMMIT', ()))

    def rollback(self):
        self.statements.append(('ROLLBACK', ()))


class MediaScanTests(unittest.TestCase):
    def test_queue_counts_existing_rows_and_includes_older_reviews(self):
        db = SQLDatabase()
        for field in ('destination','created_at','progress','result_count','full_capture','started_at','finished_at'):
            db.db.execute(f'ALTER TABLE media_scan_jobs ADD COLUMN {field} TEXT')
        db.db.execute('CREATE TABLE `roster upload` (job_id TEXT)')
        db.db.execute('CREATE TABLE media_missing_players (player_id INTEGER PRIMARY KEY,job_id TEXT,last_import_id TEXT)')
        recent = [(f'recent-{i}','ready','players') for i in range(36)]
        db.db.executemany('INSERT INTO media_scan_jobs (id,status,destination) VALUES (?,?,?)',
                          [*recent,('a-real','interrupted','vs'),('z-stale','review','vs')])
        db.db.executemany('INSERT INTO vs_scan_rows (job_id) VALUES (?)',[('a-real',),('a-real',)])
        web = Mock()
        media.install_media_upload(web,db,Mock(),ImportData)
        jobs = next(r.endpoint for r in web.include_router.call_args.args[0].routes if r.path=='/api/media/jobs')()['jobs']
        self.assertEqual((jobs[0]['id'],jobs[0]['pending_count']),('a-real',2))
        stale = next(job for job in jobs if job['id']=='z-stale')
        self.assertEqual((stale['status'],stale['pending_count']),('approved',0))
        self.assertEqual(len(jobs),36)

    def setUp(self):
        self.db = Database()
        self.app = Mock()
        self.importer = Mock()
        media.install_media_upload(self.app, self.db, self.importer, ImportData)
        router = self.app.include_router.call_args.args[0]
        self.upload = next(r.endpoint for r in router.routes if r.path == '/api/media/upload')
        self.pool = Mock()
        self.patch_pool = patch.object(media, 'executor', self.pool)
        self.patch_pool.start()
        self.addCleanup(self.patch_pool.stop)
        self.scan = patch.object(media, 'scan_tested_rankings_upload', return_value={
            'players': [{'name': 'Test Player', 'rank': 'R4', 'power': 123456789,
                         'tab': 'power', 'internal_position': 1}],
            'diagnostics': {}, 'audit': []})
        self.scanner = self.scan.start()
        self.addCleanup(self.scan.stop)

    def queued_worker(self, filename='test.mp4'):
        asyncio.run(self.upload('players', [UploadFile(filename=filename, file=io.BytesIO(b'fixture'))]))
        worker, job_id, destination, paths, directory = self.pool.submit.call_args.args
        self.addCleanup(lambda: __import__('shutil').rmtree(directory, ignore_errors=True))
        return worker, job_id, destination, paths, directory

    def test_video_results_are_staged_for_review_and_source_is_cleaned(self):
        worker, job, dest, paths, directory = self.queued_worker()
        worker(job, dest, paths, directory)
        staged = [args for q, args in self.db.statements if 'INSERT INTO `roster upload`' in q]
        self.assertEqual(len(staged), 1)
        self.assertEqual(staged[0][1:3], ('Test Player', 'R4'))
        self.assertTrue(any('status=%s' in q and args[0] == 'review' for q, args in self.db.statements))
        self.assertFalse(directory.exists())

    def test_only_video_stages_missing_players_and_ignores_archived_players(self):
        self.db.players=[{'id':7,'player_name':'Missing Player','player_rank':'R4','archived':False},
                         {'id':8,'player_name':'Old Player','player_rank':'R4','archived':True}]
        worker,job,dest,paths,directory=self.queued_worker()
        worker(job,dest,paths,directory)
        missing = [args for sql,args in self.db.statements if 'INSERT INTO media_missing_players' in sql]
        self.assertEqual(missing,[(7,job,None)])
        self.db.statements.clear()
        worker,job,dest,paths,directory=self.queued_worker('test.png')
        with patch.object(media,'scan_upload',return_value={'players':[{'name':'Visible Player','rank':'R4'}]}):
            worker(job,dest,paths,directory)
        self.assertFalse(any('INSERT INTO media_missing_players' in sql for sql,args in self.db.statements))

    def test_missing_player_decisions_keep_history_and_reject_stale_reviews(self):
        db=SQLDatabase()
        db.db.execute('CREATE TABLE media_missing_players (player_id INTEGER PRIMARY KEY,job_id TEXT,last_import_id TEXT)')
        db.db.execute("INSERT INTO media_scan_jobs (id,status) VALUES ('video','review')")
        db.db.executemany('INSERT INTO `player list` (id,player_name,last_import_id) VALUES (?,?,?)',
                          [(1,'Keep',None),(2,'Remove','original'),(3,'Found','new-scan')])
        db.db.executemany('INSERT INTO media_missing_players VALUES (?,?,?)',
                          [(1,'video',None),(2,'video','original'),(3,'video','old-scan')])
        web=Mock()
        media.install_media_upload(web,db,Mock(),ImportData)
        decide=next(r.endpoint for r in web.include_router.call_args.args[0].routes if r.path=='/api/media/missing-players/{player_id}')
        self.assertEqual(decide(1,'video',False),{'archived':False})
        self.assertEqual(decide(2,'video',True),{'archived':True})
        with self.assertRaises(HTTPException) as failure:
            decide(3,'video',True)
        self.assertEqual(failure.exception.status_code,409)
        self.assertEqual([tuple(row) for row in db.db.execute('SELECT id,archived FROM `player list` ORDER BY id')],
                         [(1,0),(2,1),(3,0)])

    def test_known_player_kills_merges_into_master_automatically(self):
        self.db.players=[{'id':1,'player_name':'Test Player','player_rank':'R4',
                          'normalized_name':'testplayer','avatar_image':None}]
        self.scanner.return_value={'players':[{'name':'TestPlayer','rank':'Unknown','kills':987654,'tab':'kills','review_required':True}],
                                   'diagnostics':{},'audit':[]}
        worker,job,dest,paths,directory=self.queued_worker()
        worker(job,dest,paths,directory)
        self.importer.assert_called_once()
        member=self.importer.call_args.args[0].players[0]
        self.assertEqual((member.name,member.rank,member.kills,member.power),('Test Player','R4',987654,None))
        self.assertTrue(any('started_at=COALESCE' in q for q,args in self.db.statements))
        self.assertTrue(any('finished_at=COALESCE' in q for q,args in self.db.statements))
        self.assertFalse(any('INSERT INTO `roster upload`' in query for query,args in self.db.statements))
        self.assertFalse(directory.exists())

    def test_failed_scan_keeps_drive_original(self):
        worker, job, dest, paths, directory = self.queued_worker()
        self.scanner.side_effect = ValueError('No readable players')
        with patch.object(media, 'download_selected_file', return_value=paths[0]), patch.object(media, 'move_selected_file_to_trash') as trash:
            worker(job, dest, [], directory, {'file_id': 'example123', 'access_token': 'test', 'move_to_trash': True})
            trash.assert_not_called()
        self.assertTrue(any('status=%s' in q and args[0] == 'error' for q, args in self.db.statements))
        self.assertFalse(directory.exists())

    def test_failed_persistence_keeps_drive_original(self):
        worker, job, dest, paths, directory = self.queued_worker()
        self.db.fail_staging = True
        with patch.object(media, 'download_selected_file', return_value=paths[0]), patch.object(media, 'move_selected_file_to_trash') as trash:
            worker(job, dest, [], directory, {'file_id': 'example123', 'access_token': 'test', 'move_to_trash': True})
            trash.assert_not_called()
        self.assertTrue(any(q == 'ROLLBACK' for q, args in self.db.statements))
        self.assertFalse(directory.exists())


if __name__ == '__main__':
    unittest.main()

"""Worker regressions using isolated OCR and database substitutes."""
import asyncio
import io
import unittest
from unittest.mock import Mock, patch

from fastapi import UploadFile
import media_upload as media


class Database:
    def __init__(self):
        self.statements = []
        self.fail_staging = False

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
        if self.fail_staging and 'INSERT INTO `roster upload`' in query:
            raise RuntimeError('staging unavailable')
        self.statements.append((query, args))

    def fetchone(self):
        return {'n': 1}

    def fetchall(self):
        return []

    def commit(self):
        self.statements.append(('COMMIT', ()))

    def rollback(self):
        self.statements.append(('ROLLBACK', ()))


class MediaScanTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.app = Mock()
        media.install_media_upload(self.app, self.db, Mock(), Mock())
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

    def queued_worker(self):
        asyncio.run(self.upload('players', [UploadFile(filename='test.mp4', file=io.BytesIO(b'fixture'))]))
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

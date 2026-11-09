import os
import unittest
from unittest.mock import Mock, patch
from py_vapid import _check_sub
from scoped_db import scoped_query
from alliance_push import dispatch_push


class TrainListTests(unittest.TestCase):
    def test_train_aggregation_is_scoped_by_alliance(self):
        for role in ('conductor','vip'):
            sql=f'SELECT {role}_id AS player_id, MAX(assignment_date) AS last_date FROM train_assignments WHERE assignment_date<=%s AND {role}_id IS NOT NULL GROUP BY {role}_id'
            scoped=scoped_query(sql,17)
            self.assertIn('workspace_train_assignments',scoped)
            self.assertIn('alliance_id = 17',scoped)
            self.assertEqual(scoped.count('%s'),1)

    def test_empty_or_root_url_vapid_subject_has_valid_default(self):
        for subject in ('','https://lastwar-rosterscanner.onrender.com/'):
            with self.subTest(subject=subject):
                cur=Mock()
                cur.fetchall.return_value=[{'id':1,'title':'Reminder','target':'/',
                                            'subscription_id':2,'subscription_json':{}}]
                with patch.dict(os.environ,{'ALLIANCE_VAPID_PRIVATE_KEY':'key','ALLIANCE_VAPID_SUBJECT':subject}), \
                        patch('alliance_push.webpush') as push:
                    dispatch_push(cur)
                self.assertTrue(_check_sub(push.call_args.kwargs['vapid_claims']['sub']))


if __name__ == '__main__':
    unittest.main()

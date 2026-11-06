import base64
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from google_vision_ocr import recheck_vs_rows
from player_identity import number_needs_review


class GoogleVisionTests(unittest.TestCase):
    def setUp(self):
        _, encoded = cv2.imencode('.webp', np.full((200,1000,3),255,dtype=np.uint8))
        self.crop = base64.b64encode(encoded).decode()

    def row(self, name, points, reasons, readings):
        return {'name':name, 'ocr_name':name, 'points':points,
                'review_reasons':reasons, 'point_readings':readings, 'evidence_base64':self.crop}

    @staticmethod
    def response(points='10,000'):
        def word(text,x,y):
            return {'description':text, 'boundingPoly':{'vertices':[
                {'x':x,'y':y}, {'x':x+35,'y':y}, {'x':x+35,'y':y+15}, {'x':x,'y':y+15},
            ]}}
        return {'textAnnotations':[
            {'description':'Entire image'}, word('7',55,90), word('Lady',330,54), word('Jess',380,54),
            word('[RVBY]',320,118), word('Rubies',390,118), word(points,850,88),
        ]}

    @patch('google_vision_ocr.reserve_vision_images', return_value=1)
    @patch('google_vision_ocr.annotate_images')
    def test_sends_only_flagged_rows_and_confirms_agreeing_score(self, annotate, reserve):
        annotate.return_value = [self.response()]
        clear = self.row('Lady Jess',2000,[],[2000])
        flagged = self.row('Ladv Jcss',10000,['Check player name','Check points',
                            'Number needs verification: frame readings were 10,000 / unavailable.'],[10000])
        recheck_vs_rows({'rows':[clear,flagged]},'test-key',Mock(),{}, {'ladyjess'},Mock())
        annotate.assert_called_once_with('test-key',[self.crop])
        self.assertEqual(flagged['name'],'Lady Jess')
        self.assertEqual(flagged['ocr_name'],'Ladv Jcss')
        self.assertEqual(flagged['review_reasons'],[])
        self.assertEqual(clear['points'],2000)

    @patch('google_vision_ocr.reserve_vision_images', return_value=1)
    @patch('google_vision_ocr.annotate_images')
    def test_conflicting_score_is_retained_for_manual_review(self, annotate, reserve):
        annotate.return_value = [self.response('20,000')]
        row = self.row('Lady Jess',10000,['Points differ between captured views'],[10000,20000])
        recheck_vs_rows({'rows':[row]},'test-key',Mock(),{}, {'ladyjess'},Mock())
        self.assertEqual(row['points'],10000)
        self.assertTrue(number_needs_review(row['review_reasons']))
        self.assertIn('Number needs verification: Google Vision read 20,000.',row['review_reasons'])

    @patch('google_vision_ocr.reserve_vision_images', return_value=1)
    @patch('google_vision_ocr.annotate_images')
    def test_google_does_not_override_a_failed_video_frame_vote(self, annotate, reserve):
        annotate.return_value = [self.response()]
        row = self.row('Lady Jess',10000,['Number needs verification: fewer than three matching frames.'],[10000])
        row['frame_consensus_failed'] = True
        recheck_vs_rows({'rows':[row]},'test-key',Mock(),{}, {'ladyjess'},Mock())
        self.assertTrue(number_needs_review(row['review_reasons']))

    @patch('google_vision_ocr.reserve_vision_images', return_value=0)
    @patch('google_vision_ocr.annotate_images')
    def test_no_google_request_when_unconfigured_or_budget_used(self, annotate, reserve):
        rows = {'rows':[self.row('Unknown',10000,['Check points'],[10000])]}
        recheck_vs_rows(rows,None,Mock(),{}, {'ladyjess'},Mock())
        reserve.assert_not_called()
        recheck_vs_rows(rows,'test-key',Mock(),{}, {'ladyjess'},Mock())
        reserve.assert_called_once()
        annotate.assert_not_called()

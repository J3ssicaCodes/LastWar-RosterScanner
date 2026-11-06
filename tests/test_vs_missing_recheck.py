import unittest
from unittest.mock import Mock, patch

import numpy as np

from vs_points_ocr import missing_player_frames, recheck_missing_players


class MissingPlayerRecheckTests(unittest.TestCase):
    def test_samples_pauses_and_transitions_without_reusing_frames(self):
        self.assertEqual(missing_player_frames((30,100,0,0,[(0,20),(40,60)]),{0,10,50}),
                         [15,30,55,99])

    @patch('vs_points_ocr.read_page')
    @patch('vs_points_ocr.estimate_row_height',return_value=189)
    @patch('vs_points_ocr.selected_weekday',return_value=1)
    @patch('vs_points_ocr.cv2.VideoCapture')
    def test_only_searches_for_absent_roster_names_once(self, capture, weekday, height, read_page):
        image = np.zeros((2340,1080,3),dtype=np.uint8)
        signature = np.zeros((24,24),dtype=np.uint8)
        capture.return_value.read.return_value = (True,image)
        observations = {
            1:{'name':'Already Seen','signature':signature},
            2:{'name':'Unmatched','signature':signature,'value':'100'},
            3:{'name':'Other Read','signature':signature,'value':'200'},
        }
        variants = {3:{'another'}}
        # First candidate is unsafe to replace, then a valid OCR alias and a previously missed rank.
        read_page.side_effect = [
            [{'name':'Missing Alias','position_read':2,'signature':signature+200,'value':'900'}],
            [{'name':'Missing Alias','position_read':2,'signature':signature,'value':'100','page':-2,'name_conf':80},
             {'name':'Fresh Player','position_read':4,'signature':signature,'value':'0','page':-2}],
            [{'name':'Another','position_read':3,'signature':signature,'value':'200','name_conf':90}],
        ]
        progress = Mock()
        resolve = lambda name: 'Missing Player' if name == 'Missing Alias' else name
        keys = {'alreadyseen','another','missingplayer','freshplayer'}
        sources = {1:('clip.mp4',10)}
        scores = {}
        result = recheck_missing_players(
            observations,variants,scores,sources,[('clip.mp4',(30,100,0,0,[(0,20)]))],
            keys,keys,resolve,1,progress)
        self.assertEqual(result,(3,3))
        self.assertEqual(read_page.call_count,3)
        progress.assert_called_once()
        self.assertEqual(observations[1]['name'],'Already Seen')
        self.assertEqual(observations[2]['name'],'Missing Alias')
        self.assertEqual(observations[4]['name'],'Fresh Player')
        self.assertEqual(scores,{2:{100},3:{200},4:{0}})
        self.assertEqual(sources[observations[4]['page']],
                         ('clip.mp4',15))
        capture.return_value.release.assert_called_once()

    @patch('vs_points_ocr.cv2.VideoCapture')
    def test_skips_video_pass_when_every_roster_name_was_seen(self,capture):
        result = recheck_missing_players({1:{'name':'Already Seen'}},{},{},{},
                                        [('clip.mp4',(30,100,0,0,[]))],{'alreadyseen'},{'alreadyseen'},
                                        str,1,Mock())
        self.assertEqual(result,(0,0))
        capture.assert_not_called()

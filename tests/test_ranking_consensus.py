import unittest
from unittest.mock import Mock, patch

from scan_rankings import matching_statistic
from tested_rankings_adapter import deduplicate_ranking_records, recheck_ranking_group
from vs_points_ocr import recheck_neighbors


class RankingConsensusTests(unittest.TestCase):
    def test_multiple_captures_confirm_number_and_keep_name_concern(self):
        for tab in ('power','kills','donations'):
            with self.subTest(tab=tab):
                rows=[{'tab':tab,'internal_position':1,'name':'Jess',tab:value,
                       'seen_in_video':is_video,'review_reasons':['uncertain_stat','uncertain_name'],
                       'review_required':True}
                      for value,is_video in [(120,False),(121,True),(120,False)]]
                result=deduplicate_ranking_records(rows)
                self.assertEqual(len(result),1)
                self.assertEqual(result[0][tab],120)
                self.assertEqual(result[0]['review_reasons'],['uncertain_name'])
                self.assertTrue(result[0]['seen_in_video'])

    def test_page_consensus_requires_two_and_a_unique_winner(self):
        for values, expected in [(['123','123'],'123'), (['123','123','124'],'123'),
                                 (['123','124'],None), (['123','123','124','124'],None),
                                 (['123',''],None)]:
            with self.subTest(values=values):
                self.assertEqual(matching_statistic([{'value': v} for v in values]),expected)

    def test_ranking_frame_consensus_two_reads_and_ties(self):
        for readings, expected in [(['123',''],True), (['123','124'],True),
                                   (['124','125','126','127'],False),
                                   (['124','125','124','123'],False)]:
            with self.subTest(readings=readings):
                row={'internal_position':1,'page':1,'name':'Jess','value':'123',
                     'name_conf':90,'value_conf':10}
                cap=Mock()
                cap.read.side_effect=[(True,value) for value in readings]
                def read_frame(value, anchors):
                    return [{'matched_position':1,'value':value,'value_conf':10}]
                with patch('vs_points_ocr.open_video',return_value=cap):
                    confirmed=recheck_neighbors({1:row},{1:('video.mp4',40)},{1:[]},str,Mock(),read_frame,min_value_votes=2)
                self.assertEqual('value' in confirmed[1],expected)
                self.assertEqual(bool(row.get('neighbor_conflicts')),not expected)

    def test_verified_number_clears_number_flags_but_keeps_name_review(self):
        row={'internal_position':1,'power':123,'review_reasons':['uncertain_stat','stat_not_descending',
             'Number needs verification: unclear','uncertain_name'],'review_required':True}
        anchors={1:{'needs_name':True,'value':'123','name':'Jess'}}
        with patch('tested_rankings_adapter.recheck_neighbors',return_value={1:{'value'}}):
            recheck_ranking_group([row],anchors,{},'Power',100,str,Mock())
        self.assertEqual(row['review_reasons'],['uncertain_name'])
        self.assertTrue(row['review_required'])

    def test_confirmed_page_does_not_lose_confirmation_when_neighbor_unreadable(self):
        row={'internal_position':1,'power':123,'review_reasons':[],'review_required':False}
        anchors={1:{'needs_name':False,'value':'123','name':'Jess','stat_confirmed':True,
                    'neighbor_conflicts':['Number needs verification: unreadable']}}
        with patch('tested_rankings_adapter.recheck_neighbors',return_value={1:set()}):
            recheck_ranking_group([row],anchors,{},'Power',100,str,Mock())
        self.assertFalse(row['review_required'])


if __name__ == '__main__':
    unittest.main()

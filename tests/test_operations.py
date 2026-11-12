import unittest
from datetime import date, datetime, timedelta, timezone
from pydantic import ValidationError
from alliance_operations import OperationBody, TemplateBody
from alliance_leadership import Schedule, materialize, schedule_dates

class OperationsTests(unittest.TestCase):
    def test_leadership_must_be_distinct_and_at_most_five_secondary(self):
        body=OperationBody(name='Train',primary_id=1,secondary_ids=[2,3,4,5,6])
        self.assertEqual(len(body.secondary_ids),5)
        for secondaries in ([1],[2,2],[2,3,4,5,6,7]):
            with self.subTest(secondaries=secondaries),self.assertRaises(ValidationError):
                OperationBody(name='Train',primary_id=1,secondary_ids=secondaries)
    def test_assignments_do_not_create_days_before_leader_was_assigned(self):
        class Cursor:
            def __init__(self):self.rows=[]
            def executemany(self,sql,rows):self.rows.extend(rows)
        now=datetime(2026,11,12,17,tzinfo=timezone.utc)
        task={'id':10,'alliance_id':5,'assignee_id':7,'timezone':'America/New_York',
              'schedule_json':Schedule(kind='daily',start_date=date(2026,10,1),due_time='18:00').model_dump(mode='json')}
        cur=Cursor();materialize(cur,task,now,assigned_at=now-timedelta(days=1))
        self.assertTrue(cur.rows)
        self.assertEqual(min(row[3] for row in cur.rows),date(2026,11,11))
        self.assertEqual({row[2] for row in cur.rows},{7})
    def test_task_templates_support_custom_intervals(self):
        body=TemplateBody(title='Announce train',schedule=Schedule(kind='interval',interval=2,interval_unit='week',start_date=date(2026,11,12),due_time='18:00'))
        self.assertEqual(body.schedule.interval,2)

    def test_roster_and_account_references_can_be_assigned(self):
        body=OperationBody(name='Train',primary_ref='p:123',secondary_refs=['a:22','p:124'])
        self.assertEqual(body.primary_ref,'p:123')
        with self.assertRaises(ValidationError):
            OperationBody(name='Train',primary_ref='p:123',secondary_refs=['p:123'])
        with self.assertRaises(ValidationError):
            OperationBody(name='Train',primary_ref='p:0')

    def test_template_can_run_on_selected_weekdays(self):
        schedule=Schedule(kind='weekly',start_date=date(2026,11,9),due_time='18:00',weekdays=[0,3]).model_dump(mode='json')
        dates=[d for d,_ in schedule_dates(schedule,'UTC',datetime(2026,11,9,tzinfo=timezone.utc),datetime(2026,11,16,tzinfo=timezone.utc))]
        self.assertEqual(dates,[date(2026,11,9),date(2026,11,12)])

if __name__=='__main__':unittest.main()

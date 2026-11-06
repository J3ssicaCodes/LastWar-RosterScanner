from datetime import date
from sqlite3 import IntegrityError as SQLiteIntegrityError
from unittest import TestCase
from unittest.mock import Mock

from fastapi import HTTPException
from pymysql.err import IntegrityError
from train_manager import Assignment, HistoryEdit, install_train_manager, nominations, parse_train_csv
from test_player_merge_vs import Cursor, SQLDatabase


def player(pid,name,birthday=None,disabled=False):
    return {'id':pid,'name':name,'rank':'R4','birthday':birthday,'disabled':disabled,'possibly_removed':False}


class TrainDatabase(SQLDatabase):
    def __init__(self):
        super().__init__()
        self.db.executescript('''
            CREATE TABLE train_settings (id INTEGER PRIMARY KEY,alliance_name TEXT,cooldown_weeks INT,spin_seconds INT);
            CREATE TABLE train_event_names (name TEXT PRIMARY KEY);
            CREATE TABLE train_restrictions (player_id INTEGER PRIMARY KEY);
            CREATE TABLE train_assignments (
                assignment_date TEXT PRIMARY KEY,conductor_id INTEGER,vip_id INTEGER,
                conductor_name TEXT,vip_name TEXT,conductor_reason TEXT,vip_reason TEXT,
                goal TEXT,target INTEGER,conductor_points INTEGER,vip_points INTEGER);
            CREATE TABLE train_emails (assignment_date TEXT PRIMARY KEY,body TEXT);
            INSERT INTO `player list` (id,player_name,normalized_name,player_rank,possibly_removed)
                VALUES (1,'Alpha','alpha','R4',0),(2,'Bravo','bravo','R3',0),(3,'Charlie','charlie','R5',0);
            INSERT INTO train_restrictions VALUES (2);
        ''')

    def cursor(self):
        return TrainCursor(self.db)


class TrainCursor(Cursor):
    def execute(self,sql,args=()):
        try:
            super().execute(sql,args)
        except SQLiteIntegrityError as exc:
            raise IntegrityError(str(exc)) from exc

    def fetchall(self):
        rows = super().fetchall()
        for row in rows:
            if row.get('assignment_date'):
                row['assignment_date'] = date.fromisoformat(row['assignment_date'])
        return rows


class TrainTests(TestCase):
    def test_history_edits_update_roles_and_move_email_atomically(self):
        db=TrainDatabase()
        app=Mock()
        install_train_manager(app,db)
        endpoints={(r.path,next(iter(r.methods))):r.endpoint for r in app.include_router.call_args.args[0].routes}
        historical=endpoints['/api/train/history','POST']
        edit=endpoints['/api/train/history/{original_day}','PUT']
        original,occupied,moved=(date(2026,11,d) for d in (5,6,7))
        historical(Assignment(day=original,conductor_id=1,vip_id=3))
        historical(Assignment(day=occupied,conductor_id=1,vip_id=3))
        db.db.execute('INSERT INTO train_emails VALUES (?,?)',(original,'Saved draft'))
        db.db.commit()

        body=HistoryEdit(day=occupied,conductor_id=1,vip_id=2,original_conductor_id=1,original_vip_id=3)
        with self.assertRaises(HTTPException) as error:
            edit(original,body)
        self.assertEqual(error.exception.status_code,409)
        edit(original,body.model_copy(update={'day':original}))
        with self.assertRaises(HTTPException) as error:
            edit(original,body.model_copy(update={'day':moved}))
        self.assertEqual(error.exception.status_code,409)
        edit(original,body.model_copy(update={'day':moved,'original_vip_id':2}))

        history=endpoints['/api/train/history','GET']()
        self.assertEqual([(row['assignment_date'],row['vip_name']) for row in history['rows']],
                         [(moved,'Bravo'),(occupied,'Charlie')])
        self.assertEqual(len(history['players']),3)
        self.assertEqual(tuple(db.db.execute('SELECT * FROM train_emails').fetchone()),(moved.isoformat(),'Saved draft'))

    def test_birthdays_override_cooldown_but_not_restrictions(self):
        day = date(2026,11,6)
        players = [player(1,'Birthday',date(2000,11,7)),player(2,'Disabled',date(2000,11,6),True),
                   player(3,'Highest'),player(4,'Cooldown'),player(5,'Upcoming',date(2000,11,18))]
        points = {1:40,2:500,3:300,4:400,5:100}
        roster,ordered = nominations(players,points,{1:date(2026,11,5),4:date(2026,11,5)},day,4,'Push',100)
        self.assertEqual(ordered,[1,3,5])
        by_id = {p['id']:p for p in roster}
        self.assertEqual(by_id[1]['reason'],'Birthday this week')
        self.assertEqual(by_id[2]['blocked'],'Train awards disabled')
        self.assertEqual(by_id[3]['reason'],'Highest VS score')
        self.assertEqual(by_id[4]['blocked'],'Train cooldown')
        self.assertEqual(by_id[5]['birthday_offset'],16)

    def test_save_includes_every_exact_match_then_ten_closest_above(self):
        players=[player(i,'Player '+str(i)) for i in range(1,16)]
        points={1:99,2:100,3:100,**{i:100+i for i in range(4,16)}}
        roster,ordered=nominations(players,points,{},date(2026,11,6),4,'Save',100)
        self.assertEqual(ordered,[2,3,*range(4,14)])
        self.assertEqual(next(p for p in roster if p['id']==4)['reason'],'4 above the save goal')

    def test_csv_preserves_names_and_validates_each_row(self):
        data=b'\xef\xbb\xbfDate,Conductor Player,VIP Player\n11/6/2026,"Alpha, Jr",Bravo\ninvalid,X,Y\n2026-11-05,C,D\n'
        result=parse_train_csv(data)
        self.assertEqual(result[0],{'line':2,'day':date(2026,11,6),'conductor':'Alpha, Jr','vip':'Bravo'})
        self.assertIn('error',result[1])
        self.assertEqual(result[2]['day'],date(2026,11,5))

    def test_live_award_restrictions_and_historical_import(self):
        db=TrainDatabase()
        app=Mock()
        install_train_manager(app,db)
        endpoints={(r.path,next(iter(r.methods))):r.endpoint for r in app.include_router.call_args.args[0].routes}
        assign=endpoints['/api/train/assignments','POST']
        historical=endpoints['/api/train/history','POST']
        with self.assertRaises(HTTPException) as error:
            assign(Assignment(day=date(2026,11,6),conductor_id=1,vip_id=2))
        self.assertEqual(error.exception.status_code,409)
        historical(Assignment(day=date(2026,11,5),conductor_id=1,vip_id=2))
        with self.assertRaises(HTTPException) as error:
            assign(Assignment(day=date(2026,11,6),conductor_id=1,vip_id=3))
        self.assertEqual(error.exception.status_code,409)
        board=endpoints['/api/train/board','GET'](date(2026,11,6))
        self.assertEqual(board['score_date'],date(2026,11,5))
        self.assertEqual({p['id']:p['blocked'] for p in board['players']},
                         {1:'Train cooldown',2:'Train awards disabled',3:''})

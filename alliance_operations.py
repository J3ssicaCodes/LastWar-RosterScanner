"""Alliance operations and recurring task templates for responsible leaders."""
from datetime import datetime, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator
from alliance_auth import audit, csrf_check, require_account, require_leader, workspace_by_slug
from alliance_leadership import Schedule, materialize, schedule_dates
from alliance_claims import notify

OPERATIONS_SCHEMA=(
    """CREATE TABLE IF NOT EXISTS alliance_operations (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        alliance_id BIGINT UNSIGNED NOT NULL, name VARCHAR(160) NOT NULL,
        description TEXT NOT NULL, instructions TEXT NOT NULL, primary_id BIGINT UNSIGNED NOT NULL,
        archived BOOLEAN NOT NULL DEFAULT FALSE, created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        KEY idx_operations_alliance (alliance_id,archived)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_operation_members (
        alliance_id BIGINT UNSIGNED NOT NULL, operation_id BIGINT UNSIGNED NOT NULL,
        account_id BIGINT UNSIGNED NOT NULL, PRIMARY KEY (operation_id,account_id),
        KEY idx_operation_members (alliance_id,account_id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_operation_roster_members (
        alliance_id BIGINT UNSIGNED NOT NULL, operation_id BIGINT UNSIGNED NOT NULL,
        player_id BIGINT UNSIGNED NOT NULL, responsibility ENUM('primary','secondary') NOT NULL,
        PRIMARY KEY (operation_id,player_id), KEY idx_operation_roster (alliance_id,player_id)
    ) ENGINE=InnoDB""",
    """CREATE TABLE IF NOT EXISTS alliance_operation_templates (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY, alliance_id BIGINT UNSIGNED NOT NULL,
        operation_id BIGINT UNSIGNED NOT NULL, title VARCHAR(160) NOT NULL, description VARCHAR(1000) NOT NULL DEFAULT '',
        schedule_json JSON NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
        KEY idx_operation_templates (alliance_id,operation_id,active)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS alliance_operation_task_links (
        alliance_id BIGINT UNSIGNED NOT NULL, template_id BIGINT UNSIGNED NOT NULL,
        account_id BIGINT UNSIGNED NOT NULL, task_id BIGINT UNSIGNED NOT NULL,
        active BOOLEAN NOT NULL DEFAULT TRUE, assigned_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6), PRIMARY KEY (template_id,account_id),
        UNIQUE KEY uq_operation_task (task_id), KEY idx_operation_task_alliance (alliance_id,active)
    ) ENGINE=InnoDB""",
)

class OperationBody(BaseModel):
    name: str = Field(min_length=1,max_length=160)
    description: str = Field(default='',max_length=4000)
    instructions: str = Field(default='',max_length=20000)
    primary_id: int | None = Field(default=None,gt=0)
    secondary_ids: list[int] = Field(default_factory=list,max_length=5)
    primary_ref: str | None = None
    secondary_refs: list[str] = Field(default_factory=list,max_length=5)
    @model_validator(mode='after')
    def validate_people(self):
        if self.primary_ref is None and self.primary_id is not None:
            self.primary_ref=f'a:{self.primary_id}'
            self.secondary_refs=[f'a:{i}' for i in self.secondary_ids]
        refs=[self.primary_ref,*self.secondary_refs]
        if any(not isinstance(r,str) or not re.fullmatch(r'[ap]:[1-9][0-9]*',r) for r in refs) or len(set(refs))!=len(refs):
            raise ValueError('Choose different leaders for primary and secondary responsibilities.')
        if not self.name.strip():raise ValueError('Enter an operation name.')
        return self

class TemplateBody(BaseModel):
    title: str = Field(min_length=1,max_length=160)
    description: str = Field(default='',max_length=1000)
    schedule: Schedule


def sync_template(cur,workspace,actor,template,people,slug):
    aid=workspace['id'];tid=template['id'];zone=workspace.get('timezone') or 'UTC'
    schedule=template['schedule_json']
    if isinstance(schedule,str):schedule=json.loads(schedule)
    now=datetime.now(timezone.utc)
    cur.execute('SELECT account_id,task_id,active FROM alliance_operation_task_links WHERE alliance_id=%s AND template_id=%s FOR UPDATE',(aid,tid))
    existing={row['account_id']:row for row in cur.fetchall()}
    for account_id,old in existing.items():
        if account_id not in people and old['active']:
            cur.execute('UPDATE alliance_operation_task_links SET active=FALSE WHERE template_id=%s AND account_id=%s AND alliance_id=%s',(tid,account_id,aid))
            cur.execute("DELETE FROM alliance_task_occurrences WHERE alliance_id=%s AND task_id=%s AND status='pending' AND due_at>=UTC_TIMESTAMP(6)",(aid,old['task_id']))
    for account_id in people:
        old=existing.get(account_id)
        if old and old['active']:continue
        if old:
            task_id=old['task_id']
            cur.execute('UPDATE alliance_operation_task_links SET active=TRUE,assigned_at=UTC_TIMESTAMP(6) WHERE template_id=%s AND account_id=%s AND alliance_id=%s',(tid,account_id,aid))
        else:
            cur.execute("""INSERT INTO alliance_tasks (alliance_id,creator_id,assignee_id,title,description,schedule_json,timezone,completion_rule)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'manual')""",(aid,actor['id'],account_id,template['title'],template['description'],json.dumps(schedule),zone))
            task_id=cur.lastrowid
            cur.execute('INSERT INTO alliance_operation_task_links (alliance_id,template_id,account_id,task_id) VALUES (%s,%s,%s,%s)',(aid,tid,account_id,task_id))
        materialize(cur,{'id':task_id,'alliance_id':aid,'assignee_id':account_id,'schedule_json':schedule,'timezone':zone},now,assigned_at=now)
        notify(cur,aid,account_id,'task','An operation task was assigned to you','/account?alliance='+slug)


def operation_refs(cur,aid,oid,primary):
    cur.execute('SELECT account_id FROM alliance_operation_members WHERE alliance_id=%s AND operation_id=%s',(aid,oid))
    secondary=[f"a:{row['account_id']}" for row in cur.fetchall()]
    cur.execute('SELECT player_id,responsibility FROM alliance_operation_roster_members WHERE alliance_id=%s AND operation_id=%s',(aid,oid))
    primary_ref=f'a:{primary}' if primary else None
    for row in cur.fetchall():
        ref=f"p:{row['player_id']}"
        if row['responsibility']=='primary':primary_ref=ref
        else:secondary.append(ref)
    return primary_ref,secondary


def resolve_people(cur,aid,refs,validate=False):
    accounts=[]
    for ref in refs:
        kind,ident=ref.split(':');ident=int(ident)
        if kind=='a':
            cur.execute("SELECT account_id FROM alliance_memberships WHERE alliance_id=%s AND account_id=%s AND role IN ('R4','R5')",(aid,ident))
            row=cur.fetchone()
            if validate and not row:raise HTTPException(422,'Select current R4 or R5 leaders from this alliance.')
            if row:accounts.append(row['account_id'])
        else:
            cur.execute("""SELECT p.id,m.account_id FROM workspace_players p
                LEFT JOIN alliance_player_claims c ON c.alliance_id=p.alliance_id AND c.player_id=p.id
                LEFT JOIN alliance_memberships m ON m.alliance_id=p.alliance_id AND m.account_id=c.account_id AND m.role IN ('R4','R5')
                WHERE p.alliance_id=%s AND p.id=%s AND p.archived=FALSE AND p.player_rank IN ('R4','R5')""",(aid,ident))
            row=cur.fetchone()
            if validate and not row:raise HTTPException(422,'Select current R4 or R5 players from this alliance.')
            if row and row['account_id']:accounts.append(row['account_id'])
    if validate and len(accounts)!=len(set(accounts)):
        raise HTTPException(422,'The same leader was selected more than once.')
    return list(dict.fromkeys(accounts))


def reconcile_operations(cur,aid):
    """Attach operation tasks when approved roster claims become leadership accounts."""
    cur.execute('SELECT id,slug,timezone FROM alliance_workspaces WHERE id=%s',(aid,));w=cur.fetchone()
    if not w:return
    cur.execute('SELECT id,primary_id FROM alliance_operations WHERE alliance_id=%s AND archived=FALSE',(aid,));ops=cur.fetchall()
    for op in ops:
        primary,secondary=operation_refs(cur,aid,op['id'],op['primary_id'])
        refs=([primary] if primary else [])+secondary
        people=resolve_people(cur,aid,refs)
        cur.execute('SELECT * FROM alliance_operation_templates WHERE alliance_id=%s AND operation_id=%s AND active=TRUE',(aid,op['id']))
        for template in cur.fetchall():
            sync_template(cur,w,{'id':people[0] if people else 0},template,people,w['slug'])

def install_operations(app, connect_db):
    router=APIRouter(prefix='/api/auth/alliances/{slug}/operations')
    def authorize(cur,slug,request):
        actor=require_account(connect_db,request)
        workspace=workspace_by_slug(cur,slug)
        require_leader(cur,workspace['id'],actor['id'])
        return actor,workspace
    def operation(cur,workspace,operation_id,lock=False):
        cur.execute('SELECT * FROM alliance_operations WHERE id=%s AND alliance_id=%s AND archived=FALSE'+(' FOR UPDATE' if lock else ''),(operation_id,workspace['id']))
        row=cur.fetchone()
        if not row:raise HTTPException(404,'Operation not found.')
        return row
    def save_responsibilities(cur,aid,oid,body):
        cur.execute('DELETE FROM alliance_operation_members WHERE alliance_id=%s AND operation_id=%s',(aid,oid))
        cur.execute('DELETE FROM alliance_operation_roster_members WHERE alliance_id=%s AND operation_id=%s',(aid,oid))
        for role,ref in [('primary',body.primary_ref),*(('secondary',v) for v in body.secondary_refs)]:
            kind,ident=ref.split(':')
            if kind=='p':cur.execute('INSERT INTO alliance_operation_roster_members (alliance_id,operation_id,player_id,responsibility) VALUES (%s,%s,%s,%s)',(aid,oid,int(ident),role))
            elif role=='secondary':cur.execute('INSERT INTO alliance_operation_members (alliance_id,operation_id,account_id) VALUES (%s,%s,%s)',(aid,oid,int(ident)))
    @router.get('')
    def list_operations(slug:str,request:Request):
        with connect_db() as conn,conn.cursor() as cur:
            actor,w=authorize(cur,slug,request)
            cur.execute("""SELECT p.id,p.player_name,p.player_rank,p.away_start,p.away_end,m.account_id FROM workspace_players p
                LEFT JOIN alliance_player_claims c ON c.alliance_id=p.alliance_id AND c.player_id=p.id
                LEFT JOIN alliance_memberships m ON m.alliance_id=p.alliance_id AND m.account_id=c.account_id AND m.role IN ('R4','R5')
                WHERE p.alliance_id=%s AND p.archived=FALSE AND p.player_rank IN ('R4','R5') ORDER BY p.player_name""",(w['id'],))
            players=cur.fetchall()
            try:today=datetime.now(ZoneInfo(w.get('timezone') or 'UTC')).date()
            except (ZoneInfoNotFoundError,ValueError):today=datetime.now(timezone.utc).date()
            leaders=[{'id':f"p:{p['id']}",'display_name':p['player_name'],'role':p['player_rank'],
                      'has_account':bool(p['account_id']),'away_start':p['away_start'],'away_end':p['away_end'],
                      'away_today':bool(p['away_start'] and p['away_end'] and p['away_start']<=today<=p['away_end'])} for p in players]
            linked={p['account_id']:f"p:{p['id']}" for p in players if p['account_id']}
            cur.execute("""SELECT a.id,a.username,a.display_name,m.role FROM alliance_memberships m
                JOIN alliance_accounts a ON a.id=m.account_id WHERE m.alliance_id=%s AND m.role IN ('R4','R5') ORDER BY a.display_name""",(w['id'],))
            leaders.extend({'id':f"a:{a['id']}",'display_name':a['display_name'],'role':a['role'],'has_account':True} for a in cur.fetchall() if a['id'] not in linked)
            cur.execute('SELECT id,name,description,instructions,primary_id FROM alliance_operations WHERE alliance_id=%s AND archived=FALSE ORDER BY name,id',(w['id'],));ops=cur.fetchall()
            cur.execute('SELECT id,operation_id,title,description,schedule_json FROM alliance_operation_templates WHERE alliance_id=%s AND active=TRUE ORDER BY id',(w['id'],));templates=cur.fetchall()
            for op in ops:
                primary,secondary=operation_refs(cur,w['id'],op['id'],op['primary_id'])
                def visible(ref):
                    return linked.get(int(ref[2:]),ref) if ref and ref.startswith('a:') else ref
                op['primary_ref']=visible(primary)
                op['secondary_refs']=[visible(ref) for ref in secondary]
                op['templates']=[{**r,'schedule':json.loads(r['schedule_json']) if isinstance(r['schedule_json'],str) else r['schedule_json']} for r in templates if r['operation_id']==op['id']]
                for t in op['templates']:t.pop('schedule_json',None)
            return {'operations':ops,'leaders':leaders,'timezone':w.get('timezone') or 'UTC'}
    @router.post('')
    def create_operation(slug:str,body:OperationBody,request:Request):
        csrf_check(request)
        with connect_db() as conn,conn.cursor() as cur:
            actor,w=authorize(cur,slug,request);resolve_people(cur,w['id'],[body.primary_ref,*body.secondary_refs],True)
            primary=int(body.primary_ref[2:]) if body.primary_ref.startswith('a:') else 0
            cur.execute('INSERT INTO alliance_operations (alliance_id,name,description,instructions,primary_id) VALUES (%s,%s,%s,%s,%s)',(w['id'],body.name.strip(),body.description.strip(),body.instructions.strip(),primary));oid=cur.lastrowid
            save_responsibilities(cur,w['id'],oid,body)
            audit(cur,w['id'],actor,'operation_created',{'operation_id':oid});conn.commit()
        return {'id':oid}
    @router.put('/{operation_id}')
    def update_operation(slug:str,operation_id:int,body:OperationBody,request:Request):
        csrf_check(request)
        with connect_db() as conn,conn.cursor() as cur:
            actor,w=authorize(cur,slug,request);operation(cur,w,operation_id,True);people=resolve_people(cur,w['id'],[body.primary_ref,*body.secondary_refs],True)
            primary=int(body.primary_ref[2:]) if body.primary_ref.startswith('a:') else 0
            cur.execute('UPDATE alliance_operations SET name=%s,description=%s,instructions=%s,primary_id=%s WHERE id=%s AND alliance_id=%s',(body.name.strip(),body.description.strip(),body.instructions.strip(),primary,operation_id,w['id']))
            save_responsibilities(cur,w['id'],operation_id,body)
            cur.execute('SELECT * FROM alliance_operation_templates WHERE alliance_id=%s AND operation_id=%s AND active=TRUE FOR UPDATE',(w['id'],operation_id))
            for template in cur.fetchall():sync_template(cur,w,actor,template,people,slug)
            audit(cur,w['id'],actor,'operation_updated',{'operation_id':operation_id});conn.commit()
        return {'ok':True}
    @router.post('/{operation_id}/templates')
    def create_template(slug:str,operation_id:int,body:TemplateBody,request:Request):
        csrf_check(request)
        if not body.title.strip():raise HTTPException(422,'Enter a task title.')
        schedule=body.schedule.model_dump(mode='json')
        with connect_db() as conn,conn.cursor() as cur:
            actor,w=authorize(cur,slug,request);op=operation(cur,w,operation_id,True)
            try:list(schedule_dates(schedule,w.get('timezone') or 'UTC',datetime.now(timezone.utc)-timedelta(days=14),datetime.now(timezone.utc)+timedelta(days=90)))
            except (ValueError,OverflowError) as exc:raise HTTPException(422,str(exc)) from exc
            cur.execute('INSERT INTO alliance_operation_templates (alliance_id,operation_id,title,description,schedule_json) VALUES (%s,%s,%s,%s,%s)',(w['id'],operation_id,body.title.strip(),body.description.strip(),json.dumps(schedule)));tid=cur.lastrowid
            primary,secondary=operation_refs(cur,w['id'],operation_id,op['primary_id'])
            sync_template(cur,w,actor,{'id':tid,'title':body.title.strip(),'description':body.description.strip(),'schedule_json':schedule},resolve_people(cur,w['id'],([primary] if primary else [])+secondary),slug)
            audit(cur,w['id'],actor,'operation_template_created',{'operation_id':operation_id,'template_id':tid});conn.commit()
        return {'id':tid}
    @router.delete('/{operation_id}/templates/{template_id}')
    def remove_template(slug:str,operation_id:int,template_id:int,request:Request):
        csrf_check(request)
        with connect_db() as conn,conn.cursor() as cur:
            actor,w=authorize(cur,slug,request);operation(cur,w,operation_id,True)
            cur.execute('SELECT * FROM alliance_operation_templates WHERE id=%s AND alliance_id=%s AND operation_id=%s AND active=TRUE FOR UPDATE',(template_id,w['id'],operation_id));template=cur.fetchone()
            if not template:raise HTTPException(404,'Task template not found.')
            sync_template(cur,w,actor,template,[],slug)
            cur.execute('UPDATE alliance_operation_templates SET active=FALSE WHERE id=%s AND alliance_id=%s',(template_id,w['id']))
            audit(cur,w['id'],actor,'operation_template_removed',{'operation_id':operation_id,'template_id':template_id});conn.commit()
        return {'ok':True}
    @router.put('/{operation_id}/templates/{template_id}')
    def update_template(slug:str,operation_id:int,template_id:int,body:TemplateBody,request:Request):
        csrf_check(request)
        if not body.title.strip():raise HTTPException(422,'Enter a task title.')
        schedule=body.schedule.model_dump(mode='json');now=datetime.now(timezone.utc)
        with connect_db() as conn,conn.cursor() as cur:
            actor,w=authorize(cur,slug,request);operation(cur,w,operation_id,True)
            cur.execute('SELECT id FROM alliance_operation_templates WHERE id=%s AND alliance_id=%s AND operation_id=%s AND active=TRUE FOR UPDATE',(template_id,w['id'],operation_id))
            if not cur.fetchone():raise HTTPException(404,'Task template not found.')
            try:list(schedule_dates(schedule,w.get('timezone') or 'UTC',now-timedelta(days=14),now+timedelta(days=90)))
            except (ValueError,OverflowError) as exc:raise HTTPException(422,str(exc)) from exc
            cur.execute('UPDATE alliance_operation_templates SET title=%s,description=%s,schedule_json=%s WHERE id=%s AND alliance_id=%s',(body.title.strip(),body.description.strip(),json.dumps(schedule),template_id,w['id']))
            cur.execute('SELECT task_id,account_id,assigned_at FROM alliance_operation_task_links WHERE alliance_id=%s AND template_id=%s FOR UPDATE',(w['id'],template_id))
            for link in cur.fetchall():
                cur.execute('UPDATE alliance_tasks SET title=%s,description=%s,schedule_json=%s,timezone=%s WHERE id=%s AND alliance_id=%s',(body.title.strip(),body.description.strip(),json.dumps(schedule),w.get('timezone') or 'UTC',link['task_id'],w['id']))
                cur.execute("DELETE FROM alliance_task_occurrences WHERE alliance_id=%s AND task_id=%s AND status='pending' AND due_at>=UTC_TIMESTAMP(6)",(w['id'],link['task_id']))
                cur.execute('SELECT active FROM alliance_operation_task_links WHERE task_id=%s AND alliance_id=%s',(link['task_id'],w['id']))
                if cur.fetchone()['active']:
                    materialize(cur,{'id':link['task_id'],'alliance_id':w['id'],'assignee_id':link['account_id'],'schedule_json':schedule,'timezone':w.get('timezone') or 'UTC'},now,assigned_at=link['assigned_at'])
            audit(cur,w['id'],actor,'operation_template_updated',{'operation_id':operation_id,'template_id':template_id});conn.commit()
        return {'ok':True}
    app.include_router(router)

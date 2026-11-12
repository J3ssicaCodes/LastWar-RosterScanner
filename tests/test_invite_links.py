import os
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

from fastapi import HTTPException, Response
import alliance_auth as auth


class InviteDB:
    def __init__(self):
        self.invites=[]; self.links={}; self.lastrowid=0; self.rows=[]
    def __call__(self): return self
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def cursor(self): return self
    def commit(self): pass
    def execute(self,sql,args=()):
        if 'INSERT INTO alliance_invites' in sql:
            digest,kind,alliance_id,issuer,expires=args
            self.lastrowid+=1
            self.invites.append(dict(id=self.lastrowid,token_hash=digest,kind=kind,alliance_id=alliance_id,
                issuer_id=issuer,expires_at=expires,revoked_at=None,used_at=None,use_count=0,created_at=auth.utcnow()))
        elif 'INSERT INTO alliance_invite_links' in sql:
            self.links[args[0]]=args[1]
        elif 'LEFT JOIN alliance_invite_links' in sql:
            self.rows=[{**row,'encrypted_token':self.links.get(row['id'])} for row in self.invites
                       if row['alliance_id']==args[0]]
    def fetchall(self): return [dict(row) for row in self.rows]


class InviteTests(unittest.TestCase):
    def setUp(self):
        self.db=InviteDB(); app=Mock(); auth.install_accounts(app,self.db)
        routes=app.include_router.call_args.args[0].routes
        self.create=next(r.endpoint for r in routes if r.path=='/api/auth/invites')
        self.list=next(r.endpoint for r in routes if r.path=='/api/auth/alliances/{slug}/invites')
        self.env=patch.dict(os.environ,{'ALLIANCE_INVITE_LINK_SECRET':'test-secret-not-production'})
        self.env.start();self.addCleanup(self.env.stop)
        self.ctx=[patch.object(auth,'csrf_check'),patch.object(auth,'require_account',return_value={'id':42,'platform_admin':False}),
                  patch.object(auth,'workspace_by_slug',return_value={'id':7,'slug':'1439-rvby'}),
                  patch.object(auth,'membership',return_value='R4'),patch.object(auth,'audit')]
        for p in self.ctx:p.start();self.addCleanup(p.stop)

    def test_r4_can_create_and_retrieve_reusable_link(self):
        created=self.create(auth.InviteRequest(kind='leadership',alliance_slug='1439-rvby'),Mock())
        with patch.object(auth,'require_leader',return_value='R4'):
            response=Response(); data=self.list('1439-rvby',Mock(),response)
        self.assertEqual(data['invites'][0]['code'],created['code'])
        self.assertNotIn(created['code'].encode(),self.db.links[created['id']])
        self.assertNotIn('token_hash',data['invites'][0])
        self.assertNotIn('encrypted_token',data['invites'][0])
        self.assertEqual(response.headers['cache-control'],'private, no-store')

    def test_r3_cannot_create_or_retrieve(self):
        with patch.object(auth,'membership',return_value='R3'):
            with self.assertRaises(HTTPException) as error:
                self.create(auth.InviteRequest(kind='leadership',alliance_slug='1439-rvby'),Mock())
            self.assertEqual(error.exception.status_code,403)
        with patch.object(auth,'require_leader',side_effect=HTTPException(403)):
            with self.assertRaises(HTTPException):self.list('1439-rvby',Mock(),Response())

    def test_legacy_expired_and_revoked_tokens_are_never_recovered(self):
        created=self.create(auth.InviteRequest(kind='leadership',alliance_slug='1439-rvby'),Mock())
        row=self.db.invites[0]
        with patch.object(auth,'require_leader',return_value='R4'):
            self.db.links.clear()
            self.assertNotIn('code',self.list('1439-rvby',Mock(),Response())['invites'][0])
            self.db.links[created['id']]=auth.invite_cipher().encrypt(created['code'].encode())
            row['expires_at']=auth.utcnow()-timedelta(seconds=1)
            self.assertNotIn('code',self.list('1439-rvby',Mock(),Response())['invites'][0])
            row['expires_at']=auth.utcnow()+timedelta(days=1);row['revoked_at']=auth.utcnow()
            self.assertNotIn('code',self.list('1439-rvby',Mock(),Response())['invites'][0])


if __name__=='__main__':unittest.main()

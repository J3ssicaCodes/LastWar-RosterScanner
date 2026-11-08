"""Destructive end-to-end rehearsal against a local restored copy only.

Restore a Rubies backup into an isolated localhost database whose name ends in
_rehearsal. Set DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME and
ALLIANCE_TEST_BACKUP_SHA256, then run this script from the repository root.
The test creates workspaces, changes data and exercises Clear Data; never point
it at a production database. It uses deterministic mocked OCR, not real media.
"""
import os
from pathlib import Path
import sys
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
database=os.environ.get("DB_NAME", "")
if (os.environ.get("DB_HOST") not in ("127.0.0.1", "localhost", "::1")
        or not database.endswith("_rehearsal")
        or any(os.environ.get(name) for name in ("DATABASE_URL", "MYSQL_URL", "MYSQL_DATABASE_URL", "DB_URL", "DB_CONNECTION_STRING"))):
    raise SystemExit("Use a dedicated local database ending in _rehearsal and no database URL.")
setup="test-private-setup-code-12345678901234567890"
os.environ.update({"ALLIANCE_WORKSPACE_MODE":"true", "ALLIANCE_WRITE_PAUSED":"true",
    "ALLIANCE_EXPECTED_DATABASE":database, "ALLIANCE_BACKUP_SHA256":os.environ["ALLIANCE_TEST_BACKUP_SHA256"],
    "ALLIANCE_BOOTSTRAP_PASSWORD":"admin", "ALLIANCE_BOOTSTRAP_CODE":setup})

from app import app
origin={"Origin":"http://testserver"}
with TestClient(app,raise_server_exceptions=True) as admin:
    assert admin.get("/a/1439-rvby").status_code==200
    assert admin.get("/api/player-list").status_code==401
    assert admin.get("/a/1439-rvby/api/player-list").status_code==403
    result=admin.post("/api/auth/login",headers=origin,json={"username":"admin","password":"admin","setup_code":setup})
    assert result.status_code==200,(result.status_code,result.text)
    assert admin.post("/api/auth/logout",headers=origin,json={}).status_code==200
    assert admin.post("/api/auth/login",headers=origin,json={"username":"admin","password":"admin"}).status_code==200
    me=admin.get("/api/auth/me")
    assert me.json()["platform_admin"] and me.json()["workspaces"][0]["role"]=="R5"
    assert admin.get("/api/auth/alliances/1439-rvby/analytics").status_code==200
    rubies_count=len(admin.get("/a/1439-rvby/api/player-list").json()["players"])
    for path in ["/a/1439-rvby/api/player-list",
                 "/a/1439-rvby/api/vs-points/weekly?week_start=2026-11-02",
                 "/a/1439-rvby/api/train/history",
                 "/a/1439-rvby/api/train/board?day=2026-11-07",
                 "/a/1439-rvby/api/train/settings",
                 "/a/1439-rvby/api/train/restrictions",
                 "/a/1439-rvby/api/train/email-templates",
                 "/a/1439-rvby/api/player-performance?week_start=2026-11-02",
                 "/a/1439-rvby/api/player-list/name-dictionary",
                 "/a/1439-rvby/api/admin/name-dictionary",
                 "/a/1439-rvby/api/media/pending",
                 "/a/1439-rvby/api/media/missing-players",
                 "/a/1439-rvby/api/media/archived-players",
                 "/a/1439-rvby/api/vs-points/pending",
                 "/a/1439-rvby/api/vs-points/export.csv?week_start=2026-11-02",
                 "/a/1439-rvby/api/media/jobs",
                 "/a/1439-rvby/api/admin/tables"]:
        result=admin.get(path)
        print(path,result.status_code)
        assert result.status_code==200,(path,result.text)
    result=admin.post("/api/auth/invites",headers=origin,json={"kind":"referral"})
    assert result.status_code==200,result.text
    referral=result.json()["code"]
    with TestClient(app,raise_server_exceptions=True) as pilot:
        result=pilot.post("/api/auth/register",headers=origin,json={"code":referral,"username":"pilotowner",
            "password":"test-password-1234","display_name":"Pilot Owner"})
        assert result.status_code==200,result.text
        result=pilot.post("/api/auth/create-alliance",headers=origin,json={"code":referral,
            "name":"Pilot","tag":"TEST","server":1440})
        assert result.status_code==200,result.text
        assert pilot.get("/a/1439-rvby/api/player-list").status_code==403
        assert admin.get("/a/1440-test/api/player-list").status_code==403
        result=pilot.get("/a/1440-test/api/player-list")
        assert result.status_code==200 and not result.json()["players"],result.text
        result=pilot.post("/a/1440-test/api/player-list/import",headers=origin,
            json={"players":[{"name":"Lady Jess","rank":"R4","power":123}],"full_capture":False})
        assert result.status_code==200,(result.status_code,result.text)
        assert len(pilot.get("/a/1440-test/api/player-list").json()["players"])==1
        rubies=admin.get("/a/1439-rvby/api/player-list").json()["players"]
        assert len(rubies)==rubies_count
        from io import BytesIO
        from PIL import Image
        def test_image(color):
            picture=BytesIO()
            Image.new("RGB",(30,60),color).save(picture,"PNG")
            return picture.getvalue()
        assert pilot.get("/api/auth/avatar").status_code==404
        assert admin.post("/api/auth/avatar",headers=origin,files={"picture":("one.png",test_image("red"),"image/png")}).status_code==200
        admin_avatar=admin.get("/api/auth/avatar")
        assert admin_avatar.status_code==200 and admin_avatar.headers["content-type"]=="image/jpeg"
        with Image.open(BytesIO(admin_avatar.content)) as picture:
            assert picture.size==(512,512)
        assert pilot.get("/api/auth/avatar").status_code==404
        assert pilot.post("/api/auth/avatar",headers={"Origin":"https://wrong.example"},files={"picture":("two.png",test_image("blue"),"image/png")}).status_code==403
        assert pilot.post("/api/auth/avatar",headers=origin,files={"picture":("two.png",test_image("blue"),"image/png")}).status_code==200
        assert pilot.get("/api/auth/avatar").content!=admin_avatar.content
        assert pilot.delete("/api/auth/avatar",headers=origin).status_code==200
        assert pilot.get("/api/auth/avatar").status_code==404 and admin.get("/api/auth/avatar").content==admin_avatar.content
        assert pilot.post("/api/auth/avatar",headers=origin,files={"picture":("bad.png",b"not an image","image/png")}).status_code==422
        print("Profile pictures are private to each account")
        settings=pilot.get("/a/1440-test/api/train/settings")
        assert settings.status_code==200 and settings.json()["alliance_name"]=="Pilot",settings.text
        templates=pilot.get("/a/1440-test/api/train/email-templates")
        assert templates.status_code==200 and len(templates.json()["templates"])>=20,templates.text
        pilot_id=pilot.get("/a/1440-test/api/player-list").json()["players"][0]["id"]
        rubies_history=admin.get("/a/1439-rvby/api/train/history").json()
        assignment=pilot.post("/a/1440-test/api/train/assignments",headers=origin,json={"day":"2026-11-07","conductor_id":pilot_id,"vip_method":"dice"})
        assert assignment.status_code==200,(assignment.status_code,assignment.text)
        assert admin.get("/a/1439-rvby/api/train/history").json()==rubies_history
        assert "Lady Jess" in str(pilot.get("/a/1440-test/api/train/history").json())
        print("New alliance defaults and same-day train isolation passed")
        import media_upload, time
        media_upload.scan_storm_upload=lambda paths, report: {"players":[{"team":"A","name":"Lady Jess","rank":"R4","role":"Participant","power":"1M"}]}
        result=pilot.post("/a/1440-test/api/media/upload?destination=storm",headers=origin,files={"files":("test.png",b"fake image","image/png")})
        assert result.status_code==202,(result.status_code,result.text)
        job_id=result.json()["job_id"]
        for _ in range(100):
            jobs=pilot.get("/a/1440-test/api/media/jobs").json()["jobs"]
            job=next(j for j in jobs if j["id"]==job_id)
            if job["status"] not in ("queued","scanning"): break
            time.sleep(.05)
        assert job["status"]=="ready",job
        storm=pilot.get("/a/1440-test/api/media/storm-roster").json()
        assert "Lady Jess" in str(storm),storm
        audit_rows=pilot.get("/api/auth/alliances/1440-test/audit").json()
        assert job_id in str(audit_rows) and "Pilot Owner" in str(audit_rows),audit_rows
        assert job_id not in str(admin.get("/a/1439-rvby/api/media/jobs").json())
        assert admin.get("/a/1439-rvby/api/media/jobs/"+job_id+"/diagnostics").status_code==404
        assert pilot.get("/a/1439-rvby/api/media/jobs/"+job_id+"/diagnostics").status_code==403
        before=admin.get("/a/1439-rvby/api/media/storm-roster").json()
        result=pilot.post("/a/1440-test/api/admin/clear-data",headers=origin,json={"confirmation":"CLEAR DATA","tables":["storm roster"]})
        assert result.status_code==200,(result.status_code,result.text)
        assert admin.get("/a/1439-rvby/api/media/storm-roster").json()==before
        assert "Lady Jess" not in str(pilot.get("/a/1440-test/api/media/storm-roster").json())
        assert "clear-data" in str(pilot.get("/api/auth/alliances/1440-test/audit").json())
        print("Upload worker and clear-data isolation passed")
        from datetime import date
        media_upload.scan_vs_upload=lambda *args,**kwargs: {"score_date":date(2026,11,7),"upload_date":date(2026,11,7),"weekday":"Sat","rows":[{"name":"Lady Jess","points":1234567,"review_reasons":[]}]}
        media_upload.recheck_vs_rows=lambda *args,**kwargs: None
        rubies_vs=admin.get("/a/1439-rvby/api/vs-points?score_date=2026-11-07").json()
        result=pilot.post("/a/1440-test/api/media/upload?destination=vs&upload_date=2026-11-07",headers=origin,files={"files":("vs.png",b"fake image","image/png")})
        assert result.status_code==202,(result.status_code,result.text)
        vs_job=result.json()["job_id"]
        for _ in range(100):
            vs_status=next(j for j in pilot.get("/a/1440-test/api/media/jobs").json()["jobs"] if j["id"]==vs_job)
            if vs_status["status"] not in ("queued","scanning"): break
            time.sleep(.05)
        assert vs_status["status"]=="ready",vs_status
        assert "1234567" in str(pilot.get("/a/1440-test/api/vs-points?score_date=2026-11-07").json())
        assert admin.get("/a/1439-rvby/api/vs-points?score_date=2026-11-07").json()==rubies_vs
        print("Same-day VS upload isolation passed")
        result=pilot.post("/api/auth/invites",headers=origin,json={"kind":"claim","alliance_slug":"1440-test"})
        assert result.status_code==200,result.text
        code=result.json()["code"]
        with TestClient(app,raise_server_exceptions=True) as claimant:
            result=claimant.post("/api/auth/register",headers=origin,json={"code":code,"username":"claimant",
                "password":"claimant-password-123","display_name":"Claimant"})
            assert result.status_code==200,result.text
            result=claimant.post("/api/auth/claims/roster",headers=origin,json={"code":code})
            assert result.status_code==200,result.text
            player_id=result.json()["players"][0]["id"]
            from io import BytesIO
            from PIL import Image
            image=BytesIO()
            Image.new("RGB",(40,40),"green").save(image,"PNG")
            result=claimant.post("/api/auth/claims",headers=origin,data={"code":code,
                "player_id":player_id,"birthday":"02-29","note":"Green row"},
                files={"screenshot":("proof.png",image.getvalue(),"image/png")})
            assert result.status_code==200,result.text
            claim_id=result.json()["id"]
            assert admin.get(f"/api/auth/alliances/1440-test/claims/{claim_id}/proof").status_code==403
            result=pilot.get("/api/auth/alliances/1440-test/claims")
            assert result.status_code==200 and result.json()["claims"][0]["birthday"]=="02-29",result.text
            result=pilot.post(f"/api/auth/alliances/1440-test/claims/{claim_id}/decision",
                headers=origin,json={"action":"approve"})
            assert result.status_code==200,result.text
            result=claimant.patch("/api/auth/my-birthday",headers=origin,
                json={"alliance_slug":"1440-test","birthday":"05-07"})
            assert result.status_code==200,result.text
            data=pilot.get("/api/public/1440-test").json()
            assert "birthday" not in str(data).lower()
        assert pilot.get("/api/public/1439-rvby").status_code==200
        result=pilot.post("/api/auth/alliances/1440-test/wall",headers=origin,json={"body":"Welcome"})
        assert result.status_code==200,result.text
        assert len(pilot.get("/api/auth/alliances/1440-test/wall").json()["posts"])==1
        assert admin.get("/api/auth/alliances/1440-test/wall").status_code==403
        result=pilot.post("/api/auth/alliances/1440-test/tasks",headers=origin,json={"title":"Post train",
            "assignee":"pilotowner","schedule":{"kind":"weekly","start_date":"2026-11-07",
            "due_time":"18:00","weekdays":[0,3]}})
        assert result.status_code==200,result.text
        tasks=pilot.get("/api/auth/alliances/1440-test/tasks?mine=true")
        assert tasks.status_code==200 and tasks.json()["occurrences"],tasks.text
        assert pilot.get("/api/auth/alliances/1440-test/inbox").status_code==200
        result=pilot.post("/api/auth/invites",headers=origin,json={"kind":"leadership","alliance_slug":"1440-test"})
        assert result.status_code==200,result.text
        leadership_code=result.json()["code"]
        with TestClient(app,raise_server_exceptions=True) as r4:
            result=r4.post("/api/auth/register",headers=origin,json={"code":leadership_code,
                "username":"pilotleaders","password":"leader-password-123","display_name":"Pilot R4"})
            assert result.status_code==200,result.text
            assert r4.get("/a/1440-test/api/player-list").status_code==200
            assert r4.post("/a/1440-test/api/admin/clear-data",headers=origin,
                json={"confirmation":"CLEAR DATA","tables":["player list"]}).status_code==403
            assert r4.get("/a/1439-rvby/api/player-list").status_code==403
            assert r4.get("/api/auth/alliances/1440-test/audit").status_code==200
            assert r4.post("/api/auth/alliances/1440-test/transfer-r5",headers=origin,
                json={"username":"pilotowner"}).status_code==403
        result=pilot.post("/api/auth/alliances/1440-test/transfer-r5",headers=origin,
            json={"username":"pilotleaders"})
        assert result.status_code==200,result.text
        roles=pilot.get("/api/auth/me").json()["workspaces"]
        assert roles[0]["role"]=="R4"
    result=admin.post("/api/auth/invites",headers=origin,json={"kind":"leadership","alliance_slug":"1439-rvby"})
    assert result.status_code==200,result.text
    with TestClient(app,raise_server_exceptions=True) as successor:
        result=successor.post("/api/auth/register",headers=origin,json={"code":result.json()["code"],
            "username":"rubiesleader","password":"leader-password-123","display_name":"New R5"})
        assert result.status_code==200,result.text
    result=admin.post("/api/auth/alliances/1439-rvby/transfer-r5",headers=origin,json={"username":"rubiesleader"})
    assert result.status_code==200,result.text
    me=admin.get("/api/auth/me").json()
    assert me["platform_admin"] and me["workspaces"][0]["role"]=="R4"
    result=admin.post("/api/auth/change-password",headers=origin,json={"current_password":"admin",
        "new_password":"test-owner-password-1234"})
    assert result.status_code==200,result.text
    print("Two workspace membership and optional password change passed")

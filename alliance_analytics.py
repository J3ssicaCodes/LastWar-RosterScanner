"""Leadership-only VS and train summaries derived from alliance records."""

from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter,Request

from alliance_auth import require_account,require_leader,workspace_by_slug


GOAL=7200000


def install_analytics(app,connect_db):
    router=APIRouter(prefix="/api/auth/alliances/{slug}")

    @router.get("/analytics")
    def analytics(slug: str,request: Request):
        actor=require_account(connect_db,request)
        with connect_db() as conn, conn.cursor() as cur:
            workspace=workspace_by_slug(cur,slug)
            require_leader(cur,workspace["id"],actor["id"])
            cur.execute("SELECT timezone FROM alliance_workspaces WHERE id=%s",(workspace["id"],))
            today=datetime.now(ZoneInfo(cur.fetchone()["timezone"])).date()
            monday=today-timedelta(days=today.weekday())
            first=monday-timedelta(weeks=3)
            cur.execute("""SELECT id,player_name,normalized_name FROM workspace_players
                WHERE alliance_id=%s AND archived=FALSE ORDER BY player_name""",(workspace["id"],))
            players=cur.fetchall()
            cur.execute("""SELECT score_date,player_id,normalized_name,player_name,points FROM workspace_vs_points
                WHERE alliance_id=%s AND score_date BETWEEN %s AND %s""",
                (workspace["id"],first,monday+timedelta(days=5)))
            scores=cur.fetchall()
            cur.execute("""SELECT assignment_date,conductor_id,vip_id,conductor_name,vip_name,
                conductor_reason,vip_reason,goal,target FROM workspace_train_assignments
                WHERE alliance_id=%s ORDER BY assignment_date DESC""",(workspace["id"],))
            trains=cur.fetchall()
            cur.execute("""SELECT player_id,reason FROM workspace_train_restrictions WHERE alliance_id=%s""",
                        (workspace["id"],))
            restricted={row["player_id"]:row["reason"] for row in cur.fetchall()}

        by_id={row["id"]:row for row in players}
        by_name={row["normalized_name"]:row["id"] for row in players}
        counts={player["id"]:[0,0,0,0] for player in players}
        totals={player["id"]:[0,0,0,0] for player in players}
        recorded={}
        for row in scores:
            player_id=row["player_id"] if row["player_id"] in by_id else by_name.get(row["normalized_name"])
            if player_id is None:continue
            offset=(row["score_date"]-first).days//7
            if offset not in range(4):continue
            counts[player_id][offset]+=1
            totals[player_id][offset]+=int(row["points"])
            recorded[(player_id,row["score_date"])]=int(row["points"])
        completed=min(today.weekday(),6)
        current_days=[monday+timedelta(days=i) for i in range(completed)]
        previous_days=[day-timedelta(days=7) for day in current_days]
        current=sum(recorded.get((p["id"],day),0) for p in players for day in current_days)
        previous=sum(recorded.get((p["id"],day),0) for p in players for day in previous_days)
        missing=[{"date":day.isoformat(),"players":[p["player_name"] for p in players
                  if (p["id"],day) not in recorded]} for day in current_days]
        current_recorded=sum((p["id"],day) in recorded for p in players for day in current_days)
        current_values=[recorded[(p["id"],day)] for p in players for day in current_days
                        if (p["id"],day) in recorded]
        goal_by_date={row["assignment_date"]-timedelta(days=1):row["goal"] for row in trains}
        save=[]
        push=[]
        for day in current_days:
            values=[recorded[(p["id"],day)] for p in players if (p["id"],day) in recorded]
            if goal_by_date.get(day)=="Save":
                save.append({"date":day.isoformat(),"hits":sum(value>=GOAL for value in values),
                    "shortfalls":sum(value<GOAL for value in values),
                    "overage":sum(max(0,value-GOAL) for value in values),
                    "shortfall_points":sum(max(0,GOAL-value) for value in values)})
            elif goal_by_date.get(day)=="Push":
                push.append({"date":day.isoformat(),"total":sum(values),
                    "contributors":sum(value>0 for value in values),"recorded":len(values)})
        trends=[]
        for player in players:
            player_id=player["id"]
            trends.append({"id":player_id,"name":player["player_name"],
                "weekly_totals":totals[player_id],"recorded_days":counts[player_id],
                "change":totals[player_id][3]-totals[player_id][2]
                    if counts[player_id][3] and counts[player_id][2] else None})
        fairness=[]
        for player in players:
            player_id=player["id"]
            conductor=[row for row in trains if row["conductor_id"]==player_id]
            vip=[row for row in trains if row["vip_id"]==player_id]
            fairness.append({"id":player_id,"name":player["player_name"],
                "last_conductor":conductor[0]["assignment_date"].isoformat() if conductor else None,
                "last_vip":vip[0]["assignment_date"].isoformat() if vip else None,
                "awards_30":sum(row["assignment_date"]>=today-timedelta(days=30) for row in conductor+vip),
                "awards_90":sum(row["assignment_date"]>=today-timedelta(days=90) for row in conductor+vip),
                "eligible":player_id not in restricted,"ineligibility_reason":restricted.get(player_id),
                "latest_conductor_reason":conductor[0]["conductor_reason"] if conductor else None,
                "latest_vip_reason":vip[0]["vip_reason"] if vip else None})
        return {"week_start":monday.isoformat(),"completed_days":completed,
            "current_points":current,"previous_points":previous,"scores_recorded":current_recorded,
            "scores_expected":len(players)*completed,"recorded_zeroes":sum(v==0 for v in current_values),
            "below_goal":sum(0<v<GOAL for v in current_values),
            "missing":missing,"save_days":save,"push_days":push,
            "trends":trends,"fairness":fairness}

    app.include_router(router)

"""Alliance performance calculated from recorded scores and stat observations."""
from collections import defaultdict
from datetime import date, timedelta

STAT_FIELDS = ('power','power_millions','kills','donations')
HISTORY_FIELDS = ('DATE(h.recorded_at) AS recorded_date,h.power,h.power_millions,h.kills,h.donations,'
                  'h.power_upload_date,h.power_millions_upload_date,h.kills_upload_date,h.donations_upload_date')


def stat_series(history):
    """Use upload dates when available and preserve pre-migration observations."""
    series = {key:{} for key in ('power','kills','donations')}
    previous = {}
    for row in history:
        legacy = all(row[f'{field}_upload_date'] is None for field in STAT_FIELDS)
        for field in STAT_FIELDS:
            value = row[field]
            sample_date = row[f'{field}_upload_date']
            if legacy and value is not None and previous.get(field) != value:
                sample_date = row['recorded_date']
            if value is not None:
                previous[field] = value
            if value is None or sample_date is None:
                continue
            if field == 'power_millions':
                series['power'].setdefault(str(sample_date),int(value * 1000000))
            else:
                series[field][str(sample_date)] = int(value)
    return series


def change_for_week(samples, monday, saturday):
    observations = sorted((day,value) for day,value in samples.items() if day <= saturday)
    if len(observations) < 2 or observations[-1][0] < monday:
        return None
    baseline = next((value for day,value in reversed(observations[:-1]) if day < monday),
                    observations[0][1])
    return observations[-1][1] - baseline


def build_performance(monday, players, score_rows, history):
    # A score keyed to the roster id takes priority over older name-only rows.
    scores = defaultdict(dict)
    for row in score_rows:
        day = row['score_date']
        if day.weekday() < 6:
            scores[day][row['roster_id']] = scores[day].get(row['roster_id'],int(row['points']))
    days = [monday + timedelta(days=i) for i in range(6)]
    captured = [day for day in days if scores[day]]
    tracked = []
    for player in players:
        pid = player['id']
        current = {day:scores[day][pid] for day in captured if pid in scores[day]}
        prior = {day:scores[day-timedelta(days=7)][pid] for day in current
                 if pid in scores[day-timedelta(days=7)]}
        trends = stat_series(history[pid])
        tracked.append({
            'id':pid, 'name':player['name'], 'rank':player['rank'],
            'vs_points':str(sum(current.values())) if current else None,
            'days_recorded':len(current),
            'vs_change':str(sum(current[day]-prior[day] for day in prior)) if prior else None,
            'days_compared':len(prior),
            **{f'{key}_change':None if (change:=change_for_week(values,days[0].isoformat(),days[-1].isoformat())) is None
               else str(change) for key,values in trends.items()},
        })

    weekly = []
    for offset in range(7,-1,-1):
        start = monday-timedelta(weeks=offset)
        captures = [scores[start+timedelta(days=i)] for i in range(6)]
        weekly.append({'week_end':(start+timedelta(days=6)).isoformat(),
                       'days_recorded':sum(bool(day) for day in captures),
                       'points':str(sum(sum(day.values()) for day in captures))})
    return {
        'week_start':monday.isoformat(), 'week_end':(monday+timedelta(days=6)).isoformat(),
        'players':tracked, 'weekly':weekly,
        'daily':[{'date':day.isoformat(),'players':len(scores[day]),
                  'points':str(sum(scores[day].values()))} for day in days],
    }


def load_performance(connect_db, week_start: date):
    monday = week_start - timedelta(days=week_start.weekday())
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT id,player_name AS name,player_rank AS `rank`
                       FROM `player list` WHERE possibly_removed=FALSE ORDER BY player_name,id""")
        players = cur.fetchall()
        cur.execute("""SELECT p.id AS roster_id,v.score_date,v.points
                       FROM vs_points v JOIN `player list` p ON
                         p.id=v.player_id OR (v.player_id IS NULL AND p.normalized_name=v.normalized_name)
                       WHERE p.possibly_removed=FALSE AND v.score_date BETWEEN %s AND %s
                       ORDER BY v.player_id IS NULL,v.updated_at DESC,v.id DESC""",
                    (monday-timedelta(weeks=7),monday+timedelta(days=5)))
        scores = cur.fetchall()
        cur.execute(f"""SELECT h.player_id,{HISTORY_FIELDS} FROM player_stat_history h
                        JOIN `player list` p ON p.id=h.player_id WHERE p.possibly_removed=FALSE
                        ORDER BY h.recorded_at,h.import_id""")
        history = defaultdict(list)
        for row in cur.fetchall():
            history[row['player_id']].append(row)
    return build_performance(monday,players,scores,history)

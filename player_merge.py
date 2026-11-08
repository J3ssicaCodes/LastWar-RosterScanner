"""Consolidate an explicitly selected OCR duplicate into a canonical player."""
import json
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, Field

from player_identity import learn_name_correction, name_key


class PlayerMerge(BaseModel):
    duplicate_id: int = Field(gt=0)
    expected_name: str
    expected_duplicate_name: str


def merge_vs_rows(cursor, scores, keep_id, keep, duplicate_id=None):
    """Consolidate locked score rows, preferring the retained player's score."""
    keep_key = name_key(keep)
    ordered = sorted(scores, key=lambda row: (
        row['player_id'] != keep_id,
        row['normalized_name'] != keep_key,
        row['player_id'] != duplicate_id,
        row['id'],
    ))
    by_day, redundant = {}, []
    for row in ordered:
        if row['score_date'] in by_day:
            redundant.append(row['id'])
        else:
            by_day[row['score_date']] = row['id']
    cursor.executemany('DELETE FROM vs_points WHERE id=%s', [(id_,) for id_ in redundant])
    cursor.executemany('UPDATE vs_points SET player_id=%s,player_name=%s,normalized_name=%s WHERE id=%s',
                       [(keep_id,keep,keep_key,id_) for id_ in by_day.values()])
    return len(redundant)


def merge_player_records(connect_db, keep_id, body):
    if keep_id == body.duplicate_id:
        raise HTTPException(422, detail='Choose two different players.')
    ids = tuple(sorted((keep_id, body.duplicate_id)))
    with connect_db() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""SELECT id,player_name,power,power_millions,kills,donations,hq_level,birthday,
                                      avatar_mime,avatar_updated_at,avatar_image,first_seen_at,last_seen_at,
                                      is_new,possibly_removed,has_t11,away_start,away_end,player_notes FROM `player list`
                               WHERE id IN (%s,%s) ORDER BY id FOR UPDATE""", ids)
                players = {row['id']:row for row in cur.fetchall()}
                if len(players) != 2:
                    raise HTTPException(409, detail='One of these players is no longer in the list. Refresh and try again.')
                retained, source = players[keep_id], players[body.duplicate_id]
                keep, duplicate = retained['player_name'], source['player_name']
                if (keep, duplicate) != (body.expected_name, body.expected_duplicate_name):
                    raise HTTPException(409, detail='A player name changed. Refresh and confirm the two names again.')
                keep_key, duplicate_key = name_key(keep), name_key(duplicate)
                retained_away=(retained['away_start'],retained['away_end'])
                source_away=(source['away_start'],source['away_end'])
                if any(retained_away) and any(source_away) and retained_away!=source_away:
                    raise HTTPException(409,detail='These profiles have different away dates. Resolve them before merging.')
                notes=[value for value in (retained['player_notes'],source['player_notes']) if value]
                combined_notes='\n\n'.join(dict.fromkeys(notes))
                if len(combined_notes)>5000:
                    raise HTTPException(409,detail='Combined player notes exceed 5000 characters. Shorten them before merging.')
                away=retained_away if any(retained_away) else source_away

                # Keep one score per day. A score on the retained record takes priority.
                cur.execute("""SELECT id,score_date,player_id,normalized_name FROM vs_points
                               WHERE player_id IN (%s,%s) OR
                                   (player_id IS NULL AND normalized_name IN (%s,%s))
                               ORDER BY score_date,id FOR UPDATE""", (*ids,keep_key,duplicate_key))
                overlapping = merge_vs_rows(cur, cur.fetchall(), keep_id, keep, body.duplicate_id)

                cur.execute("""SELECT player_id,import_id FROM player_stat_history
                               WHERE player_id IN (%s,%s) FOR UPDATE""", ids)
                history = cur.fetchall()
                retained_imports = {row['import_id'] for row in history if row['player_id'] == keep_id}
                cur.executemany('UPDATE player_stat_history SET import_id=%s WHERE player_id=%s AND import_id=%s',
                                [(str(uuid4()),body.duplicate_id,row['import_id']) for row in history
                                 if row['player_id'] == body.duplicate_id and row['import_id'] in retained_imports])
                cur.execute('UPDATE player_stat_history SET player_id=%s WHERE player_id=%s',(keep_id,body.duplicate_id))

                for table in ('roster upload','vs_scan_rows'):
                    cur.execute(f"""UPDATE `{table}` SET ocr_name=COALESCE(ocr_name,player_name),player_name=%s
                                    WHERE BINARY player_name=%s""", (keep,duplicate))
                cur.execute('UPDATE player_name_dictionary SET player_name=%s WHERE BINARY player_name=%s',(keep,duplicate))
                learn_name_correction(cur,duplicate,keep)

                cur.execute("""SELECT id,updated_player_ids FROM media_scan_jobs
                               WHERE JSON_CONTAINS(updated_player_ids,%s) ORDER BY id FOR UPDATE""",
                            (str(body.duplicate_id),))
                for row in cur.fetchall():
                    updated = set(json.loads(row['updated_player_ids']))
                    updated.discard(body.duplicate_id)
                    updated.add(keep_id)
                    cur.execute('UPDATE media_scan_jobs SET updated_player_ids=%s WHERE id=%s',
                                (json.dumps(sorted(updated)),row['id']))

                fields = ('power','power_millions','kills','donations','hq_level','birthday')
                combined = [source[field] if retained[field] is None else retained[field] for field in fields]
                portrait = retained if retained['avatar_image'] is not None else source
                cur.execute("""UPDATE `player list` SET power=%s,power_millions=%s,kills=%s,donations=%s,
                               hq_level=%s,birthday=%s,avatar_mime=%s,avatar_updated_at=%s,avatar_image=%s,
                               has_t11=%s,away_start=%s,away_end=%s,player_notes=%s,
                               first_seen_at=LEAST(first_seen_at,%s),last_seen_at=GREATEST(last_seen_at,%s),
                               is_new=is_new AND %s,possibly_removed=possibly_removed AND %s,
                               updated_at=UTC_TIMESTAMP(6) WHERE id=%s""",
                            (*combined,portrait['avatar_mime'],portrait['avatar_updated_at'],portrait['avatar_image'],
                             bool(retained['has_t11'] or source['has_t11']),*away,combined_notes or None,
                             source['first_seen_at'],source['last_seen_at'],source['is_new'],source['possibly_removed'],keep_id))
                for field in ('conductor_id','vip_id'):
                    cur.execute(f'UPDATE train_assignments SET {field}=%s WHERE {field}=%s',
                                (keep_id,body.duplicate_id))
                cur.execute('SELECT reason FROM train_restrictions WHERE player_id=%s FOR UPDATE',
                            (body.duplicate_id,))
                restriction = cur.fetchone()
                if restriction:
                    cur.execute("""INSERT INTO train_restrictions (player_id,reason) VALUES (%s,%s)
                                   ON DUPLICATE KEY UPDATE reason=COALESCE(reason,VALUES(reason))""",
                                (keep_id,restriction['reason']))
                    cur.execute('DELETE FROM train_restrictions WHERE player_id=%s',(body.duplicate_id,))
                cur.execute('DELETE FROM `player list` WHERE id=%s',(body.duplicate_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return {'id':keep_id,'name':keep,'merged_name':duplicate,'overlapping_vs_scores':overlapping}

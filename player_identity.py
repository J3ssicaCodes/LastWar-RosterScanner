"""Resolve OCR observations to the one master player directory."""
import json
import re
import unicodedata


def name_key(name):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC',name or '').casefold())


def compact_key(name):
    return ''.join(c for c in name_key(name) if c.isalnum())


def exact_player_index(players):
    """Index canonical names and leave ambiguous normalized keys unresolved."""
    by_name = {}
    for player in players:
        key = name_key(player.get('player_name', player.get('name')))
        if key:
            by_name[key] = player if key not in by_name else None
    return by_name


def match_master(row, players):
    key=name_key(row.get('name'))
    exact=[p for p in players if name_key(p.get('player_name',p.get('name')))==key]
    if len(exact)==1:return exact[0]
    compact=compact_key(row.get('name'))
    candidates=[p for p in players if compact_key(p.get('player_name',p.get('name')))==compact]
    # Only accept an unambiguous punctuation/spacing variant, never a fuzzy name.
    if len(compact)>=3 and len(candidates)==1:return candidates[0]
    # Decorative names sometimes OCR differently between game screens. A strong
    # portrait match plus a close name supports the same identity; repeated or
    # common portraits alone never cause a merge.
    if not row.get('avatar_base64'):return None
    from difflib import SequenceMatcher
    from app import portraits_match
    candidates=[p for p in players if
                SequenceMatcher(None,compact,compact_key(p.get('player_name',p.get('name')))).ratio()>=.78
                and p.get('avatar_base64') and portraits_match(row['avatar_base64'],p['avatar_base64'])]
    return candidates[0] if len(candidates)==1 else None


def load_name_corrections(cursor):
    cursor.execute('SELECT ocr_key, player_name FROM player_name_dictionary')
    return {row['ocr_key']: row['player_name'] for row in cursor.fetchall()}


def corrected_name(name, corrections, master_keys):
    original = name
    visited = set()
    while True:
        key = name_key(name)
        # A real roster name takes precedence over a learned OCR alias.
        if key in master_keys or key not in corrections:
            return name
        if key in visited:
            return original
        visited.add(key)
        name = corrections[key]


def learn_name_correction(cursor, original, corrected):
    key = name_key(original)
    if not key or key == name_key(corrected) or len(key) > 255:
        return
    cursor.execute('''INSERT INTO player_name_dictionary (ocr_key, player_name)
        VALUES (%s, %s) ON DUPLICATE KEY UPDATE player_name=VALUES(player_name)''',
        (key, corrected))


def scan_player_ids(cursor, job_id, lock=False):
    cursor.execute('SELECT updated_player_ids FROM media_scan_jobs WHERE id=%s'
                   + (' FOR UPDATE' if lock else ''), (job_id,))
    job = cursor.fetchone()
    return set(json.loads(job.get('updated_player_ids') or '[]')) if job else set()


def record_scan_players(cursor, job_id, player_ids, previous):
    updated = previous | set(player_ids)
    if updated != previous:
        cursor.execute('UPDATE media_scan_jobs SET updated_player_ids=%s WHERE id=%s',
                       (json.dumps(sorted(updated)), job_id))

"""Resolve OCR observations to the one master player directory."""
import re
import unicodedata


def name_key(name):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC',name or '').casefold())


def compact_key(name):
    return ''.join(c for c in name_key(name) if c.isalnum())


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

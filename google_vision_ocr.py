"""Use Cloud Vision as a bounded second reader for ambiguous VS rows."""
from datetime import datetime
import base64
import json
import logging
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from player_identity import corrected_name, name_key, number_needs_review, NUMBER_REVIEW_PREFIX

log = logging.getLogger(__name__)
MONTHLY_IMAGE_LIMIT = 900
BATCH_SIZE = 12


def reserve_vision_images(connect_db, requested):
    """Reserve usage before dispatch so retries and restarts cannot reset the cap."""
    month = datetime.now(ZoneInfo('America/Los_Angeles')).date().replace(day=1)
    with connect_db() as conn, conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS vs_vision_usage (
            month_start DATE PRIMARY KEY, images INT UNSIGNED NOT NULL
        ) ENGINE=InnoDB''')
        cur.execute('INSERT IGNORE INTO vs_vision_usage VALUES (%s,0)', (month,))
        cur.execute('SELECT images FROM vs_vision_usage WHERE month_start=%s FOR UPDATE', (month,))
        allowed = min(requested, max(0, MONTHLY_IMAGE_LIMIT - cur.fetchone()['images']))
        cur.execute('UPDATE vs_vision_usage SET images=images+%s WHERE month_start=%s', (allowed, month))
        conn.commit()
    return allowed


def annotate_images(api_key, crops):
    request = Request(
        'https://vision.googleapis.com/v1/images:annotate',
        headers={'Content-Type': 'application/json', 'x-goog-api-key': api_key},
        data=json.dumps({'requests': [
            {'image': {'content': image}, 'features': [{'type': 'TEXT_DETECTION'}]}
            for image in crops
        ]}).encode(),
        method='POST',
    )
    with urlopen(request, timeout=10) as response:
        results = json.load(response)['responses']
    if not isinstance(results, list) or len(results) != len(crops):
        raise ValueError('Unexpected Vision response count.')
    return results


def read_row_words(response, width, height):
    name_words, score_words = [], []
    for word in response.get('textAnnotations', [])[1:]:
        vertices = word.get('boundingPoly', {}).get('vertices', [])
        if not vertices:
            continue
        x = sum(v.get('x', 0) for v in vertices) / (len(vertices) * width)
        y = sum(v.get('y', 0) for v in vertices) / (len(vertices) * height)
        text = word.get('description', '').strip()
        if .30 <= x <= .75 and .14 <= y < .48:
            name_words.append((x, text))
        if x >= .71 and .30 <= y <= .70:
            score_words.append((x, text))
    name = ' '.join(text for _,text in sorted(name_words))
    score = ''.join(text for _,text in sorted(score_words))
    points = None
    if re.fullmatch(r'[0-9]{1,16}|[0-9]{1,3}(?:,[0-9]{3}){1,5}', score):
        value = int(score.replace(',', ''))
        if value <= 9007199254740991:
            points = value
    return name, points


def recheck_vs_rows(result, api_key, connect_db, corrections, master_keys, progress):
    if not api_key:
        return
    def resolve(name):
        return corrected_name(name, corrections, master_keys)

    candidates = [row for row in result['rows'] if row.get('evidence_base64') and
                  (name_key(resolve(row['name'])) not in master_keys or
                   number_needs_review(row['review_reasons']))]
    for start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[start:start+BATCH_SIZE]
        try:
            allowed = reserve_vision_images(connect_db, len(batch))
            if not allowed:
                break
            batch = batch[:allowed]
            progress(99,100,f'Cross-checking flagged VS rows {start+1}–{start+allowed}…')
            responses = annotate_images(api_key, [row['evidence_base64'] for row in batch])
        except (OSError, ValueError, KeyError, TypeError):
            log.warning('Cloud Vision could not check this scan; preserving local review results.')
            break
        except Exception:
            log.warning('Could not reserve Cloud Vision usage; preserving local review results.')
            break
        for row,response in zip(batch,responses):
            if not isinstance(response, dict) or response.get('error'):
                continue
            image = cv2.imdecode(np.frombuffer(base64.b64decode(row['evidence_base64']), np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                continue
            name, points = read_row_words(response, image.shape[1], image.shape[0])
            canonical = resolve(name)
            reasons = row['review_reasons']
            if (name_key(resolve(row['name'])) not in master_keys and
                    name_key(canonical) in master_keys):
                row['name'] = canonical
                reasons[:] = [reason for reason in reasons if 'name' not in reason.lower()]
            if points is None or not number_needs_review(reasons):
                continue
            readings = set(row.get('point_readings', []))
            if readings == {points} and row['points'] == points:
                reasons[:] = [reason for reason in reasons if not (
                    reason == 'Check points' or reason.startswith(NUMBER_REVIEW_PREFIX)
                )]
            else:
                reasons.append(f'{NUMBER_REVIEW_PREFIX} Google Vision read {points:,}.')
        if allowed < BATCH_SIZE and start+allowed < len(candidates):
            break

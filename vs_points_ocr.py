"""VS daily scores: stable pages, visible full rows, selected weekday, no pinned self row."""
from __future__ import annotations
import base64
from collections import Counter, defaultdict
import re
from difflib import SequenceMatcher
from datetime import date, timedelta
from pathlib import Path
import tempfile
import cv2
import numpy as np
from extract_ranking_pages import detect, extract
from scan_rankings import card_borders, pinned_top, estimate_row_height, text_with_conf, enlarge, best_start, retry_name_crop

from player_identity import corrected_name, compact_key, name_key, NUMBER_REVIEW_PREFIX

DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat')


def score_date_for_day(upload_date: date, day: int) -> date:
    if day not in range(6):
        raise ValueError('VS has scores Monday through Saturday only.')
    sunday = upload_date + timedelta(days=(6-upload_date.weekday()) % 7)
    return sunday - timedelta(days=6-day)


def selected_weekday(image, validate_labels=True):
    h, w = image.shape[:2]
    # The active day has a white rectangle; inactive days are beige. Read the
    # labels too, so a Weekly Rank screen cannot silently become a daily score.
    band = image[int(.151*h):int(.190*h), int(.03*w):int(.97*w)]
    if validate_labels:
        text, _ = text_with_conf(enlarge(band), 6)
        if sum(day.lower() in text.lower() for day in DAYS) < 3:
            raise ValueError('Daily VS weekday tabs were not readable. Use the Daily Rank screen.')
    scores = []
    for i in range(6):
        cell = band[:, int(i*band.shape[1]/6):int((i+1)*band.shape[1]/6)]
        gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        scores.append(float((gray > 238).mean()))
    order = np.argsort(scores)
    if scores[order[-1]] < .35 or scores[order[-1]]-scores[order[-2]] < .18:
        raise ValueError('Selected VS day is unclear. Capture the highlighted day at the top.')
    return int(order[-1])


def isolated_zero(crop):
    """Recognize the tiny printed 0 by its one centered, tall inner aperture.

    The test requires actual dark glyph ink. It never interprets a blank stat
    cell as zero, and rejects multi-digit cells and 6/8/9 shapes.
    """
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    contours,hierarchy=cv2.findContours((gray<100).astype('uint8')*255,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:return False
    outer=[i for i,c in enumerate(contours) if hierarchy[0][i][3]<0 and cv2.contourArea(c)>8]
    if not outer:return False
    biggest=max(cv2.contourArea(contours[i]) for i in outer)
    major=[i for i in outer if cv2.contourArea(contours[i])>.18*biggest]
    if len(major)!=1:return False
    index=major[0];x,y,w,h=cv2.boundingRect(contours[index])
    holes=[i for i in range(len(contours)) if hierarchy[0][i][3]==index and cv2.contourArea(contours[i])>4]
    if len(holes)!=1 or x<crop.shape[1]*.65 or not 1.1<h/max(w,1)<2.2:return False
    hx,hy,hw,hh=cv2.boundingRect(contours[holes[0]])
    return (hh/h>.52 and .15<hw/w<.65
            and abs((hy+hh/2)-(y+h/2))<.15*h
            and abs((hx+hw/2)-(x+w/2))<.20*w)


def score_line(crop):
    """Separate the score's printed digits from touch indicators and card edges."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    scale = w / 285
    mask = (gray < float(np.median(gray)) - 38).astype('uint8')
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    glyphs = [i for i, (x, y, width, height, area) in enumerate(stats[1:], 1)
              if 7*scale <= width <= 28*scale and 16*scale <= height <= 31*scale
              and .20*h <= y + height/2 <= .72*h and area >= 65*scale**2]
    if not glyphs:
        return None, None, None
    glyphs.sort(key=lambda i: stats[i][0])
    # Scores are right-aligned. A missing glyph leaves a gap wider than
    # the game's comma spacing, so a short reading cannot pass validation.
    gaps = [stats[b][0] - (stats[a][0] + stats[a][2]) for a,b in zip(glyphs,glyphs[1:])]
    if max(gaps, default=0) > 21*scale or w-(stats[glyphs[-1]][0]+stats[glyphs[-1]][2]) > 43*scale:
        return None, None, None
    first = stats[glyphs[0]][0]
    right = max(stats[i][0] + stats[i][2] for i in glyphs)
    top = min(stats[i][1] for i in glyphs)
    baseline = max(stats[i][1] + stats[i][3] for i in glyphs)
    bottom = baseline
    keep = np.zeros(count, np.uint8)
    keep[glyphs] = 255
    for i, (x, y, width, height, area) in enumerate(stats[1:], 1):
        if first < x < right and 3*scale <= width <= 12*scale and 5*scale <= height <= 14*scale and baseline-8*scale <= y <= baseline:
            keep[i] = 255
            bottom = max(bottom, y + height)
    ink = keep[labels][top:bottom,first:right]
    return len(glyphs), gray[top:bottom,first:right], 255-ink


def read_score(crop):
    digits, gray, ink = score_line(crop)
    if digits == 1 and isolated_zero(crop):
        return '0', 98.

    def read(image, psm):
        text, confidence = text_with_conf(image, psm, '0123456789,')
        if not re.fullmatch(r'[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+', text):
            return '', 0.
        return text.replace(',',''), confidence

    value, confidence = read(enlarge(crop), 7)
    if digits is None:
        return value, min(confidence, 54)
    if len(value) == digits and confidence >= 55:
        return value, confidence
    candidates = defaultdict(list)
    if len(value) == digits:
        candidates[value].append(confidence)
    for line, psm in ((gray, 7), (ink, 13), (ink, 7)):
        padded = cv2.copyMakeBorder(enlarge(line,3),24,24,24,24,cv2.BORDER_CONSTANT,value=255)
        alternate, score = read(padded, psm)
        if len(alternate) == digits:
            candidates[alternate].append(score)
    if len(candidates) == 1:
        chosen, scores = next(iter(candidates.items()))
        # A correctly segmented full score, read consistently in multiple
        # image treatments, can corroborate an otherwise low-confidence read.
        return chosen, max(max(scores), 65 if len(scores) > 1 else 0)
    if candidates:
        chosen = max(candidates, key=lambda k: (len(candidates[k]), max(candidates[k])))
        return chosen, min(max(candidates[chosen]), 54)
    return '', 0.


def read_player_name(crop, resolve, master_keys):
    name, confidence = text_with_conf(enlarge(crop), 7)
    if master_keys and name_key(resolve(name)) in master_keys:
        return name, confidence
    alternate, score = text_with_conf(enlarge(crop,3), 13)
    if master_keys and name_key(resolve(alternate)) in master_keys:
        return alternate, score
    if alternate and (not name or score > confidence):
        name, confidence = alternate, score
    return retry_name_crop(crop,name,confidence)


def roster_portrait_match(row, name, portraits):
    """Require matching artwork and supporting text before linking a fuzzy name."""
    if not row.get('avatar_base64'):
        return None
    avatar = cv2.imdecode(np.frombuffer(base64.b64decode(row['avatar_base64']), np.uint8), cv2.IMREAD_GRAYSCALE)
    if avatar is None:
        return None
    center = cv2.resize(avatar, (96,96), interpolation=cv2.INTER_AREA)[23:73,23:73]
    if center.std() < 12:
        return None
    templates = [cv2.resize(center,(size,size)) for size in (45,50,55)]
    scores = sorted(((max(float(cv2.matchTemplate(portrait, template, cv2.TM_CCOEFF_NORMED).max())
                          for template in templates), key) for key, portrait in portraits.items()), reverse=True)
    if not scores:
        return None
    best, key = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0
    text = compact_key(name)
    target = compact_key(key)
    similarity = SequenceMatcher(None, text, target, autojunk=False).ratio()
    if best >= .86 and best-runner_up >= .14 and similarity >= .52:
        return key
    return None


def read_page(image, page, period, upper, targets=None, anchors=None, *, resolve=str, master_keys=None,
              gap_neighbors=None):
    h,w=image.shape[:2]; scale=w/1080
    bottom=pinned_top(image)
    rows=[]

    def avatar_at(center):
        return image[round(center-52*scale):round(center+53*scale),round(240*scale):round(347*scale)]

    def signature_at(center):
        return cv2.resize(cv2.cvtColor(avatar_at(center),cv2.COLOR_BGR2GRAY),(24,24))

    if anchors is None:
        borders=card_borders(image,bottom)
        intervals = [(top, end, None) for top, end in zip(borders,borders[1:])
                     if .90*period <= end-top <= 1.12*period and top >= upper-8*scale and end <= bottom-4*scale]
    else:
        # A moving border or touch overlay can defeat row detection even
        # though the same avatar and score are still fully visible.
        intervals = []
        for anchor in anchors:
            expected = anchor['center']
            def distance(center):
                return cv2.absdiff(signature_at(center),anchor['signature']).mean()
            centers = range(expected-round(.29*period),expected+round(.29*period)+1,max(1,round(3*scale)))
            centers = [cy for cy in centers if upper+50*scale <= cy <= bottom-55*scale]
            if upper+50*scale <= expected <= bottom-55*scale:
                centers.append(expected)
            if not centers:
                continue
            cy = min(centers,key=distance)
            if distance(cy) < 13:
                half = round(66*scale)
                intervals.append((cy-half,cy+half,anchor))
    for index, (top, end, anchor) in enumerate(intervals):
        cy=round((top+end)/2)
        def crop(x0,x1,y0,y1):
            return image[max(top,round(cy+y0*scale)):min(end,round(cy+y1*scale)),round(x0*scale):round(x1*scale)]
        avatar=avatar_at(cy)
        signature=signature_at(cy)
        matched = anchor['internal_position'] if anchor is not None else None
        position, pc=text_with_conf(enlarge(crop(92,185,-35,39)),7,'0123456789') if anchors is None else ('',0)
        position=re.sub(r'\D','',position)
        position=int(position) if position.isdigit() and 1<=int(position)<=100 else None
        if targets is not None and position not in targets:
            if gap_neighbors is None or not 0 < index < len(intervals)-1:
                continue
            before = round(sum(intervals[index-1][:2])/2)
            after = round(sum(intervals[index+1][:2])/2)
            if not all(.85*period < abs(center-cy) < 1.15*period
                       and cv2.absdiff(signature_at(center), neighbor['signature']).mean() < 12
                       for center,neighbor in zip((before,after),gap_neighbors)):
                continue
        name_crop=crop(350,808,-50,-8)
        check_name=anchor is None or anchor.get('check_name',True)
        name,nc=read_player_name(name_crop,resolve,master_keys) if check_name else ('',0)
        name=re.sub(r'\s+',' ',name).strip()
        val,vc=read_score(crop(750,1035,-25,44))
        ok=ok_avatar=False
        if anchors is None:
            evidence=image[top:end,round(45*scale):round(1040*scale)]
            ok, encoded=cv2.imencode('.webp',evidence,[cv2.IMWRITE_WEBP_QUALITY,75])
            ok_avatar, encoded_avatar=cv2.imencode('.webp',avatar,[cv2.IMWRITE_WEBP_QUALITY,80])
        rows.append({'name':name,'value':val,'name_conf':nc,'value_conf':vc,
                     'position_read':position,'position_conf':pc,'page':page,
                     'center':cy,'signature':signature,'matched_position':matched,'period':period,
                     'avatar_base64':base64.b64encode(encoded_avatar).decode() if ok_avatar else None,
                     'evidence_base64':base64.b64encode(encoded).decode() if ok else None})
    return rows


def review_flags(observations, names, scores, resolve, master_keys, verified_numbers=()):
    reasons = defaultdict(list)
    previous = None
    for position, row in sorted(observations.items()):
        flags = reasons[position]
        flags.extend(row.get('neighbor_conflicts',[]))
        if len(names.get(position,set()) - {''}) > 1:
            flags.append('Name differs between captured views')
        if not row['name'] or row['name_conf'] < 55:
            flags.append('Check player name')
        if master_keys is not None and name_key(resolve(row['name'])) not in master_keys:
            flags.append('Verify player name against the Player List')
        if len(scores.get(position,set())) > 1:
            flags.append('Points differ between captured views')
        if not row['value'].isdigit() or row['value_conf'] < 55:
            flags.append('Check points')
        points = int(row['value']) if row['value'].isdigit() else None
        if points is not None and previous is not None and points > previous[1]:
            previous_verified = previous[0] in verified_numbers
            current_verified = position in verified_numbers
            if not current_verified or previous_verified:
                flags.append('Points exceed the row above')
            if not previous_verified or current_verified:
                reasons[previous[0]].append('Next row has higher points; verify this value')
        previous = (position, points) if points is not None else None
    return reasons


def recheck_neighbors(observations, sources, reasons, resolve, progress, read_frame):
    groups = defaultdict(list)
    for position, row in observations.items():
        if row['page'] in sources:
            row['check_name']=bool(reasons.get(position))
            groups[sources[row['page']]].append(row)
    confirmed = defaultdict(set)
    captures = {}
    try:
        for index, ((path, frame), anchors) in enumerate(groups.items(), 1):
            progress(96+round(3*index/max(1,len(groups))),100,
                     f'Cross-checking adjacent frames {index}/{len(groups)}…')
            if path not in captures:
                captures[path] = cv2.VideoCapture(str(path))
            cap = captures[path]
            neighbors = defaultdict(list)
            previous_read = False
            for adjacent in (frame-1,frame+1):
                if adjacent < 0:
                    continue
                # After reading frame-1 the decoder is already at frame.
                # Skip that one frame instead of seeking back to a keyframe.
                positioned = cap.grab() if previous_read else False
                if not positioned and not cap.set(cv2.CAP_PROP_POS_FRAMES,adjacent):
                    continue
                ok,image = cap.read()
                if not ok:
                    continue
                previous_read = adjacent == frame-1
                readings = defaultdict(list)
                for reading in read_frame(image,anchors):
                    readings[reading['matched_position']].append(reading)
                for position, matches in readings.items():
                    if len(matches) == 1:
                        neighbors[position].append(matches[0])
            for row in anchors:
                position = row['internal_position']
                views = [row,*neighbors[position]]
                numbers = [view['value'] for view in views]
                readable = [int(value) for value in numbers if value.isdecimal()]
                reliable = [view for view in views
                            if view['value'].isdecimal() and view['value_conf'] >= 55]
                if len(reliable) >= 2 and len(set(readable)) == 1:
                    best = max(reliable, key=lambda view: view['value_conf'])
                    row['value'], row['value_conf'] = best['value'], best['value_conf']
                    confirmed[position].add('value')
                else:
                    readings = [f'{int(value):,}' if value.isdecimal() else 'unreadable' for value in numbers]
                    readings += ['unavailable'] * (3 - len(views))
                    row.setdefault('neighbor_conflicts',[]).append(
                        f"{NUMBER_REVIEW_PREFIX} frame readings were {' / '.join(readings)}.")
                if len(views) != 3 or not row['check_name']:
                    continue
                keys = [name_key(resolve(view['name'])) for view in views]
                votes = Counter(key for key in keys if key)
                if not votes:
                    continue
                winner, count = votes.most_common(1)[0]
                if count < 2:
                    if len(votes)>1:
                        row.setdefault('neighbor_conflicts',[]).append('Player name differs across adjacent frames')
                    continue
                best = max((view for view,key in zip(views,keys) if key == winner),
                           key=lambda view:view['name_conf'])
                row['name'],row['name_conf'] = best['name'],best['name_conf']
                confirmed[position].add('name')
    finally:
        for cap in captures.values():
            cap.release()
    return confirmed


def read_vs_neighbor(image, anchors, day, resolve, master_keys):
    try:
        if selected_weekday(image,False) != day:
            return []
    except ValueError:
        return []
    return read_page(image,0,anchors[0]['period'],
                     round(.225*image.shape[0]),anchors=anchors,resolve=resolve,master_keys=master_keys)


def scan_vs_upload(paths, progress, upload_date: date, corrections=None, master_keys=None):
    corrections = corrections or {}
    resolve = lambda name: corrected_name(name, corrections, master_keys or set())
    observations={}; days=set(); total_pages=0; page_sources={}; recovered=[]; variants={}; score_variants={}
    with tempfile.TemporaryDirectory(prefix='vs_pages_',dir=str(Path(paths[0]).parent)) as tmp:
        for index,path in enumerate(paths):
            path=Path(path)
            if path.suffix.lower() in ('.mp4','.mov','.m4v','.webm'):
                info=detect(path,progress=progress)
                manifest=extract(path,Path(tmp)/str(index),*info)
                pages=[Path(tmp)/str(index)/p['image'] for p in manifest['kept']]
            else:
                pages=[path]
            for number,page in enumerate(pages,1):
                image=cv2.imread(str(page))
                if image is None:raise ValueError('Could not read VS media.')
                if number==1:
                    days.add(selected_weekday(image))
                    if len(days)>1:raise ValueError('Upload one VS day at a time.')
                    if score_date_for_day(upload_date,next(iter(days)))>upload_date:
                        raise ValueError('Selected VS day is later than the local upload date.')
                elif selected_weekday(image,False) not in days:
                    raise ValueError('The selected VS day changed during the recording. Upload one day at a time.')
                try:period=estimate_row_height(image)
                except ValueError:period=189*image.shape[1]/1080
                rows=read_page(image,total_pages+1,period,round(.225*image.shape[0]),resolve=resolve,master_keys=master_keys)
                start=best_start(rows,observations)
                for offset,row in enumerate(rows):
                    position=start+offset
                    if not 1<=position<=100:continue
                    variants.setdefault(position,set()).add(name_key(resolve(row['name'])))
                    if row['value'].isdigit():
                        score_variants.setdefault(position,set()).add(int(row['value']))
                    old=observations.get(position)
                    row['internal_position']=position
                    if old is None or row['name_conf']+row['value_conf']>old['name_conf']+old['value_conf']:
                        observations[position]=row
                if path.suffix.lower() in ('.mp4','.mov','.m4v','.webm'):
                    page_sources[total_pages+1]=(path,manifest['kept'][number-1]['frame'])
                total_pages+=1
                progress(28+round(65*number/len(pages)),100,f'Reading VS page {number}/{len(pages)} · {len(observations)} scores')
        gaps=[p for p in range(1,max(observations,default=0)+1) if p not in observations]
        for gap in gaps[:8]:
            previous=observations.get(gap-1);following=observations.get(gap+1)
            if not previous or not following:continue
            left=page_sources.get(previous['page']);right=page_sources.get(following['page'])
            if not left or not right or left[0]!=right[0] or right[1]<=left[1]:continue
            progress(95,100,f'Checking transition for missing VS position {gap}…')
            cap=cv2.VideoCapture(str(left[0]))
            try:
                for frame in np.linspace(left[1],right[1],14,dtype=int)[1:-1]:
                    cap.set(cv2.CAP_PROP_POS_FRAMES,int(frame));ok,image=cap.read()
                    if not ok:continue
                    candidates=read_page(image,-gap,previous['period'],round(.225*image.shape[0]),{gap},
                                         resolve=resolve,master_keys=master_keys,gap_neighbors=(previous,following))
                    if len(candidates) == 1:
                        row=candidates[0];row['internal_position']=gap
                        observations[gap]=row
                        page_sources[-gap]=(left[0],int(frame))
                        recovered.append(gap)
                        break
            finally:cap.release()
        reasons = review_flags(observations, variants, score_variants, resolve, master_keys)
        name_warnings = {pos: any('name' in flag.lower() for flag in flags) for pos,flags in reasons.items()}
        confirmed = recheck_neighbors(
            observations, page_sources, name_warnings, resolve, progress,
            lambda image,anchors: read_vs_neighbor(image,anchors,next(iter(days)),resolve,master_keys))
        for position, fields in confirmed.items():
            if 'name' in fields:
                variants.pop(position,None)

    if not observations:raise ValueError('No complete VS score rows found.')
    day=next(iter(days)); earned=score_date_for_day(upload_date,day)
    if earned>upload_date:
        raise ValueError('The selected VS day is later than the upload date. Check the daily tab and your local date.')
    verified_numbers = {pos for pos, fields in confirmed.items()
                        if 'value' in fields and len(score_variants.get(pos, set())) <= 1}
    reasons = review_flags(observations, variants, score_variants, resolve, master_keys, verified_numbers)
    rows=[]
    for pos,row in sorted(observations.items()):
        if len(score_variants.get(pos,set()))>1 or row['value_conf']<55:
            reasons[pos].append(f'{NUMBER_REVIEW_PREFIX} captured scores disagree or the number is unclear.')
        if reasons[pos] and row['page'] in page_sources:
            reasons[pos].append('Adjacent frames did not resolve all review flags')
        rows.append({**{k:v for k,v in row.items() if k not in ('signature','center','matched_position','period')},
                     'name':resolve(row['name']),'ocr_name':row['name'],
                     'points':int(row['value']) if row['value'].isdigit() else None,
                     'review_reasons':reasons[pos],'score_date':earned.isoformat()})
    return {'rows':rows,'weekday':DAYS[day],'score_date':earned.isoformat(),
            'upload_date':upload_date.isoformat(),'pages':total_pages,'recovered_positions':recovered,
            'missing_positions':[p for p in range(1,max(observations)+1) if p not in observations]}

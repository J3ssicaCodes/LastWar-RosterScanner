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
from extract_ranking_pages import detect, extract, open_video
from scan_rankings import card_borders, pinned_top, estimate_row_height, text_with_conf, enlarge, best_start, retry_name_crop

from player_identity import corrected_name, compact_key, name_key, NUMBER_REVIEW_PREFIX
from vs_rank_reader import read_rank

DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat')
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm'}


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
              gap_neighbors=None, missing_names=None, protected_positions=()):
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
        position, pc=read_rank(crop(92,185,-35,39)) if anchors is None else ('',0)
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
        if missing_names is not None and (position is None or pc < 70 or position in protected_positions):
            continue
        name_crop=crop(350,808,-50,-8)
        check_name=anchor is None or anchor.get('check_name',True)
        name,nc=read_player_name(name_crop,resolve,master_keys) if check_name else ('',0)
        name=re.sub(r'\s+',' ',name).strip()
        if missing_names is not None and (nc < 55 or name_key(resolve(name)) not in missing_names):
            continue
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


def recheck_neighbors(observations, sources, reasons, resolve, progress, read_frame, min_value_votes=3):
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
                captures[path] = open_video(path)
            cap = captures[path]
            neighbors = defaultdict(list)

            def read_at(index, targets, positioned=False):
                if index < 0 or (not positioned and not cap.set(cv2.CAP_PROP_POS_FRAMES,index)):
                    return False
                ok,image = cap.read()
                if not ok:
                    return False
                readings = defaultdict(list)
                for reading in read_frame(image,targets):
                    readings[reading['matched_position']].append(reading)
                for target in targets:
                    position = target['internal_position']
                    matches = readings[position]
                    neighbors[position].append(matches[0] if len(matches) == 1 else None)
                return True

            def points_in(views):
                return [int(view['value']) for view in views if view and view['value'].isdecimal()]

            def winning_value(views):
                votes = Counter(points_in(views)).most_common()
                if votes and votes[0][1] >= min_value_votes and (len(votes) == 1 or votes[0][1] > votes[1][1]):
                    return votes[0][0]
                return None

            before = read_at(frame-1,anchors)
            read_at(frame+1,anchors,positioned=before and cap.grab())
            unresolved = [row for row in anchors
                          if winning_value([row,*neighbors[row['internal_position']]]) is None]
            if unresolved:
                read_at(frame-3,unresolved)
                read_at(frame+3,unresolved)
            for row in anchors:
                position = row['internal_position']
                views = [row,*neighbors[position]]
                readable = points_in(views)
                row['point_readings'] = sorted(set(readable))
                winner = winning_value(views)
                if winner is not None:
                    row['value'] = str(winner)
                    row['value_conf'] = max(55, *(view['value_conf'] for view in views
                                            if view and view['value'].isdecimal()
                                            and int(view['value']) == winner))
                    confirmed[position].add('value')
                else:
                    row['frame_consensus_failed'] = True
                    readings = [f"{int(view['value']):,}" if view and view['value'].isdecimal()
                                else 'unreadable' if view else 'unavailable' for view in views]
                    readings += ['unavailable'] * (5 - len(views))
                    row.setdefault('neighbor_conflicts',[]).append(
                        f"{NUMBER_REVIEW_PREFIX} frame readings were {' / '.join(readings)}.")
                views = [view for view in views if view]
                keys = [name_key(resolve(view.get('name',''))) for view in views]
                row['name_readings'] = sorted(set(keys)-{''})
                if len(views) < 3 or not row['check_name']:
                    continue
                votes = Counter(key for key in keys if key)
                if not votes:
                    continue
                winner, count = votes.most_common(1)[0]
                if count < 2 or list(votes.values()).count(count) > 1:
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
    if day is not None and readable_weekday(image,False) not in (None,day):
        return []
    return read_page(image,0,anchors[0]['period'],
                     round(.225*image.shape[0]),anchors=anchors,resolve=resolve,master_keys=master_keys)


def include_readable_opening(path, detection):
    fps, count, top, bottom, runs = detection
    if not runs or runs[0][0] <= fps/3:
        return detection
    cap = cv2.VideoCapture(str(path))
    try:
        ok, opening = cap.read()
        if not ok or not cap.set(cv2.CAP_PROP_POS_FRAMES, sum(runs[0])//2):
            return detection
        ok, paused = cap.read()
        if ok and selected_weekday(opening) == selected_weekday(paused, False):
            return fps, count, top, bottom, [(0,0), *runs]
    except ValueError:
        pass
    finally:
        cap.release()
    return detection


def readable_weekday(image, validate_labels=True):
    try:
        return selected_weekday(image,validate_labels)
    except ValueError:
        return None


def missing_player_frames(detection, used_frames):
    """Sample the edges of pauses and the scrolls between them once."""
    _, count, _, _, pauses = detection
    frames = {0, count-1}
    for start,end in pauses:
        frames.add((start+3*end)//4)
    for (_,end),(start,_) in zip(pauses,pauses[1:]):
        if start > end+1:
            frames.add((end+start)//2)
    return sorted(frame for frame in frames-used_frames if 0 <= frame < count)


def recheck_missing_players(observations, variants, score_variants, page_sources,
                            videos, roster_keys, master_keys, resolve, day, progress):
    known_by_position = {position:name_key(resolve(row['name'])) for position,row in observations.items()}
    missing = roster_keys-set(known_by_position.values())
    if not missing or not videos:
        return 0, 0

    checked = len(missing)
    found = 0
    protected = {pos for pos,key in known_by_position.items() if key in master_keys}
    used = defaultdict(set)
    for path,frame in page_sources.values():
        used[path].add(frame)
    page = min([0,*page_sources])-1
    progress(95,100,f'Rechecking the video once for {checked} missing roster players…')
    for path,detection in videos:
        if not missing:
            break
        cap = cv2.VideoCapture(str(path))
        try:
            for frame in missing_player_frames(detection,used[path]):
                if not missing:
                    break
                if not cap.set(cv2.CAP_PROP_POS_FRAMES,frame):
                    continue
                ok,image = cap.read()
                if not ok:
                    continue
                if day is not None and readable_weekday(image,False) not in (None,day):
                    continue
                try:
                    period = estimate_row_height(image)
                except ValueError:
                    period = 189*image.shape[1]/1080
                candidates = read_page(image,page,period,round(.225*image.shape[0]),
                                       resolve=resolve,master_keys=master_keys,
                                       missing_names=missing,protected_positions=protected)
                for row in candidates:
                    position = row['position_read']
                    key = name_key(resolve(row['name']))
                    known_alternates = variants.get(position,set()) & master_keys
                    if position in protected or key not in missing or known_alternates-{key}:
                        continue
                    old = observations.get(position)
                    if old and cv2.absdiff(old['signature'],row['signature']).mean() >= 18:
                        # A disputed rank must not replace a different visible player.
                        continue
                    variants[position] = {key}
                    for view in (old,row):
                        if view and view['value'].isdigit():
                            score_variants.setdefault(position,set()).add(int(view['value']))
                    if old and old['value'].isdigit():
                        # Keep the original frame and score when only the name
                        # was missing; its score still gets the normal cross-check.
                        old['name'], old['name_conf'] = row['name'], row['name_conf']
                    else:
                        row['internal_position'] = position
                        observations[position] = row
                        page_sources[page] = (path,frame)
                    protected.add(position)
                    missing.remove(key)
                    found += 1
                page -= 1
        finally:
            cap.release()
    return checked, found



def scan_vs_upload(paths, progress, upload_date: date, corrections=None, master_keys=None, *, recheck_keys=None):
    corrections = corrections or {}
    resolve = lambda name: corrected_name(name, corrections, master_keys or set())
    observations={}; days=set(); total_pages=0; page_sources={}; recovered=[]; variants={}; score_variants={}
    videos = []
    with tempfile.TemporaryDirectory(prefix='vs_pages_',dir=str(Path(paths[0]).parent)) as tmp:
        for index,path in enumerate(paths):
            path=Path(path)
            if path.suffix.lower() in VIDEO_EXTENSIONS:
                info=include_readable_opening(path,detect(path,progress=progress))
                manifest=extract(path,Path(tmp)/str(index),*info)
                videos.append((path,info))
                pages=[Path(tmp)/str(index)/p['image'] for p in manifest['kept']]
            else:
                pages=[path]
            for number,page in enumerate(pages,1):
                image=cv2.imread(str(page))
                if image is None:raise ValueError('Could not read VS media.')
                visible_day=readable_weekday(image,number==1 or not days)
                if visible_day is not None:
                    days.add(visible_day)
                    if len(days)>1:raise ValueError('Upload one VS day at a time.')
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
                if path.suffix.lower() in VIDEO_EXTENSIONS:
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
        day=next(iter(days),None)
        rechecked, found = recheck_missing_players(
            observations, variants, score_variants, page_sources, videos,
            (master_keys or set()) if recheck_keys is None else recheck_keys,
            master_keys or set(), resolve, day, progress)
        reasons = review_flags(observations, variants, score_variants, resolve, master_keys)
        name_warnings = {pos: any('name' in flag.lower() for flag in flags) for pos,flags in reasons.items()}
        confirmed = recheck_neighbors(
            observations, page_sources, name_warnings, resolve, progress,
            lambda image,anchors: read_vs_neighbor(image,anchors,day,resolve,master_keys))
        for position,row in observations.items():
            row['name_readings'] = sorted(set(row.get('name_readings',[])) | variants.get(position,set()))
            row['point_readings'] = sorted(set(row.get('point_readings',[])) | score_variants.get(position,set()))
        for position, fields in confirmed.items():
            if 'name' in fields:
                variants.pop(position,None)
            if 'value' in fields:
                score_variants.pop(position,None)

    if not observations:raise ValueError('No complete VS score rows found.')
    earned=score_date_for_day(upload_date,day) if day is not None else None
    if earned is not None and earned>upload_date:
        earned=None
    score_date=earned.isoformat() if earned else None
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
                     'point_readings':sorted(set(row.get('point_readings',[])) | score_variants.get(pos,set())),
                     'points':int(row['value']) if row['value'].isdigit() else None,
                     'review_reasons':reasons[pos],'score_date':score_date})
    return {'rows':rows,'weekday':DAYS[day] if earned else '', 'score_date':score_date,
            'upload_date':upload_date.isoformat(),'pages':total_pages,'recovered_positions':recovered,
            'rechecked_missing_players':rechecked,'found_missing_players':found,
            'missing_positions':[p for p in range(1,max(observations)+1) if p not in observations]}

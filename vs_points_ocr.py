"""VS daily scores: stable pages, visible full rows, selected weekday, no pinned self row."""
from __future__ import annotations
import base64
from collections import Counter, defaultdict
import re
from datetime import date, timedelta
from pathlib import Path
import tempfile
import cv2
import numpy as np
from extract_ranking_pages import detect, extract
from scan_rankings import card_borders, pinned_top, estimate_row_height, text_with_conf, enlarge, best_start, retry_name_crop

from player_identity import corrected_name, name_key

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


def read_page(image, page, period, upper, targets=None, anchors=None):
    h,w=image.shape[:2]; scale=w/1080
    bottom=pinned_top(image)
    borders=card_borders(image,bottom)
    rows=[]
    for top, end in zip(borders,borders[1:]):
        if not (.90*period <= end-top <= 1.12*period and top >= upper-8*scale and end <= bottom-4*scale):
            continue
        cy=round((top+end)/2)
        def crop(x0,x1,y0,y1):
            return image[max(top,round(cy+y0*scale)):min(end,round(cy+y1*scale)),round(x0*scale):round(x1*scale)]
        avatar=crop(240,347,-52,53)
        signature=cv2.resize(cv2.cvtColor(avatar,cv2.COLOR_BGR2GRAY),(24,24))
        matched = None
        if anchors is not None:
            matches = [anchor for anchor in anchors
                       if abs(cy-anchor['center']) < period*.30
                       and cv2.absdiff(signature,anchor['signature']).mean() < 14]
            if len(matches) != 1:
                continue
            matched = matches[0]['internal_position']
        position, pc=text_with_conf(enlarge(crop(92,185,-35,39)),7,'0123456789')
        position=re.sub(r'\D','',position)
        position=int(position) if position.isdigit() and 1<=int(position)<=100 else None
        if targets is not None and position not in targets:continue
        name_crop=crop(350,808,-57,-5)
        name, nc=text_with_conf(enlarge(name_crop),7)
        if not name or nc<55:
            alternate,confidence=text_with_conf(enlarge(name_crop,3),13)
            if alternate and (not name or confidence>nc):name,nc=alternate,confidence
            name,nc=retry_name_crop(name_crop,name,nc)
        name=re.sub(r'\s+',' ',name).strip()
        zero=isolated_zero(crop(806,1035,-25,44))
        val,vc=('0',98.) if zero else text_with_conf(enlarge(crop(806,1035,-25,44)),7,'0123456789,')
        val=re.sub(r'\D','',val)
        if not val or vc<55:
            # Tighten the actual ink and add a white border. A tiny single zero
            # in a wide stat cell is otherwise often rejected by Tesseract.
            gray=cv2.cvtColor(crop(806,1035,-25,44),cv2.COLOR_BGR2GRAY)
            ys,xs=np.where(gray<min(180,float(np.median(gray))-30))
            if len(xs):
                tight=gray[max(0,int(ys.min())-5):int(ys.max())+6,max(0,int(xs.min())-5):int(xs.max())+6]
                tight=cv2.copyMakeBorder(enlarge(tight,4),30,30,30,30,cv2.BORDER_CONSTANT,value=255)
                retry,confidence=text_with_conf(tight,7)
                if re.fullmatch(r'[Oo]',retry.strip()):retry='0'
                retry=re.sub(r'\D','',retry)
                if retry and (not val or confidence>vc):val,vc=retry,confidence
        if not val:
            retry,confidence=text_with_conf(enlarge(crop(806,1035,-25,44),3),13,'0123456789,')
            val,vc=re.sub(r'\D','',retry),confidence
        evidence=image[top:end,round(45*scale):round(1040*scale)]
        ok, encoded=cv2.imencode('.webp',evidence,[cv2.IMWRITE_WEBP_QUALITY,75])
        ok_avatar, encoded_avatar=cv2.imencode('.webp',avatar,[cv2.IMWRITE_WEBP_QUALITY,80])
        rows.append({'name':name,'value':val,'name_conf':nc,'value_conf':vc,
                     'position_read':position,'position_conf':pc,'page':page,
                     'center':cy,'signature':signature,'matched_position':matched,
                     'avatar_base64':base64.b64encode(encoded_avatar).decode() if ok_avatar else None,
                     'evidence_base64':base64.b64encode(encoded).decode() if ok else None})
    return rows


def review_flags(observations, names, scores, resolve, master_keys):
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
            flags.append('Points exceed the row above')
            reasons[previous[0]].append('Next row has higher points; verify this value')
        previous = (position, points) if points is not None else None
    return reasons


def recheck_neighbors(observations, sources, reasons, resolve, progress, read_frame):
    groups = defaultdict(list)
    for position, row in observations.items():
        if reasons[position] and row['page'] in sources:
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
                readings = read_frame(image,anchors)
                for reading in readings:
                    neighbors[reading['matched_position']].append(reading)
            for row in anchors:
                position = row['internal_position']
                if len(neighbors[position]) != 2:
                    continue
                views = [row,*neighbors[position]]
                for field, confidence in (('name','name_conf'),('value','value_conf')):
                    if field == 'name':
                        keys = [name_key(resolve(view[field])) for view in views]
                    else:
                        keys = [int(view[field]) if view[field].isdigit() else None for view in views]
                    votes = Counter(key for key in keys if key is not None and key != '')
                    if not votes:
                        continue
                    winner, count = votes.most_common(1)[0]
                    if count < 2:
                        if len(votes) > 1:
                            label = 'Player name' if field == 'name' else 'Points'
                            row.setdefault('neighbor_conflicts',[]).append(
                                f'{label} differs across adjacent frames')
                        continue
                    best = max((view for view,key in zip(views,keys) if key == winner),
                               key=lambda view:view[confidence])
                    row[field],row[confidence] = best[field],best[confidence]
                    confirmed[position].add(field)
    finally:
        for cap in captures.values():
            cap.release()
    return confirmed


def read_vs_neighbor(image, anchors, day):
    try:
        if selected_weekday(image,False) != day:
            return []
    except ValueError:
        return []
    return read_page(image,0,189*image.shape[1]/1080,
                     round(.225*image.shape[0]),anchors=anchors)


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
                rows=read_page(image,total_pages+1,period,round(.225*image.shape[0]))
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
                    candidates=read_page(image,-gap,189*image.shape[1]/1080,round(.225*image.shape[0]),{gap})
                    if candidates:
                        row=candidates[0];row['internal_position']=gap
                        observations[gap]=row
                        page_sources[-gap]=(left[0],int(frame))
                        recovered.append(gap)
                        break
            finally:cap.release()
        reasons = review_flags(observations, variants, score_variants, resolve, master_keys)
        confirmed = recheck_neighbors(
            observations, page_sources, reasons, resolve, progress,
            lambda image,anchors: read_vs_neighbor(image,anchors,next(iter(days))))
        for position, fields in confirmed.items():
            if 'name' in fields:
                variants.pop(position,None)
            if 'value' in fields:
                score_variants.pop(position,None)
    if not observations:raise ValueError('No complete VS score rows found.')
    day=next(iter(days)); earned=score_date_for_day(upload_date,day)
    if earned>upload_date:
        raise ValueError('The selected VS day is later than the upload date. Check the daily tab and your local date.')
    reasons = review_flags(observations, variants, score_variants, resolve, master_keys)
    rows=[]
    for pos,row in sorted(observations.items()):
        if reasons[pos] and row['page'] in page_sources:
            reasons[pos].append('Adjacent frames did not resolve all review flags')
        rows.append({**{k:v for k,v in row.items() if k not in ('signature','center','matched_position')},
                     'name':resolve(row['name']),
                     'points':int(row['value']) if row['value'].isdigit() else None,
                     'review_reasons':reasons[pos],'score_date':earned.isoformat()})
    return {'rows':rows,'weekday':DAYS[day],'score_date':earned.isoformat(),
            'upload_date':upload_date.isoformat(),'pages':total_pages,'recovered_positions':recovered,
            'missing_positions':[p for p in range(1,max(observations)+1) if p not in observations]}

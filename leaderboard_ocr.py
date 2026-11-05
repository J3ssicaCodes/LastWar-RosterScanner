"""Leaderboard OCR for Media Upload: screenshots and paused video pages share a pipeline.

Never fabricate leaderboard positions or OCR partially visible row cards.  Internal
leaderboard position is only an alignment/audit key; it is not a player attribute.
Original media remains temporary. OCR outputs + audit metadata are persisted by
media_upload.py only after staging, not directly into the official Player List.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path
from collections import defaultdict, Counter
from typing import Callable

import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps
from scipy.signal import find_peaks, medfilt

ALGORITHM_VERSION = 'leaderboard-v1.0'
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm'}
TAB_NAMES = ('power', 'kills', 'donations')
TAB_LABELS = {'power': 'Power', 'kills': 'Kills', 'donations': 'Donation'}
# All horizontal reference fields are fractions of a 1080 wide example layout.
CROPS = {'position': (75/1080, 158/1080), 'avatar': (211/1080, 316/1080),
         'badge': (336/1080,399/1080), 'name': (410/1080,804/1080),
         'stat': (808/1080,1065/1080)}
DEFAULTS = {'video_stride':3, 'dwell_seconds':0.38, 'dwell_frames':15,
            'motion_threshold':2.5, 'duplicate_threshold':2.2,
            'name_confidence':55.0, 'number_confidence':55.0,
            'row_pitch_per_width':189/1080, 'pitch_min_factor':0.94,
            'pitch_max_factor':1.12, 'rank_template_confidence':0.70}

@dataclass
class Layout:
    width: int
    height: int
    tabs: list[tuple[float,float,float,float]]
    header_bottom: float
    viewport_bottom: float
    confidence: str
    warnings: list[str] = field(default_factory=list)
    pinned_top: float|None = None

@dataclass
class Geometry:
    pitch: float
    boundaries: list[int]
    confidence: str
    top: int
    bottom: int
    rejected: list[dict] = field(default_factory=list)


def image_digest(image):
    # Stable across re-encodings of visually identical input after normalization.
    return hashlib.sha256(ALGORITHM_VERSION.encode()+image.tobytes()).hexdigest()


def normalize_image(value):
    if isinstance(value, (str,Path)):
        with Image.open(str(value)) as img:
            return cv2.cvtColor(np.asarray(ImageOps.exif_transpose(img).convert('RGB')),cv2.COLOR_RGB2BGR)
    if isinstance(value, np.ndarray) and value.ndim==3:
        return value
    raise ValueError('Unsupported image input')


def ingest_screenshots(paths, audit=None):
    audit = audit if audit is not None else []
    pages=[];seen=set()
    for path in paths:
        frame=normalize_image(path)
        digest=image_digest(frame)
        if digest in seen:
            audit.append({'event':'exact_duplicate_screenshot','source':Path(path).name,'hash':digest})
            continue
        seen.add(digest)
        pages.append({'image':frame,'source':Path(path).name,'frame':None,'timestamp':None,'hash':digest,'dwell':None,'video':False})
    return pages


def _ocr_data(image,config='--psm 7'):
    try:
        data=pytesseract.image_to_data(image,config=config,output_type=pytesseract.Output.DICT,timeout=9)
    except (RuntimeError,pytesseract.TesseractError):
        return ('',0.0)
    text=[];conf=[]
    for token,c in zip(data.get('text',[]),data.get('conf',[])):
        if token.strip():
            text.append(token.strip())
            try:
                if float(c)>=0:conf.append(float(c))
            except (ValueError,TypeError):pass
    return (' '.join(text).strip(), float(np.mean(conf)) if conf else 0.0)


def _top_words(image):
    h,w=image.shape[:2]
    max_y=int(h*.70)
    # Once per layout / tab series, not once for every row.
    scaled=cv2.resize(image[:max_y],(max(400,int(w*.9)),max(1,int(max_y*.9))))
    try:
        data=pytesseract.image_to_data(scaled,config='--psm 11',output_type=pytesseract.Output.DICT,timeout=14)
    except (RuntimeError,pytesseract.TesseractError):return []
    result=[]
    for i,raw in enumerate(data.get('text',[])):
        word=raw.strip().lower().strip('.:')
        if not word:continue
        try:conf=float(data['conf'][i])
        except (KeyError,ValueError):conf=0
        result.append((word,(data['left'][i]+data['width'][i]/2)/(scaled.shape[1]),
                       (data['top'][i]+data['height'][i]/2)/scaled.shape[0]*max_y/h,conf))
    return result


def _green_pin_boundary(image, min_fraction=.60):
    h,w=image.shape[:2]
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    ymin=int(h*min_fraction)
    sub=hsv[ymin:,int(w*.09):int(w*.94)]
    mask=cv2.inRange(sub,np.array([32,60,55]),np.array([91,255,255]))
    coverage=np.mean(mask>0,axis=1)
    # Require broad, sustained green, not isolated avatar or green button pixels.
    ys=np.where(coverage>.36)[0]
    if len(ys)<max(10,int(w*.016)):return None
    # A large connected green band with near-contiguous visible rows.
    groups=np.split(ys,np.where(np.diff(ys)>5)[0]+1)
    groups=[g for g in groups if len(g)>=max(10,int(w*.016)) and g[-1]-g[0]>=max(11,int(w*.016))]
    return (ymin+int(groups[0][0]))/h if groups else None


def detect_leaderboard_layout(image):
    h,w=image.shape[:2]
    if w<360 or h<500:return None
    words=_top_words(image)
    tabs={'power':[],'kills':[],'donations':[]}
    head=[]
    for word,x,y,conf in words:
        if conf<15:continue
        if word in ('power','powers'):tabs['power'].append((x,y))
        elif word in ('kills','kill'):tabs['kills'].append((x,y))
        elif word in ('donation','donations','donate'):tabs['donations'].append((x,y))
        elif word in ('ranking','commander','rankings','commanders'):
            head.append(y)
    labels=[(key,*p[0]) for key,p in tabs.items() if p and p[0][1]<.65]
    # Require at least two real tab captions plus a table header; otherwise
    # do not mistake the older member list or player profile for a leaderboard.
    if len(labels)<2 or not head:return None
    band_y=float(np.median([v[2] for v in labels]))
    if any(abs(v[2]-band_y)>.10 for v in labels):return None
    header_y=[y for y in head if y>band_y+.035]
    if not header_y:return None
    header=float(min(header_y))
    # A row header contains text, so begin after its lower border. Keep margin.
    header_bottom=min(.79,header+.025)
    pinned=_green_pin_boundary(image)
    bottom=float(pinned-.006) if pinned is not None else .965
    if bottom-header_bottom<max(.18,2*DEFAULTS['row_pitch_per_width']*w/h):return None
    tab_y=max(.02,band_y-.031)
    tab_h=.065
    # Use observed centers; only infer the missing caption's center from the other two.
    centers=[tabs[k][0][0] if tabs[k] else None for k in TAB_NAMES]
    if centers[0] is None and centers[1] is not None and centers[2] is not None:
        centers[0]=centers[1]-(centers[2]-centers[1])
    if centers[1] is None and centers[0] is not None and centers[2] is not None:
        centers[1]=(centers[0]+centers[2])/2
    if centers[2] is None and centers[0] is not None and centers[1] is not None:
        centers[2]=centers[1]+(centers[1]-centers[0])
    if any(v is None or not (.06<v<.94) for v in centers):return None
    centers=list(map(float,centers))
    if not all(.08<centers[i+1]-centers[i]<.42 for i in (0,1)):return None
    tabs_boxes=[]
    for i,cx in enumerate(centers):
        left=(centers[i-1]+cx)/2 if i else max(.01,cx-(centers[1]-cx)/2)
        right=(cx+centers[i+1])/2 if i<2 else min(.99,cx+(cx-centers[i-1])/2)
        tabs_boxes.append((left,tab_y,right,min(header-.01,tab_y+tab_h)))
    return Layout(w,h,tabs_boxes,header_bottom,bottom,'strong' if len(labels)==3 else 'moderate',[],pinned)


def detect_selected_tab(image,layout):
    h,w=image.shape[:2]
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    scores=[]
    for x0,y0,x1,y1 in layout.tabs:
        region=hsv[max(0,int(y0*h)):min(h,int(y1*h)),max(0,int(x0*w)):min(w,int(x1*w))]
        if not region.size:scores.append(0.0);continue
        mask=cv2.inRange(region,(7,160,180),(27,255,255))
        scores.append(float(np.mean(mask>0)))
    ranking=sorted(range(3),key=lambda i:scores[i],reverse=True)
    best,second=scores[ranking[0]],scores[ranking[1]]
    # Avoid false labels from subtle shades or banners.
    if best<.065 or best<second*1.35 or best-second<.022:
        return None, {'orange_coverage':scores,'error':'Selected leaderboard tab ambiguous'}
    return TAB_NAMES[ranking[0]], {'orange_coverage':scores,'selected':TAB_NAMES[ranking[0]]}


def _motion_thumb(image,layout):
    h,w=image.shape[:2]
    # Completely remove the animated header and the current user's fixed row.
    y0=int(layout.header_bottom*h);y1=int(layout.viewport_bottom*h)
    crop=image[y0:y1,int(w*.08):int(w*.95)]
    if crop.size==0:return None
    return cv2.resize(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(112,184),interpolation=cv2.INTER_AREA)


def _motion_difference(a,b):
    return float(np.mean(cv2.absdiff(a,b)))


def extract_video_pages(video_path,progress=None,settings=None,audit=None):
    cfg={**DEFAULTS,**(settings or {})}
    audit=audit if audit is not None else []
    cap=cv2.VideoCapture(str(video_path))
    if not cap.isOpened():raise ValueError('Could not open video recording.')
    fps=cap.get(cv2.CAP_PROP_FPS)
    fps=fps if np.isfinite(fps) and fps>2 else 30.0
    count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if count<=0 or count/fps>420:
        cap.release();raise ValueError('Recording has invalid duration or exceeds seven minutes.')
    first_layout=None;prev=None;stable_start=None;stable_last=None;stops=[]
    stride=int(cfg['video_stride'])
    frame_number=-1
    try:
        while True:
            frame_number+=1
            if frame_number%stride!=0:
                if not cap.grab():break
                continue
            ok,frame=cap.read()
            if not ok:break
            if first_layout is None:
                first_layout=detect_leaderboard_layout(frame)
                if first_layout is None:continue
            thumb=_motion_thumb(frame,first_layout)
            if thumb is None:continue
            if prev is not None:
                diff=_motion_difference(thumb,prev)
                if diff<float(cfg['motion_threshold']):
                    if stable_start is None:stable_start=max(0,frame_number-stride)
                    stable_last=frame_number
                else:
                    if stable_start is not None and stable_last is not None:
                        span=stable_last-stable_start
                        if span>=cfg['dwell_frames'] and span/fps>=cfg['dwell_seconds']:
                            stops.append((stable_start,stable_last))
                    stable_start=stable_last=None
            prev=thumb
            if progress and frame_number%max(30,stride*35)<stride:
                progress(4+min(30,int(30*frame_number/max(count,1))),100,
                         f'Detecting video pauses: {frame_number}/{count} frames')
        if stable_start is not None and stable_last is not None:
            span=stable_last-stable_start
            if span>=cfg['dwell_frames'] and span/fps>=cfg['dwell_seconds']:
                stops.append((stable_start,stable_last))
        audit.append({'event':'dwell_detection','source':Path(video_path).name,
                      'source_frames':count,'fps':round(float(fps),4),'pauses':len(stops)})
        if first_layout is None:
            raise ValueError('Leaderboard tabs and table header not recognized. This is not a supported Power/Kills/Donation leaderboard video.')
        if not stops:
            raise ValueError('No stable leaderboard pages found. Pause briefly between scrolls to show complete rows.')
        pages=[];thumbs=[]
        for i,(start,end) in enumerate(stops):
            midpoint=(start+end)//2
            # Seek original full-resolution frame, do not OCR reduced motion images.
            # Evaluate center and nearby frames for motion blur.
            candidates=[]
            for n in (midpoint,max(start,midpoint-2),min(end,midpoint+2)):
                cap.set(cv2.CAP_PROP_POS_FRAMES,n)
                ok,im=cap.read()
                if ok and im is not None:
                    gray=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY)
                    sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
                    candidates.append((sharpness,n,im))
            if not candidates:continue
            _,frame_index,image=max(candidates,key=lambda x:x[0])
            thumb=_motion_thumb(image,first_layout)
            if thumb is None:continue
            near=next((p for p,t in enumerate(thumbs) if _motion_difference(thumb,t)<cfg['duplicate_threshold']),None)
            digest=image_digest(image)
            record={'source':Path(video_path).name,'frame':frame_index,
                    'timestamp':round(frame_index/fps,3),'duration':round((end-start)/fps,3),
                    'hash':digest,'duplicate_of':near}
            audit.append({'event':'pause_frame',**record})
            if near is not None:continue
            thumbs.append(thumb)
            pages.append({'image':image,'source':record['source'],'frame':frame_index,
                          'timestamp':record['timestamp'],'hash':digest,'dwell':record['duration'],
                          'video':True,'interval':(start,end),'fps':fps,'source_video':str(video_path)})
            if progress:progress(34+int(9*(i+1)/len(stops)),100,
                                 f'Selected {len(pages)} distinct pause images')
        if not pages:raise ValueError('No distinct leaderboard pages found.')
        return pages
    finally:
        cap.release()


def _boundary_strength(image,layout):
    h,w=image.shape[:2]
    # Average multiple strips around the left card border to suppress avatars.
    x0=int(.045*w);x1=int(.175*w)
    strip=cv2.cvtColor(image[:,x0:x1],cv2.COLOR_BGR2GRAY).astype(np.float32)
    profile=np.median(strip,axis=1)
    # Smooth broad UI bands, retain border edges.
    smooth=cv2.GaussianBlur(profile[:,None],(1,5),0).ravel()
    signal=np.abs(np.gradient(smooth))
    return signal


def calibrate_row_geometry(images,layout):
    # Each page may be different scroll offset; pitch is common across pages.
    pitch_prior=layout.width*DEFAULTS['row_pitch_per_width']
    bounds_by_page=[];intervals=[]
    for image in images:
        lay=detect_leaderboard_layout(image) or layout
        top=int(lay.header_bottom*image.shape[0]);bottom=int(lay.viewport_bottom*image.shape[0])
        profile=_boundary_strength(image,lay)
        window=profile[top:bottom]
        if window.size<10:
            bounds_by_page.append([]);continue
        magnitude=max(3.2,float(np.percentile(window,83))*1.3)
        peaks,_=find_peaks(window,distance=max(12,int(pitch_prior*.14)),prominence=magnitude)
        yvals=sorted(int(top+p) for p in peaks)
        # Cluster peaks in the same thick border line.
        clusters=[]
        for y in yvals:
            if clusters and y-clusters[-1][-1]<max(5,int(pitch_prior*.05)):
                clusters[-1].append(y)
            else:clusters.append([y])
        boundaries=[int(round(np.mean(x))) for x in clusters]
        bounds_by_page.append(boundaries)
        for a,b in zip(boundaries,boundaries[1:]):
            diff=b-a
            if pitch_prior*.80<=diff<=pitch_prior*1.23:
                intervals.append(diff)
    strong=len(intervals)>=5
    pitch=float(np.median(intervals)) if strong else pitch_prior
    confidence='strong' if strong and np.std(intervals)/max(1,pitch)<.13 else 'low'
    if not strong:confidence='low'
    return pitch,bounds_by_page,confidence


def extract_complete_rows(image,geometry):
    results=[]
    for start,end in zip(geometry.boundaries,geometry.boundaries[1:]):
        if start<geometry.top or end>geometry.bottom:
            geometry.rejected.append({'top':start,'bottom':end,'reason':'clipped by scrolling viewport'})
            continue
        ratio=(end-start)/max(geometry.pitch,1)
        if not (DEFAULTS['pitch_min_factor']<=ratio<=DEFAULTS['pitch_max_factor']):
            geometry.rejected.append({'top':start,'bottom':end,'reason':'height disagrees with calibrated pitch'})
            continue
        # Require actual observed row boundary pair. Do not extend or infer.
        if end-start<24:continue
        results.append((int(start),int(end)))
    return results


def _crop(image,xlimits,ylimits,scale=1):
    h,w=image.shape[:2]
    x0=max(0,int(w*xlimits[0]));x1=min(w,int(w*xlimits[1]))
    y0=max(0,int(h*ylimits[0]));y1=min(h,int(h*ylimits[1]))
    if x1<=x0 or y1<=y0:return np.zeros((2,2,3),np.uint8)
    piece=image[y0:y1,x0:x1]
    return cv2.resize(piece,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC) if scale!=1 else piece


def _name_retry(image):
    variants=[]
    for interp,psm in ((3,13),(3,7)):
        big=cv2.resize(image,None,fx=interp,fy=interp,interpolation=cv2.INTER_CUBIC)
        text,conf=_ocr_data(big,config=f'--psm {psm}')
        variants.append({'text':text,'confidence':round(conf,1),'mode':f'psm{psm}'})
    return variants


def _stat_candidates(image):
    variants=[('color',image)]
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    variants.append(('gray',gray))
    variants.append(('large',cv2.resize(gray,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)))
    for t in (115,165,205):
        variants.append((f'binary{t}',cv2.threshold(gray,t,255,cv2.THRESH_BINARY)[1]))
    variants.append(('adaptive',cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,31,10)))
    answers=[]
    for name,im in variants:
        text,conf=_ocr_data(im,config='--psm 7 -c tessedit_char_whitelist=0123456789,')
        digits=re.sub(r'[\s,]','',text)
        value=int(digits) if digits.isdigit() else None
        answers.append({'value':value,'text':text,'confidence':round(conf,1),'method':name})
    return answers


def _badge_templates(path=None):
    target=Path(path) if path else Path(__file__).with_name('rank_badge_templates.npz')
    if not target.exists():return {}
    try:
        with np.load(target,allow_pickle=False) as bundle:
            return {key.upper():bundle[key] for key in bundle.files if re.fullmatch('r[1-5]',key,re.I)}
    except (OSError,ValueError):return {}


def _badge_rank(badge,templates):
    # Digit OCR fallback when no validated template file was supplied.
    observed=cv2.resize(badge,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
    text,conf=_ocr_data(observed,config='--psm 10 -c tessedit_char_whitelist=Rr12345')
    match=re.search(r'R\s*([1-5])',text,re.I)
    if match and conf>=55:return 'R'+match.group(1),conf,'ocr'
    hsv=cv2.cvtColor(badge,cv2.COLOR_BGR2HSV)
    color=cv2.inRange(hsv,(5,65,28),(28,255,250))
    scored=[]
    for key,tmp in templates.items():
        templ=(tmp if tmp.ndim==2 else cv2.cvtColor(tmp,cv2.COLOR_BGR2GRAY)).astype(np.uint8)
        resized=cv2.resize(color,(templ.shape[1],templ.shape[0]))
        val=float(cv2.matchTemplate(resized,templ,cv2.TM_CCOEFF_NORMED)[0,0])
        scored.append((val,key))
    if scored:
        best,key=max(scored)
        if best>=DEFAULTS['rank_template_confidence']:
            return key,100*best,'template'
    return 'Unknown',conf,'unresolved'


def recognize_player_row(row_image, layout, tab, source=None, geometry=None, cache=None, templates=None):
    """Recognize one *previously verified complete* row with scoped text crops."""
    cache=cache if cache is not None else {}
    templates=templates if templates is not None else {}
    digest=image_digest(row_image)
    key=tab+':'+digest
    if key in cache:
        result=dict(cache[key]);result['cached']=True
        return result
    h,w=row_image.shape[:2]
    # Avoid outer card border and use consistent interior across resolutions.
    y=(.13,.88)
    position_img=_crop(row_image,CROPS['position'],y,scale=2)
    raw_position,position_conf=_ocr_data(position_img,'--psm 7 -c tessedit_char_whitelist=0123456789')
    pos_string=re.sub(r'\D','',raw_position)
    pos=int(pos_string) if pos_string and 1<=int(pos_string)<=100 else None
    name_img=_crop(row_image,CROPS['name'],(.18,.78),scale=2)
    name,name_conf=_ocr_data(name_img,'--psm 7')
    # No Unicode simplification; strip surrounding OCR whitespace only.
    name=name.strip()
    reasons=[];alts=[]
    if not name or name_conf<DEFAULTS['name_confidence'] or len(name)>80 or name.endswith('…'):
        alts=_name_retry(_crop(row_image,CROPS['name'],(.18,.78)))
        winner=max([{'text':name,'confidence':name_conf,'mode':'psm7'},*alts],key=lambda x:x['confidence'])
        if not name and winner['text']:name=winner['text']
        reasons.append('Name OCR confidence is low or the name may be truncated')
    stat_img=_crop(row_image,CROPS['stat'],(.13,.90),scale=2)
    stat_text,stat_conf=_ocr_data(stat_img,'--psm 7 -c tessedit_char_whitelist=0123456789,')
    digits=re.sub(r'[\s,]','',stat_text)
    stat=int(digits) if digits.isdigit() else None
    stat_alternatives=[]
    if stat is None or stat_conf<DEFAULTS['number_confidence']:
        stat_alternatives=_stat_candidates(_crop(row_image,CROPS['stat'],(.13,.90)))
        values=[x['value'] for x in stat_alternatives if x['value'] is not None]
        counts=Counter(values)
        consensus=counts.most_common(1)[0] if counts else None
        if consensus and consensus[1]>=2 and (stat is None or stat==consensus[0]):
            stat=consensus[0]
        reasons.append('Statistic needs review: unreadable or low OCR confidence')
    if pos is None:reasons.append('Internal leaderboard position unreadable')
    badge_img=_crop(row_image,CROPS['badge'],(.15,.84))
    rank,rank_conf,rank_method=_badge_rank(badge_img,templates)
    if rank=='Unknown':reasons.append('Rank badge not confidently recognized')
    if not name:reasons.append('Player name missing')
    avatar=_crop(row_image,CROPS['avatar'],(.10,.94))
    # Preserve avatar losslessly. Keep a compact version if image dimensions are very large.
    if avatar.shape[0]>180 or avatar.shape[1]>180:
        factor=min(180/avatar.shape[0],180/avatar.shape[1])
        avatar=cv2.resize(avatar,None,fx=factor,fy=factor,interpolation=cv2.INTER_AREA)
    ok,encoded=cv2.imencode('.png',avatar)
    avatar_bytes=encoded.tobytes() if ok else b''
    result={'name':name,'rank':rank,'power':stat if tab=='power' else None,
            'kills':stat if tab=='kills' else None,'donations':stat if tab=='donations' else None,
            'tab':tab,'leaderboard_position':pos,'avatar_base64':base64.b64encode(avatar_bytes).decode() if avatar_bytes else None,
            'avatar_hash':hashlib.sha256(avatar_bytes).hexdigest() if avatar_bytes else None,
            'review_required':bool(reasons),'review_reasons':reasons,
            'source_image':source or 'unknown','position_text':raw_position,
            'name_text':name,'stat_text':stat_text,'name_confidence':round(name_conf,1),
            'stat_confidence':round(stat_conf,1),'rank_confidence':round(rank_conf,1),
            'rank_method':rank_method,'name_alternatives':alts,'stat_alternatives':stat_alternatives,
            'source_type':'leaderboard','source_hash':digest}
    cache[key]=dict(result)
    return result


def _identity_score(a,b):
    if a['tab']!=b['tab']:return 0
    score=0
    if a.get('leaderboard_position') is not None and a.get('leaderboard_position')==b.get('leaderboard_position'):score+=5
    if a.get('name') and a['name'].casefold()==(b.get('name') or '').casefold():score+=2
    if a.get('avatar_hash') and a['avatar_hash']==b.get('avatar_hash'):score+=1
    statkey=a['tab']
    if a.get(statkey) is not None and a.get(statkey)==b.get(statkey):score+=1
    return score


def _observation_quality(item):
    return ((0 if item['review_required'] else 15)+item['name_confidence']/20+
            item['stat_confidence']/20+(4 if item['leaderboard_position'] is not None else 0))


def merge_player_observations(observations):
    """Merge within (tab, position) only, not across different selected tabs."""
    grouped=defaultdict(list);unpositioned=[]
    for item in observations:
        pos=item.get('leaderboard_position')
        if pos is None:unpositioned.append(dict(item));continue
        grouped[(item['tab'],pos)].append(item)
    merged=[]
    for (tab,pos),variants in grouped.items():
        variants.sort(key=_observation_quality,reverse=True)
        best=dict(variants[0])
        if len(variants)>1:
            for candidate in variants[1:]:
                if best.get('name') and candidate.get('name') and best['name'].casefold()!=candidate['name'].casefold():
                    best['review_reasons'].append('Overlapping pages disagree on the player name')
                if best.get(tab) is not None and candidate.get(tab) is not None and best[tab]!=candidate[tab]:
                    best['review_reasons'].append('Overlapping pages disagree on the statistic')
                if best['rank']!='Unknown' and candidate['rank']!='Unknown' and best['rank']!=candidate['rank']:
                    best['review_reasons'].append('Overlapping pages disagree on rank')
            best['review_required']=bool(best['review_reasons'])
        best['observations']=len(variants)
        merged.append(best)
    # Never invent a missing position for unpositioned observations.
    for item in unpositioned:item['observations']=1
    merged+=unpositioned
    return sorted(merged,key=lambda x:(TAB_NAMES.index(x['tab']),x['leaderboard_position'] if x['leaderboard_position'] else 999))


def validate_results(players,expected_count=None):
    warnings=[];missing={};review_count=0
    for tab in sorted({p['tab'] for p in players}):
        positioned=sorted((p for p in players if p['tab']==tab and p.get('leaderboard_position') is not None),key=lambda p:p['leaderboard_position'])
        positions={p['leaderboard_position'] for p in positioned}
        holes=[i for i in range(min(positions),max(positions)+1) if i not in positions] if positions else []
        missing[tab]=holes
        if holes:warnings.append(f'{TAB_LABELS[tab]} has {len(holes)} missing internal positions: '+', '.join(map(str,holes[:20])))
        for left,right in zip(positioned,positioned[1:]):
            if left.get(tab) is not None and right.get(tab) is not None and left[tab]<right[tab]:
                right['review_required']=True
                right['review_reasons'].append('Statistic breaks the descending leaderboard order')
        if expected_count and len(positions)!=expected_count:
            warnings.append(f'{TAB_LABELS[tab]}: {len(positions)} positions observed; expected count {expected_count} is only a validation hint')
    for p in players:
        p['review_reasons']=list(dict.fromkeys(p['review_reasons']))
        p['review_required']=bool(p['review_reasons'])
        review_count+=int(p['review_required'])
    return {'missing_positions':missing,'missing_count':sum(map(len,missing.values())),
            'review_count':review_count,'warnings':warnings}


def _extract_page_observations(pages,progress,audit,settings=None):
    if not pages:return [],[],0
    cfg={**DEFAULTS,**(settings or {})}
    first_layout=None
    for p in pages:
        first_layout=detect_leaderboard_layout(p['image'])
        if first_layout:break
    if first_layout is None:raise ValueError('Leaderboard Power/Kills/Donation tabs and Ranking/Commander header not detected.')
    pitch,bounds_by_page,confidence=calibrate_row_geometry([p['image'] for p in pages],first_layout)
    templates=_badge_templates()
    if not templates:
        audit.append({'event':'rank_templates_unavailable','detail':'Using conservative rank OCR; unresolved badges require review'})
    observations=[];cache={};seen_hash=set();complete_count=0
    for i,p in enumerate(pages):
        im=p['image'];layout=detect_leaderboard_layout(im)
        if not layout:
            audit.append({'event':'unsupported_layout','source':p['source'],'frame':p.get('frame')})
            continue
        tab,tab_audit=detect_selected_tab(im,layout)
        if tab is None:
            audit.append({'event':'ambiguous_tab','source':p['source'],'frame':p.get('frame'),**tab_audit})
            continue
        g=Geometry(pitch,bounds_by_page[i],confidence,int(layout.header_bottom*im.shape[0]),
                   int(layout.viewport_bottom*im.shape[0]))
        accepted=extract_complete_rows(im,g)
        audit.append({'event':'page_geometry','source':p['source'],'frame':p.get('frame'),
                      'timestamp':p.get('timestamp'),'tab':tab,'pitch':round(pitch,1),
                      'calibration':confidence,'complete_rows':len(accepted),
                      'rejected':g.rejected,'image_hash':p['hash'],
                      'source_count':len(pages)})
        for y0,y1 in accepted:
            complete_count+=1
            row=im[y0:y1,:]
            hashkey=tab+':'+image_digest(row)
            if hashkey in seen_hash:continue
            seen_hash.add(hashkey)
            obs=recognize_player_row(row,layout,tab,
                source=f"{p['source']}@{p['timestamp']:.2f}s" if p.get('timestamp') is not None else p['source'],
                cache=cache,templates=templates)
            observations.append(obs)
        if progress:progress(44+round(38*(i+1)/len(pages)),100,
                             f'Analyzing {i+1}/{len(pages)} pages; {len(observations)} observations')
    return observations,sorted({o['tab'] for o in observations}),complete_count


def recover_missing_rows(source,missing_positions,pages,observations,progress=None,audit=None):
    """Targeted recovery from transitions between neighboring paused video pages.

    Never uses clipped rows: target must first pass boundary validation.  Only
    scan transitions if they are adjacent to missing leaderboard positions.
    """
    audit=audit if audit is not None else []
    if not missing_positions or not pages or not any(p.get('video') for p in pages):return []
    known={(x['tab'],x['leaderboard_position']) for x in observations if x.get('leaderboard_position') is not None}
    recovered=[]
    by_video=defaultdict(list)
    for p in pages:
        if p.get('video'):by_video[p.get('source_video')].append(p)
    for video_path,group in by_video.items():
        group.sort(key=lambda p:p['frame'])
        cap=cv2.VideoCapture(video_path)
        if not cap.isOpened():continue
        try:
            for left,right in zip(group,group[1:]):
                possible={t:{pos for tab,pos in missing_positions if tab==t} for t in TAB_NAMES}
                left_frame=left['frame'];right_frame=right['frame']
                if right_frame-left_frame<8:continue
                max_samples=min(18,max(0,(right_frame-left_frame)//3))
                if max_samples<=0:continue
                for frame_id in np.linspace(left_frame+2,right_frame-2,max_samples,dtype=int):
                    cap.set(cv2.CAP_PROP_POS_FRAMES,int(frame_id))
                    ok,img=cap.read()
                    if not ok:continue
                    layout=detect_leaderboard_layout(img)
                    if not layout:continue
                    tab,_=detect_selected_tab(img,layout)
                    targets=possible.get(tab,set()) if tab else set()
                    if not targets:continue
                    pitch,bounds,confidence=calibrate_row_geometry([img],layout)
                    geom=Geometry(pitch,bounds[0],confidence,int(layout.header_bottom*img.shape[0]),
                                  int(layout.viewport_bottom*img.shape[0]))
                    for y0,y1 in extract_complete_rows(img,geom):
                        crop=img[y0:y1]
                        position_text,_=_ocr_data(_crop(crop,CROPS['position'],(.13,.88),2),
                                                  '--psm 7 -c tessedit_char_whitelist=0123456789')
                        digits=re.sub(r'\D','',position_text)
                        if not digits or int(digits) not in targets or (tab,int(digits)) in known:continue
                        obs=recognize_player_row(crop,layout,tab,
                              source=f'{Path(video_path).name}@{frame_id/left["fps"]:.2f}s',
                              templates=_badge_templates())
                        if obs['leaderboard_position']!=int(digits):continue
                        known.add((tab,int(digits)));recovered.append(obs)
                        audit.append({'event':'recovered_transition_row','tab':tab,'position':int(digits),
                                      'frame':int(frame_id),'source':Path(video_path).name,
                                      'full_boundaries':True})
                        targets.discard(int(digits))
                    if not any(v for v in possible.values()):break
        finally:cap.release()
    return recovered


def export_results(players,audit):
    # Remove internal-only helper keys from user records without losing audit.
    exported=[]
    for p in players:
        public={key:p.get(key) for key in ('avatar_base64','rank','name','power','kills','donations','tab',
                                           'source_image','review_required','review_reasons','source_type')}
        # Position is sent to staging/audit but is NOT a user-facing table field.
        public['internal_position']=p.get('leaderboard_position')
        public['audit']={key:p.get(key) for key in ('position_text','name_text','stat_text',
                 'name_confidence','stat_confidence','rank_confidence','rank_method',
                 'name_alternatives','stat_alternatives','observations','source_hash')}
        exported.append(public)
    return {'players':exported,'audit':audit}


def scan_leaderboard_upload(paths,progress:Callable|None=None,expected_count=None,settings=None):
    """Unified screenshot/video processing, returns player records and diagnostics.

    If no leaderboard layout can be found, fail safely rather than invent data.
    """
    settings={**DEFAULTS,**(settings or {})}
    audit=[];pages=[];source_frames=0
    image_paths=[]
    for path in paths:
        path=Path(path)
        if path.suffix.lower() in VIDEO_EXTENSIONS:
            extra=extract_video_pages(path,progress,settings,audit)
            pages.extend(extra)
            source_frames+=next((e['source_frames'] for e in reversed(audit) if e.get('event')=='dwell_detection' and e.get('source')==path.name),0)
        else:image_paths.append(path)
    pages+=ingest_screenshots(image_paths,audit)
    if progress:progress(43,100,'Calibrating complete leaderboard row boundaries…')
    observations,categories,complete_count=_extract_page_observations(pages,progress,audit,settings)
    if not categories:
        raise ValueError('No confidently selected leaderboard tab and complete rows. Unsupported or cropped leaderboard layout; database unchanged.')
    merged=merge_player_observations(observations)
    validity=validate_results(merged,expected_count)
    missing={(tab,pos) for tab,gaps in validity['missing_positions'].items() for pos in gaps}
    recovered=[]
    if missing:
        if progress:progress(83,100,'Checking missing positions against scroll transitions…')
        recovered=recover_missing_rows(paths,missing,pages,merged,progress,audit)
    if recovered:
        merged=merge_player_observations(observations+recovered)
        validity=validate_results(merged,expected_count)
    if progress:progress(97,100,'Validating identities and leaderboard coverage…')
    result=export_results(merged,audit)
    diagnostics={'source_frames':source_frames,'input_files':len(paths),'distinct_pages':len(pages),
                 'tabs':categories,'complete_rows':complete_count+len(recovered),
                 'distinct_entries':len(merged),'missing_positions':validity['missing_positions'],
                 'missing_count':validity['missing_count'],'recovered_rows':len(recovered),
                 'review_count':validity['review_count'],
                 'warnings':validity['warnings']}
    result['diagnostics']=diagnostics
    result['frames_examined']=len(pages)
    result['warnings']=validity['warnings']
    result['players']=[p for p in result['players'] if p.get('name') or p.get('internal_position') is not None]
    return result
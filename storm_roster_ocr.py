"""Server OCR for Storm Task Force screenshots and scrolling event rosters.

The Storm roster is intentionally an event snapshot, not the member directory.
It is replaced only after a scan yields verifiable Team A or B players.
"""
from __future__ import annotations
import re
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

VIDEO = {'.mp4','.mov','.m4v','.webm'}
POWER_RE = re.compile(r'\b(\d{1,5}(?:[.,]\d{1,2})?)\s*([MKBN])\b', re.I)


def _ocr(image, psm=7, whitelist=None):
    config=f'--psm {psm}'
    if whitelist: config+=f' -c tessedit_char_whitelist={whitelist}'
    try: return pytesseract.image_to_string(image,config=config,timeout=12).strip()
    except (RuntimeError,pytesseract.TesseractError): return ''


def _rank_bars(image):
    """Find lavender R5/R4/R3/R2/R1 separators by background color."""
    h,w=image.shape[:2]
    if h<650:return []
    rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB).astype(np.int16)
    # Broad flat lavender strip, not light player cards or the search box.
    xs=np.linspace(int(w*.30),int(w*.72),11,dtype=int)
    sample=rgb[:,xs,:]
    target=np.array([207,204,215])
    masks=(np.max(np.abs(sample-target),axis=2)<16).mean(axis=1)>.72
    # Fill small holes caused by the badges and counts cutting across the stripe.
    masks=cv2.morphologyEx(masks.astype(np.uint8).reshape(-1,1),cv2.MORPH_CLOSE,
                           np.ones((25,1),dtype=np.uint8)).reshape(-1).astype(bool)
    masks[:int(h*.32)]=False
    groups=[];start=None
    for i,good in enumerate(masks):
        if good and start is None:start=i
        elif not good and start is not None:
            if 42<i-start<160: groups.append((start,i))
            start=None
    if start is not None and 42<h-start<160:groups.append((start,h))
    # Rank header text/icons can interrupt the flat-color stripe; merge short gaps.
    joined=[]
    for a,b in groups:
        if joined and a-joined[-1][1]<28:
            joined[-1]=(joined[-1][0],b)
        else:joined.append((a,b))
    return joined


def _rank_from_bar(image, a, b):
    h,w=image.shape[:2]
    # Crop the badge interior; reading the whole lavender row confuses OCR.
    crops=[(int(w*.078),int(w*.155),a+33,min(b,a+96)),
           (int(w*.075),int(w*.170),a+18,min(b,a+98))]
    for x0,x1,y0,y1 in crops:
        part=image[max(0,y0):min(h,y1),x0:x1]
        if not part.size:continue
        large=cv2.resize(part,None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
        for form in (large,cv2.cvtColor(large,cv2.COLOR_BGR2GRAY)):
            text=_ocr(form,7,'Rr12345')
            match=re.search(r'[Rr]\s*([1-5])',text)
            if match:return 'R'+match.group(1)
    return None


def _team_from_image(image, words, width):
    search_words=[w for w in words if re.search(r'search|suchen|chercher|cerca|buscar|søg|memb',w['text'],re.I)]
    search_y=min((w['y'] for w in search_words),default=int(image.shape[0]*.49))
    # In all supported layouts the selector is above Search for Members. Match
    # the selector words, not the participant B badges inside player cards.
    for word in words:
        if word['y']>search_y-30:continue
        if re.search(r'force|truppe|squad|team|equipo|équipe',word['text'],re.I):
            near=sorted((p for p in words if abs(p['y']-word['y'])<22 and
                         word['x']<p['x']<width*.80),key=lambda p:p['x'])
            for p in near:
                letter=p['text'].strip(' .|()[]!')
                if letter.upper() in ('A','B'):return letter.upper()
    # Focus on one horizontal team bar below the selector title and above stats.
    anchor=search_y-int(image.shape[0]*.095)
    for delta in (0,-28,28):
        lo=max(0,anchor+delta-29);hi=min(image.shape[0],anchor+delta+28)
        region=image[lo:hi,int(width*.12):int(width*.76)]
        if not region.size:continue
        text=_ocr(cv2.resize(region,None,fx=2,fy=2),7)
        m=re.search(r'([AB])\s*$',text,re.I)
        if m:return m.group(1).upper()
    return None


def _flat_words(image):
    try:
        d=pytesseract.image_to_data(image,config='--psm 11',output_type=Output.DICT,timeout=22)
    except (RuntimeError,pytesseract.TesseractError):return []
    return [{'text':str(t).strip(),'x':d['left'][i],'y':d['top'][i],
             'width':d['width'][i],'height':d['height'][i]}
            for i,t in enumerate(d['text']) if str(t).strip()]


def _power_anchors(words,h,w):
    results=[]
    for word in words:
        if not re.search(r'power\s*:|power',word['text'],re.I):continue
        y=word['y']
        near=[p for p in words if abs(p['y']-y)<22 and p['x']>word['x'] and p['x']<w*.8]
        phrase=' '.join(p['text'] for p in sorted(near,key=lambda x:x['x']))
        match=POWER_RE.search(phrase.upper().replace('O','0'))
        if match:
            value=match.group(1).replace(',','.')+('M' if match.group(2).upper()=='N' else match.group(2).upper())
            results.append((y,value))
    # Fallback for non-English game labels: numeric power line beneath the name.
    if not results:
        for word in words:
            if word['x']<w*.3 or word['x']>w*.72:continue
            m=POWER_RE.fullmatch(word['text'].upper().replace('O','0'))
            if m:results.append((word['y'],m.group(1).replace(',','.')+m.group(2)))
    return sorted(set(results))


def _read_name(words,power_y,w):
    candidates=[word for word in words if power_y-105<=word['y']<=power_y-18 and
                w*.17<word['x']<w*.52 and len(word['text'])>1]
    if not candidates:return ''
    # Choose closest name line above the power value; don't treat icon text as a name.
    lines={}
    for word in candidates:
        key=round(word['y']/18)
        lines.setdefault(key,[]).append(word)
    for key in sorted(lines,reverse=True):
        ordered=sorted(lines[key],key=lambda x:x['x'])
        name=' '.join(w['text'] for w in ordered).strip(' .:!|`')
        if re.search(r'strategist|commander|power|total|hero|Lv\.|R[1-5]|^online$',name,re.I):continue
        if len(name)>=2 and re.search(r'[A-Za-z\d]',name):return name[:80]
    return ''


def _role_from_color(image,power_y,w):
    h=image.shape[0]
    # Blue B in the left role box means participant; the other box is the sub marker.
    # Some screenshots don't allow reliable detection; keep them marked for review.
    y0=max(0,power_y-40);y1=min(h,power_y+45)
    x0=int(w*.52);x1=int(w*.66)
    if y1<=y0 or x1<=x0:return 'Needs review'
    hsv=cv2.cvtColor(image[y0:y1,x0:x1],cv2.COLOR_BGR2HSV)
    blue=np.logical_and.reduce((hsv[:,:,0]>=90,hsv[:,:,0]<=132,hsv[:,:,1]>=60,hsv[:,:,2]>=70))
    return 'Participant' if np.mean(blue)>.02 else 'Sub'


def extract_storm_image(image):
    h,w=image.shape[:2]
    if w<500 or h<700:return []
    # Normalize to original 945px game screenshot width.
    if w!=945:
        image=cv2.resize(image,(945,round(h*945/w)),interpolation=cv2.INTER_AREA if w>945 else cv2.INTER_CUBIC)
    h,w=image.shape[:2]
    words=_flat_words(image)
    bars=[(a,b,_rank_from_bar(image,a,b)) for a,b in _rank_bars(image)]
    for word in words:
        text=word['text'].strip('\\|!()[]{};:.,')
        m=re.fullmatch(r'[Rr]([1-5])',text)
        if m and word['y']>h*.32:
            if not any(a-20<word['y']<b+20 for a,b,_ in bars):
                bars.append((word['y']-12,word['y']+42,'R'+m.group(1)))
    bars.sort()
    team=_team_from_image(image,words,w)
    results=[]
    for y,power in _power_anchors(words,h,w):
        bar=next(((a,b,rank) for a,b,rank in reversed(bars) if b<y),None)
        if not bar:continue
        name=_read_name(words,y,w)
        if not name:continue
        role=_role_from_color(image,y,w)
        results.append({'name':name,'team':team or '?','rank':bar[2] or 'R3','power':power,'role':role})
    return results


def scan_storm_upload(paths,progress):
    candidates={}; frames=0; warnings=[]
    for index,path in enumerate(paths):
        ext=Path(path).suffix.lower()
        if ext in VIDEO:
            # Video frames are extracted inside the parent temporary upload directory.
            folder=Path(tempfile.mkdtemp(prefix='storm_frames_',dir=str(Path(path).parent)))
            try:
                cmd=['ffmpeg','-y','-nostdin','-hide_banner','-loglevel','error','-i',str(path),
                     '-vf','fps=1.5,scale=945:-2','-frames:v','100','-q:v','4',str(folder/'frame_%03d.jpg')]
                subprocess.run(cmd,check=True,timeout=120)
                media=sorted(folder.glob('frame_*.jpg'))
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
                raise ValueError('Video decoding failed. Please upload MP4 or screenshots.')
        else:
            media=[Path(path)]
        for file in media:
            image=cv2.imread(str(file))
            frames+=1
            if image is not None:
                for row in extract_storm_image(image):
                    key=(row['team'],re.sub(r'\s+','',row['name'].casefold()))
                    if key[0]!='?' and key[1] and (key not in candidates or not candidates[key].get('power')):
                        candidates[key]=row
            progress(max(4,min(96,round(96*(index+(frames/max(1,len(media))))/max(len(paths),1)))),100,
                     f'Storm OCR: {frames} frame(s), {len(candidates)} players found')
    if not candidates:warnings.append('Team A/B or player values were not recognized; no existing data will be replaced.')
    return {'players':list(candidates.values()),'frames_examined':frames,'warnings':warnings}
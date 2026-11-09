"""Member List extraction from screenshots and scrolling screen recordings.

The video/screenshot is treated as a changing viewport. Only the rows below the
Search for Members bar are analyzed. Never inspect the decorative officer or
leader area at the top. Uses spatial rank headers, OCR and overlap deduplication.
"""
from __future__ import annotations
import base64
import math
import os
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pytesseract

NAME_BAD_WORDS = {"POWER", "MANAGE", "ONLINE", "LV", "MEMBERS", "LEGENDS", "INACTIVE", "SEARCH", "MEMBER", "LIST"}


def _safe_ocr(image, config='--psm 7'):
    """Bound each OCR subprocess so a bad frame cannot freeze the upload."""
    try:
        return pytesseract.image_to_string(image, config=config, timeout=12)
    except (RuntimeError, pytesseract.TesseractError):
        return ''


def _decode_video(command, seconds, progress):
    """Track actual FFmpeg decode progress rather than showing 0% indefinitely."""
    import threading
    command = [*command[:-1], '-progress', 'pipe:1', '-nostats', command[-1]]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    timeout = threading.Timer(170, process.kill)
    timeout.daemon = True
    timeout.start()
    messages = []
    try:
        register=getattr(progress,'register_process',None)
        if register:register(process)
        for line in process.stdout:
            line = line.strip()
            if line.startswith('out_time_ms='):
                try:
                    done = int(line.split('=', 1)[1]) / 1_000_000
                    portion = min(1.0, max(0.0, done / max(seconds, 1)))
                    progress(3 + round(19 * portion), 100,
                             f'Extracting video frames: {round(portion * 100)}%')
                except ValueError:
                    pass
            elif line and not line.startswith(('frame=', 'fps=', 'stream_', 'bitrate=',
                                               'total_size=', 'out_time=', 'out_time_us=',
                                               'progress=', 'dup_frames=', 'drop_frames=', 'speed=')):
                messages.append(line)
                messages = messages[-3:]
        code = process.wait(timeout=5)
        if code:
            raise ValueError('Video decoding failed. Please try MP4/H.264 or screenshots.')
    finally:
        unregister=getattr(progress,'unregister_process',None)
        if unregister:unregister(process)
        timeout.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()


def _groups(indices, max_gap=5, min_span=20):
    groups = []
    for value in indices:
        value = int(value)
        if not groups or value - groups[-1][-1] > max_gap:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [(v[0], v[-1]) for v in groups if v[-1] - v[0] >= min_span]


def _resize_frame(image):
    h, w = image.shape[:2]
    if w != 1080 and w > 0:
        size = (1080, max(1, int(round(h * (1080 / w)))))
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA if w > 1080 else cv2.INTER_CUBIC)
    return image


def _detect_rank_headers(image):
    """Find the in game header bars, including the green R4 and gray R3/R2/R1."""
    h, w = image.shape[:2]
    if w < 640 or h < 850:
        return []
    roi = image[:, 380:690:31, ::-1].astype(np.int16)  # RGB
    red, green, blue = [roi[:, :, k] for k in range(3)]
    header_pixels = ((blue - red > 5) | (green - red > 5)) & (red > 155) & (red < 243) & (green > 180)
    rows = np.mean(header_pixels, axis=1) > .68
    candidates = _groups(np.where(rows & (np.arange(h) > int(h * .36)) & (np.arange(h) < int(h * .94)))[0], min_span=25)
    results = []
    for y0, y1 in candidates:
        if y1 - y0 > 140:
            continue
        badge = image[max(0, y0 - 4): min(h, y1 + 6), 33:126]
        large = cv2.resize(badge, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        text = _safe_ocr(large, config='--psm 7 -c tessedit_char_whitelist=Rr12345')
        match = re.search(r'[Rr]\s*([1-5])', text)
        if not match:
            header_line = image[y0:y1, 115:430]
            text = _safe_ocr(cv2.resize(header_line, None, fx=2, fy=2), config='--psm 7')
            match = re.search(r'\bR\s*([1-5])\b', text, flags=re.I)
        if match:
            results.append((y0, y1, 'R' + match.group(1)))
    return results


def _detect_button_y(image):
    """Find card rows by their cyan manage buttons. Most member cards have one."""
    h, w = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    area = hsv[int(h*.365):int(h*.917), int(w*.73):int(w*.96)]
    mask = cv2.inRange(area, (85, 90, 100), (110, 255, 255))
    ys = np.where(np.sum(mask > 0, axis=1) > area.shape[1] * .22)[0] + int(h*.365)
    pieces = _groups(ys, max_gap=3, min_span=12)
    # Each button has two broad cyan stripes separated by ~20 px of white lettering.
    # Merge stripes whose first rows are less than 86 px apart.
    merged = []
    for start, end in pieces:
        if merged and start - merged[-1][1] < 75:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [start for start, end in merged if end - start >= 27]


def _card_candidate_y(image):
    """Find light card backgrounds, including the Online cards without a button."""
    h, w = image.shape[:2]
    roi = image[:, 375:691:29, ::-1].astype(np.int16)
    r,g,b = [roi[:,:,k] for k in range(3)]
    cards = (r-g>=7)&(r>210)&(g>185)&(g<244)&(b>180)&(b<243)&(np.abs(g-b)<27)
    rows = np.mean(cards,axis=1) > .65
    return _groups(np.where(rows & (np.arange(h)>int(h*.38)) & (np.arange(h)<int(h*.915)))[0],min_span=44)


def _ocr_line(image, box, config='--psm 7', scale=2):
    h,w=image.shape[:2]
    x0,y0,x1,y1=box
    x0=max(0,int(x0));y0=max(0,int(y0));x1=min(w,int(x1));y1=min(h,int(y1))
    if x0>=x1 or y0>=y1:return ''
    crop=image[y0:y1,x0:x1]
    if scale>1:crop=cv2.resize(crop,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    return _safe_ocr(crop,config=config).strip().replace('\n',' ')


def _normalize_name(name):
    # Avoid normalizing actual usernames into something different. Only trim OCR UI residue.
    name=re.sub(r'^[^\w\[\]\u00c0-\uffff]+','',name,flags=re.U)
    name=re.sub(r'\s+',' ',name).strip(' |~:.,;!(){}<>\\')
    name=re.sub(r'\b(?:Online|Manage)\b\s*$','',name,flags=re.I).strip()
    # Decorative streamer/gender icon artifacts occasionally trail names.
    name=re.sub(r'\s+[tT][S™]$','',name).strip()
    name=re.sub(r'\s*[—–-]+[tT™*\s]*$','',name).strip()
    name=re.sub(r'[—–™*]+$','',name).strip()
    if len(name)<2 or len(name)>48 or not any(c.isalpha() for c in name):return ''
    if name.upper() in NAME_BAD_WORDS or name.upper().startswith('POWER'):return ''
    if re.search(r'\b(?:Power|Level|Lv|Manage|Members)\b',name,flags=re.I):return ''
    return name


def _read_power(text):
    """Read a game power number, tolerating outlined-font OCR punctuation.

    Text is known to begin with the Power label and stop at the HQ label.
    Tesseract can read a decimal dot as apostrophe, colon, slash or a space.
    Uncertain readings are left blank for user review instead of fabricated.
    """
    m=re.search(r'p[o0]w[e3]?r+r?',text,re.I)
    if not m:return None
    tail=text[m.end():]
    tail=re.split(r'\bL[vVuU]\s*[:.]?',tail,1,flags=re.I)[0]
    tail=tail.split('M',1)[0].split('m',1)[0]
    digits=''.join(re.findall(r'[0-9]',tail))
    if len(digits)==4:
        val=float(digits[:-1]+'.'+digits[-1])
        return val if 1<=val<=5000 else None
    if len(digits)==3:
        # Two-digit powers exist, but require an unambiguous decimal dot or
        # comma. A missing leading glyph from 201.7 must not become 20.1.
        clear=re.search(r'(?<![0-9])[0-9]{2}[.,][0-9]\s*[Mm]',tail)
        return round(float(clear.group().rstrip('Mm ')),1) if clear else None
    if len(digits)==5:
        # Decimal powers above 1000M are possible, but 5 digits often mean
        # OCR invented a glyph. Leave it for human verification.
        return None
    return None


def _video_portrait(image, box, size=104):
    """Compress a portrait crop into small WebP bytes for durable MySQL storage.

    Only the video code calls this. Do not extract images from screenshots.
    """
    x0,y0,x1,y1=map(int,box)
    h,w=image.shape[:2]
    x0=max(0,x0);x1=min(w,x1);y0=max(0,y0);y1=min(h,y1)
    if x1-x0<50 or y1-y0<50:return None
    crop=image[y0:y1,x0:x1]
    # Enforce a consistent, compact square thumbnail without distortion.
    side=min(crop.shape[:2])
    ox=(crop.shape[1]-side)//2;oy=(crop.shape[0]-side)//2
    crop=crop[oy:oy+side,ox:ox+side]
    crop=cv2.resize(crop,(size,size),interpolation=cv2.INTER_AREA)
    ok,binary=cv2.imencode('.webp',crop,[cv2.IMWRITE_WEBP_QUALITY,73])
    if not ok or len(binary)>32000:return None
    return base64.b64encode(binary.tobytes()).decode('ascii')


def extract_video_leader(frame):
    """R5 portrait and display name from the member list header of a VIDEO.

    The same R5 generally appears later in a separate profile screenshot,
    which supplies rank/power/HQ and possibly an alliance tag. The image itself
    must always originate from video, never that profile screenshot.
    """
    frame=_resize_frame(frame)
    h,w=frame.shape[:2]
    if w!=1080 or h<1400:return None
    # R5 portrait sits above the Search for Members bar; ordinary rows below.
    name=_normalize_name(_profile_text(frame,(436,425,705,493),threshold=None))
    if not name or len(name)>32:return None
    # Verify the R5 badge alongside the header; not a generic player card.
    badge=_ocr_line(frame,(327,420,391,493),config='--psm 7 -c tessedit_char_whitelist=Rr12345',scale=3)
    if not re.search(r'R\s*5',badge,re.I):return None
    portrait=_video_portrait(frame,(466,255,624,423),size=116)
    if not portrait:return None
    return {'name':name,'rank':'R5','power_millions':None,
            'hq_level':None,'source_type':'video_leader','avatar_base64':portrait}


def _read_card(image,button_top,rank,capture_avatar=False):
    # Button's upper stripe is aligned roughly 15-35 pixels below player name,
    # and power/HQ is on the line 35-75px lower. All coordinates are at width=1080.
    y=int(button_top)
    name1=_ocr_line(image,(228,y-49,670,y+17),scale=2)
    name=_normalize_name(name1)
    if not name:
        name=_normalize_name(_ocr_line(image,(230,y-49,680,y+18),config='--psm 13',scale=2))
    if not name:return None
    line=_ocr_line(image,(222,y+23,685,y+83),scale=2)
    power=_read_power(line)
    if power is None:
        fallback=_ocr_line(image,(310,y+27,490,y+79),config='--psm 7 -c tessedit_char_whitelist=0123456789.,:MBK',scale=3)
        power=_read_power('Power:'+fallback)
    level=None
    hq_inline=re.search(r'\b[Ll][Vv][.\s:]*(\d{1,2})\b',line)
    if hq_inline and 10<=int(hq_inline.group(1))<=99:
        level=int(hq_inline.group(1))
    if level is None:
        hq=_ocr_line(image,(532,y+20,686,y+79),config='--psm 7 -c tessedit_char_whitelist=0123456789',scale=2)
        match=re.search(r'(?<!\d)([1-9]\d|1\d\d|[1-9])(?!\d)',hq)
        level=int(match.group(1)) if match and 10<=int(match.group(1))<=99 else None
    item={'name':name,'rank':rank,'power_millions':power,'hq_level':level}
    if capture_avatar:
        item['avatar_base64']=_video_portrait(image,(52,y-45,178,y+86))
    return item


def _fingerprint(image,y):
    h,w=image.shape[:2]
    crop=image[max(0,y-45):min(h,y+79),225:670]
    if crop.size==0:return None
    gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    small=cv2.resize(gray,(33,16),interpolation=cv2.INTER_AREA)
    return (small[:,1:]>small[:,:-1]).flatten()


def _seen_fingerprint(fingerprints,print_arr):
    if print_arr is None:return False
    for prev in fingerprints:
        if np.count_nonzero(prev!=print_arr)<=40:return True
    fingerprints.append(print_arr)
    return False



def _video_has_unseen_cards(image, known_fingerprints):
    """Cheap video pass: do not launch Tesseract when all visible cards were read."""
    if not known_fingerprints:
        return True
    image = _resize_frame(image)
    candidates = _detect_button_y(image)
    for y0,y1 in _card_candidate_y(image):
        if y1-y0 < 65:
            continue
        estimate = y0+54
        if not any(abs(existing-estimate)<83 for existing in candidates):
            candidates.append(estimate)
    if not candidates:
        return True  # Avoid skipping unusual layouts with no identifiable buttons.
    checked = 0
    for y in candidates:
        fp = _fingerprint(image,y)
        if fp is None:
            return True
        checked += 1
        if not any(np.count_nonzero(old != fp) <= 40 for old in known_fingerprints):
            return True
    return checked == 0

def _profile_text(image, box, threshold=210, psm=7):
    """Read high contrast white game lettering, ignoring the blue or gray panel."""
    height, width = image.shape[:2]
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0 = max(0,x0),max(0,y0)
    x1, y1 = min(width,x1),min(height,y1)
    if x1-x0<10 or y1-y0<10:return ''
    gray=cv2.cvtColor(image[y0:y1,x0:x1],cv2.COLOR_BGR2GRAY)
    # White fill with a black outline is very readable when isolated from the
    # brightly coloured game UI. This deliberately avoids translated labels.
    if threshold is not None:
        gray=cv2.threshold(gray,threshold,255,cv2.THRESH_BINARY)[1]
    enlarged=cv2.resize(gray,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
    return _safe_ocr(enlarged,config=f'--psm {psm}').strip().replace('\n',' ')


def extract_profile(frame):
    """Read one player's profile popup, independent of the scrolling roster.

    Popups show 'Lv.XX PLAYER NAME' in a blue strip, power in the first stat
    box, and an R1-R5 badge above the alliance name. Their position is
    anchored to the blue strip, rather than the viewport height. Never scan
    a member list card behind an open profile popup.
    """
    h0,w0=frame.shape[:2]
    if w0<300 or h0<550:return None
    width=709
    frame=cv2.resize(frame,(width,max(1,round(h0*width/w0))),
                     interpolation=cv2.INTER_AREA if w0>width else cv2.INTER_CUBIC)
    h,w=frame.shape[:2]
    hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
    blue=hsv[:,int(w*.40):int(w*.73):8]
    hits=((blue[:,:,0]>=95)&(blue[:,:,0]<=115)&
          (blue[:,:,1]>105)&(blue[:,:,2]>120)).mean(axis=1)
    yy=np.where((hits>.60)&(np.arange(h)>int(h*.075))&
                (np.arange(h)<int(h*.58)))[0]
    # A profile has a wide blue strip. Search its upper edge: dynamic vertical
    # anchoring permits screenshots with notifications or differently sized UI.
    starts=[first for first,last in _groups(yy,max_gap=3,min_span=13)]
    for y in starts:
        if y+300>=h:continue
        header=_profile_text(frame,(int(w*.135),y+8,int(w*.75),y+96),210)
        m=re.search(r'\b[LlI1]\s*[vVuU]\s*[.:]?\s*([1-9]\d?)\s+(.+)',header)
        if not m:continue
        level=int(m.group(1))
        name=_normalize_name(m.group(2))
        # The icon after the name represents player gender, not part of the
        # username. It is ordinarily excluded by the white-text threshold.
        name=re.sub(r'[♀♂⚥]','',name).strip()
        if name.count('[')!=name.count(']'):
            # UI glyph outlines can make a closing alliance-tag bracket look
            # like a j. Try a less aggressive luminance cutoff before
            # allowing an untrusted name to reach the database.
            for threshold in (195,200,205,225):
                revised=_profile_text(frame,(int(w*.135),y+8,int(w*.75),y+96),threshold)
                variant=re.search(r'\b[LlI1]\s*[vVuU]\s*[.:]?\s*([1-9]\d?)\s+(.+)',revised)
                if not variant or int(variant.group(1))!=level:continue
                candidate=_normalize_name(variant.group(2))
                if candidate.count('[')==candidate.count(']'):
                    name=candidate
                    break
        if not name or not 1<=level<=99:continue
        # Profile badges use dark orange lettering on a yellow shield. A tight
        # crop reads the R number without including the alliance name beside it.
        badge_box=(int(w*.46),y+240,int(w*.52),y+288)
        bx0,by0,bx1,by1=badge_box
        badge=frame[by0:by1,bx0:bx1]
        if badge.size==0:continue
        gray=cv2.cvtColor(badge,cv2.COLOR_BGR2GRAY)
        versions=(gray,cv2.threshold(gray,130,255,cv2.THRESH_BINARY)[1])
        rank=None
        for version in versions:
            up=cv2.resize(version,None,fx=4,fy=4,interpolation=cv2.INTER_CUBIC)
            match=re.search(r'R\s*([1-5])',_safe_ocr(up,
                config='--psm 7 -c tessedit_char_whitelist=Rr12345'),re.I)
            if match:
                rank='R'+match.group(1)
                break
        if not rank:continue
        stat=_profile_text(frame,(int(w*.53),y+110,int(w*.73),y+188),225)
        power_match=re.search(r'(?<!\d)(\d{1,4})[.,](\d)\s*[Mm]\b',stat)
        power=float(power_match.group(1)+'.'+power_match.group(2)) if power_match else None
        return {'name':name,'rank':rank,'power_millions':power,
                'hq_level':level,'source_type':'profile',
                'needs_review':name.count('[')!=name.count(']')}
    return None


def extract_frame(frame,known_fingerprints=None,capture_avatar=False):
    frame=_resize_frame(frame);h,w=frame.shape[:2]
    if h<1400 or w<640:return []
    header=_detect_rank_headers(frame)
    buttons=_detect_button_y(frame)
    # Add card candidates without a blue Manage button, e.g. online R4 players.
    cards=_card_candidate_y(frame)
    for cy0,cy1 in cards:
        estimate=cy0+54
        if cy1-cy0 < 65:continue
        if any(abs(existing-estimate)<83 for existing in buttons):continue
        # A candidate should not intersect rank header band; nor be above first header.
        if any(not(cy1<start or cy0>end+5) for start,end,_ in header):continue
        if header and cy0<header[0][1]:continue
        buttons.append(estimate)
    buttons.sort()
    for before,after in list(zip(buttons,buttons[1:])):
        diff=after-before
        if 296<diff<395:
            midpoint=round((before+after)/2)
            if all(not (start-90<midpoint<end+90) for start,end,_ in header):
                buttons.append(midpoint)
    buttons.sort()
    results=[]
    for button in buttons:
        # All rows must be below the search region, and the entire name and power
        # must be visible. Ignore partly hidden or clipped rows.
        if button< int(h*.405) or button+83>int(h*.918):continue
        if header and button < header[0][1]+25:continue
        if any(start-42 <= button <= end+43 for start,end,_ in header):continue
        visible_headers=[value for start,end,value in header if end<button]
        if not visible_headers:continue
        rank=visible_headers[-1]
        fp=_fingerprint(frame,button)
        if known_fingerprints is not None and _seen_fingerprint(known_fingerprints,fp):continue
        item=_read_card(frame,button,rank,capture_avatar=capture_avatar)
        if item: results.append(item)
    return results


def _key(name):
    return re.sub(r'\s+','',name.casefold())


def _rank_power_score(item):
    power=item.get('power_millions')
    return (6 if item.get('source_type')=='profile' else 0) + (3 if power is not None and 100 <= power < 600 else 1 if power is not None else 0) + (2 if item.get('hq_level') else 0) + (1 if item.get('rank') else 0) - (2 if any(c in item['name'] for c in '—™*') else 0)


def _edit_distance(a,b):
    if abs(len(a)-len(b))>2:return 99
    prev=list(range(len(b)+1))
    for i,c in enumerate(a,1):
        curr=[i]
        for j,d in enumerate(b,1):
            curr.append(min(prev[j]+1,curr[-1]+1,prev[j-1]+(c!=d)))
        prev=curr
    return prev[-1]


def reconcile_players(rows):
    """Combine OCR variants only when corroborating data makes it safe.

    Never merge solely because usernames are similar. Different players can
    have names that differ by one character.
    """
    rows=sorted(rows,key=_rank_power_score,reverse=True)
    output=[]
    for row in rows:
        name=re.sub(r'[^\w]','',row['name'].casefold())
        match_index=None
        for i,prev in enumerate(output):
            old=re.sub(r'[^\w]','',prev['name'].casefold())
            if row['rank']!=prev['rank']:continue
            if name==old:
                match_index=i;break
            # The video displays the R5 name without its [alliance] tag;
            # only merge this exact suffix when one side is the video leader.
            if row['rank']=='R5' and ('video_leader' in (row.get('source_type'), prev.get('source_type'))):
                short=lambda x:re.sub(r'^\[[^\]]{1,12}\]','',x).casefold()
                a,b=short(row['name']),short(prev['name'])
                # There is exactly one alliance leader (R5) in this header.
                # Allow one OCR glyph difference for this uniquely anchored row.
                if a==b or (min(len(a),len(b))>=4 and _edit_distance(a,b)<=1):
                    match_index=i;break
            # A truncated initial/final character can be merged only when
            # the available power and HQ readings agree.
            same_power=(row['power_millions'] is not None and prev['power_millions'] is not None and abs(row['power_millions']-prev['power_millions'])<.15)
            same_hq=(row['hq_level'] is not None and prev['hq_level'] is not None and row['hq_level']==prev['hq_level'])
            compatible=(row['power_millions'] is None or prev['power_millions'] is None or same_power) and (row['hq_level'] is None or prev['hq_level'] is None or same_hq)
            if len(name)>=5 and len(old)>=5 and compatible and _edit_distance(name,old)==1:
                # Distinct usernames such as GrenyaReb and GrenyaReh must
                # never be conflated just because the letters look alike.
                is_insertion=len(name)!=len(old)
                if is_insertion and (same_power or same_hq):match_index=i;break
        if match_index is None:
            output.append(dict(row));continue
        prev=output[match_index]
        if not prev.get('avatar_base64') and row.get('avatar_base64'):
            prev['avatar_base64']=row['avatar_base64']
        # A clear profile is more authoritative than an OCR reading from a scrolling list.
        if row.get('source_type')=='profile' and prev.get('source_type')!='profile':
            prev.update({k:v for k,v in row.items() if v is not None})
        # Fill missing fields without replacing confirmed numeric values.
        if prev.get('power_millions') is None and row.get('power_millions') is not None:prev['power_millions']=row['power_millions']
        if prev.get('hq_level') is None and row.get('hq_level') is not None:prev['hq_level']=row['hq_level']
        if len(name)<len(re.sub(r'[^\w]','',prev['name'].casefold())) and _rank_power_score(row)>=_rank_power_score(prev):prev['name']=row['name']
    return output


def scan_upload(paths,progress:Callable[[int,int,str],None]):
    """Return OCR candidates and diagnostics. Does not mutate the database."""
    all_frames=[]; filenames=[]; warnings=[]
    progress(1, 100, 'Checking uploaded files…')
    for path in paths:
        suffix=Path(path).suffix.lower()
        if suffix in ('.mp4','.mov','.m4v','.webm'):
            try:
                meta=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',str(path)],capture_output=True,text=True,timeout=12,check=True)
                seconds=max(1,float(meta.stdout.strip()))
                if seconds>420: raise ValueError('Video is too long. Limit recordings to seven minutes.')
                # Keep enough frames to cover scrolling while staying practical
                # on Render's free CPU. Static frames are skipped before OCR.
                fps=min(1.7,88 / seconds)
                count_limit=96
                folder=Path(tempfile.mkdtemp(prefix='frames_',dir=str(Path(path).parent)))
                command=['ffmpeg','-hide_banner','-nostdin','-loglevel','error','-i',str(path),'-vf',f'fps={fps:.3f},scale=1080:-2','-frames:v',str(count_limit),'-q:v','4',str(folder/'frame_%04d.jpg')]
                progress(3, 100, 'Extracting video frames…')
                _decode_video(command, seconds, progress)
                images=sorted(folder.glob('frame_*.jpg'))
                all_frames.extend(images);filenames.extend([Path(path).name]*len(images))
                if len(images)==count_limit:
                    warnings.append('The recording reached the frame safety limit. Check for players not captured near the end.')
            except (ValueError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
                raise ValueError('Could not decode the video: '+str(exc)[:150]) from exc
        else:
            all_frames.append(Path(path));filenames.append(Path(path).name)
    if not all_frames:raise ValueError('No usable screenshots or video frames were found.')
    observed={};fps_seen=[];total=len(all_frames);missing_stats=0
    previous_video_thumb=None
    progress(24, 100, f'Found {total} frames. Starting text recognition…')
    try:
        for index,frame_file in enumerate(all_frames,1):
            progress(24 + round(72*(index-1)/total), 100,
                     f'Reading frame {index} of {total}…')
            frame=cv2.imread(str(frame_file))
            if frame is None:
                warnings.append('Could not read an image frame.');continue
            if frame_file.parent.name.startswith('frames_'):
                h,w=frame.shape[:2]
                view=frame[int(h*.42):int(h*.91),int(w*.15):int(w*.87)]
                if view.size:
                    thumb=cv2.resize(cv2.cvtColor(view,cv2.COLOR_BGR2GRAY),(36,48))
                    if previous_video_thumb is not None and np.mean(
                            cv2.absdiff(previous_video_thumb,thumb))<1.8:
                        progress(24 + round(72*index/total), 100,
                                 f'Frame {index} of {total}: unchanged, skipped')
                        continue
                    previous_video_thumb=thumb
            from_video=frame_file.parent.name.startswith('frames_')
            if from_video and index>5 and fps_seen and not _video_has_unseen_cards(frame,fps_seen):
                progress(24 + round(72*index/total),100,
                         f'Frame {index} of {total}: already recognized, skipped')
                continue
            profile=None if from_video else extract_profile(frame)
            rows=[profile] if profile else extract_frame(frame,fps_seen if total>3 else None,
                                                      capture_avatar=from_video)
            if from_video and index<=5:
                leader=extract_video_leader(frame)
                if leader:rows.insert(0,leader)
            for row in rows:
                norm=_key(row['name'])
                if not norm:continue
                # Preserve name with most information; avoid replacing a valid
                # value with a low confidence reading of the same visual row.
                prev=observed.get(norm)
                if not prev or _rank_power_score(row)>_rank_power_score(prev):
                    if prev and row.get('power_millions') is None:row['power_millions']=prev.get('power_millions')
                    if prev and row.get('hq_level') is None:row['hq_level']=prev.get('hq_level')
                    if prev and not row.get('avatar_base64'):row['avatar_base64']=prev.get('avatar_base64')
                    observed[norm]=row
                elif prev:
                    if prev.get('power_millions') is None:prev['power_millions']=row.get('power_millions')
                    if prev.get('hq_level') is None:prev['hq_level']=row.get('hq_level')
                    if not prev.get('avatar_base64'):prev['avatar_base64']=row.get('avatar_base64')
            progress(24 + round(72*index/total),100,
                     f'Processed frame {index} of {total}; {len(observed)} names found')
    finally:
        for f in all_frames:
            if f.parent.name.startswith('frames_'):
                try:f.unlink(missing_ok=True)
                except OSError:pass
        folders={f.parent for f in all_frames if f.parent.name.startswith('frames_')}
        for folder in folders:
            try:folder.rmdir()
            except OSError:pass
    progress(97, 100, 'Combining detected players…')
    candidates=sorted(reconcile_players(list(observed.values())),key=lambda x:(-(int(x['rank'][1]) if x.get('rank') else 0),-(x.get('power_millions') or 0),x['name'].casefold()))
    for item in candidates:
        if item['power_millions'] is None or item['hq_level'] is None: missing_stats+=1
    # Record possible duplicate readings as a review warning, never delete or
    # automatically merge names with single-character substitutions.
    suspicious=[]
    for i,a in enumerate(candidates):
        if len(a['name'])<6:continue
        for b in candidates[i+1:]:
            if a['rank']!=b['rank'] or len(b['name'])<6:continue
            left=re.sub(r'[^\w]','',a['name'].casefold())
            right=re.sub(r'[^\w]','',b['name'].casefold())
            if _edit_distance(left,right)>1:continue
            if a['power_millions'] is not None and b['power_millions'] is not None and abs(a['power_millions']-b['power_millions'])>.15:continue
            suspicious.append(a['name']+' / '+b['name'])
            if len(suspicious)>=8:break
        if len(suspicious)>=8:break
    if suspicious:
        warnings.append('Check possible OCR name variants before syncing: '+', '.join(suspicious))
    if missing_stats:
        warnings.append(f'{missing_stats} players have a power or HQ value that OCR could not read. Correct these before syncing.')
    return {'players':candidates,'frames_examined':total,'warnings':warnings}

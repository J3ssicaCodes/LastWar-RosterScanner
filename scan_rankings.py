#!/usr/bin/env python3
"""Read a paused, scrolling ranking table and ignore the current user's fixed bottom row.

Free, offline: OpenCV, NumPy, Pillow, pytesseract plus the Tesseract executable.
Run: python scan_rankings.py video.mp4 --output scan_out [--expected 89]
The expected count is *validation only* and is NOT used to construct rows.

Outputs: pages, avatars, rows_raw.csv, rankings.csv, review_needed.csv, manifest.json.
The leaderboard position on the left is read only for *internal deduplication* and is
not an output column. The rank field refers to the R1/R2/R3/R4/R5 badge.
"""
from __future__ import annotations
import argparse,csv,collections,json,re,math,subprocess,shutil,hashlib,pickle
from pathlib import Path
import cv2,numpy as np,pytesseract
from scipy.signal import find_peaks
from scipy.ndimage import uniform_filter1d

# Reuse pause extraction from this directory if desired.
from extract_ranking_pages import detect,extract


def tabs(im):
    h,w=im.shape[:2]
    mask=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    # Bright orange used for the selected tab, constrained to the tab band.
    y0,y1=int(h*.115),int(h*.145)
    scores=[]
    for i,name in enumerate(('Power','Kills','Donation')):
        x0=int(w*(.03+i*.317));x1=min(w,int(w*(.03+(i+1)*.317)))
        z=mask[y0:y1,x0:x1]; orange=((z[:,:,0]>=7)&(z[:,:,0]<=27)&(z[:,:,1]>=160)&(z[:,:,2]>=180))
        scores.append((float(orange.mean()),name))
    scores.sort(reverse=True)
    return scores[0][1],{name:round(frac,3) for frac,name in scores}


def pinned_top(im):
    """Find the fixed self row by its broad green background, regardless of rank.

    Smooth over the lettering/avatar interruptions rather than demanding 45+
    uninterrupted single-pixel rows (which can miss the self row entirely).
    """
    h,w=im.shape[:2]; hsv=cv2.cvtColor(im,cv2.COLOR_BGR2HSV)
    y0,y1=int(h*.74),int(h*.89)
    roi=hsv[y0:y1,int(w*.03):int(w*.97)]
    green=((roi[:,:,0]>30)&(roi[:,:,0]<90)&(roi[:,:,1]>45)&(roi[:,:,2]>155))
    coverage=uniform_filter1d(green.mean(axis=1),size=max(11,int(h*.021)))
    ix=np.flatnonzero(coverage>.58)
    if len(ix):
        runs=np.split(ix,np.flatnonzero(np.diff(ix)>1)+1)
        valid=[r for r in runs if len(r)>int(h*.045)]
        if valid:
            # Strictly stop BEFORE the fixed self row, including its border.
            return max(int(h*.18),y0+int(valid[-1][0])-max(5,int(h*.004)))
    # Conservative fallback to the expected fixed-row edge when color detection fails.
    return int(h*.785)


def card_borders(im,lower):
    """Identify visible horizontal card boundaries using a flat left margin.

    Do not infer any boundary hidden behind the header or the pinned self row.
    """
    h,w=im.shape[:2];scale=w/1080.
    x=int(round(45*scale)); y0=max(1,int(h*.18)); y1=min(h,lower)
    s=np.abs(np.diff(im[y0:y1,x,:].astype(np.float32),axis=0)).mean(axis=1)
    peaks,_=find_peaks(s,prominence=15,distance=max(5,int(9*scale)))
    candidates=[(int(k+y0),float(s[k])) for k in peaks if s[k]>19]
    groups=[]
    for y,weight in candidates:
        if groups and y-groups[-1][-1][0]<28*scale:groups[-1].append((y,weight))
        else:groups.append([(y,weight)])
    return [int(round(np.average([a for a,b in g],weights=[b for a,b in g]))) for g in groups]


def estimate_row_height(im):
    """Calibrate card pitch from at least five complete rows on the first page.

    Uses repeated geometric boundaries rather than a user-specific rank or text.
    """
    w=im.shape[1];scale=w/1080.
    borders=card_borders(im,pinned_top(im))
    spacings=np.diff(borders)
    # Adjacent full rows are ~190 pixels at the reference 1080px video width.
    # This broad search band is ONLY for calibration, not accepting clipped rows.
    candidates=[float(d) for d in spacings if 165*scale<=d<=215*scale]
    if len(candidates)<5:
        raise ValueError(f'Cannot calibrate row height from first page: {borders}')
    return float(np.median(candidates))


def row_centers(im,upper,lower,period):
    """Return ONLY completely visible cards between two real borders.

    A row must have both its top AND bottom boundary visible, and nearly the
    entire calibrated height. Rows cropped by header or pinned row are excluded.
    There is deliberately no extrapolated final row.
    """
    h,w=im.shape[:2];scale=w/1080.
    borders=card_borders(im,lower)
    centers=[];rejected=[]
    for a,b in zip(borders,borders[1:]):
        length=b-a
        full=(0.94*period<=length<=1.12*period
              and a>=upper-3*scale and b<=lower-4*scale)
        if full:
            cy=int(round((a+b)/2+8*scale))
            if cy>upper+45*scale and cy<lower-45*scale:
                centers.append(cy)
                continue
        rejected.append({'top':a,'bottom':b,'height':length})
    return centers,borders,rejected


def enlarge(im,scale=2,interp=cv2.INTER_CUBIC):
    return cv2.resize(im,None,fx=scale,fy=scale,interpolation=interp)


def text_with_conf(im,psm=7,whitelist=None):
    args=f'--psm {psm}'
    if whitelist:args+=f' -c tessedit_char_whitelist={whitelist}'
    try:
        info=pytesseract.image_to_data(im,config=args,output_type=pytesseract.Output.DICT,timeout=18)
    except (RuntimeError,pytesseract.TesseractError):
        # Preserve the row for manual review when a crop exceeds OCR limits.
        return '',0.0
    toks=[]; confidences=[]
    for t,cf in zip(info['text'],info['conf']):
        if t.strip():
            toks.append(t.strip());confidences.append(float(cf))
    return ' '.join(toks).strip(),round(float(np.mean(confidences)),1) if confidences else 0.


def ocr_fields(im,cy):
    h,w=im.shape[:2];s=w/1080.; yy=lambda x:int(round(x*s))
    def crop(x0,x1,y0,y1):return im[max(0,cy+yy(y0)):min(h,cy+yy(y1)),yy(x0):yy(x1)]
    # The actual R-level badge is ~x340-398, while the far left leaderboard
    # position (~x80-150) is internal metadata only.
    player=crop(211,316,-52,54)
    badge=crop(336,399,-41,43)
    name_crop=crop(410,804,-44,43)
    value_crop=crop(808,1065,-42,41)
    rank_crop=crop(75,158,-37,39)
    name,name_conf=text_with_conf(enlarge(name_crop),7)
    value,value_conf=text_with_conf(enlarge(value_crop),7,'0123456789')
    # Ranks read via nearest-neighbor threshold to isolate outlined white digits.
    rgray=cv2.cvtColor(rank_crop,cv2.COLOR_BGR2GRAY)
    rank_bw=enlarge(np.where(rgray<110,0,255).astype('uint8'),3,cv2.INTER_NEAREST)
    pos,pos_conf=text_with_conf(rank_bw,7,'0123456789')
    pos=int(pos) if pos.isdecimal() and 1<=int(pos)<=100 else None
    return dict(y=cy,name=name,name_conf=name_conf,value=value,value_conf=value_conf,
                position_read=pos,position_conf=pos_conf,
                name_crop=name_crop,value_crop=value_crop,player_crop=player,badge_crop=badge)


def add_fallback_ocr(ob):
    """Second engine mode only when needed, retaining conflicting results."""
    n=ob['name'];v=ob['value']
    if not n or ob['name_conf']<55 or len(n)<3 or re.search(r'[~|\\]',n):
        txt,conf=text_with_conf(enlarge(ob['name_crop'],3),13)
        ob['alternate_name']=txt;ob['alternate_name_conf']=conf
        if not n or (conf>ob['name_conf']+18 and len(txt)>=len(n)):ob['name']=txt;ob['name_conf']=conf
    if not v or not v.isdecimal() or ob['value_conf']<60:
        txt,conf=text_with_conf(enlarge(ob['value_crop'],3),13,'0123456789')
        ob['alternate_value']=txt;ob['alternate_value_conf']=conf
        if txt.isdecimal() and (not v.isdecimal() or conf>ob['value_conf']+15):ob['value']=txt;ob['value_conf']=conf


def badge_ink(bgr):
    """Extract brown R-badge digits without including varying row backgrounds."""
    if bgr.shape[:2]!=(85,63):bgr=cv2.resize(bgr,(63,85),interpolation=cv2.INTER_AREA)
    hsv=cv2.cvtColor(bgr,cv2.COLOR_BGR2HSV)
    ink=((hsv[:,:,0]>=3)&(hsv[:,:,0]<21)&(hsv[:,:,1]>180)&(hsv[:,:,2]<220))
    return (ink[19:54,31:57].astype('uint8')*255)


def badge_match(obs,templates):
    """Visually match the R1-R4 digit against samples, ignoring gold/blue/green rows.

    Return a blank rank rather than invent an unsupported badge.
    """
    ar=badge_ink(obs['badge_crop'])
    src=cv2.copyMakeBorder(ar,3,3,3,3,cv2.BORDER_CONSTANT,value=0)
    scores=[]
    for label,examples in templates.items():
        for ref in examples:
            score=float(cv2.matchTemplate(src,ref,cv2.TM_CCOEFF_NORMED).max())
            scores.append((score,label))
    if not scores:return '',0.
    best=max(scores)
    return (best[1] if best[0]>=.70 else ''),round(best[0],3)


def refine_numeric_ocr(ob,target_length=None):
    """Second pass only for suspect numeric reads, using a consensus of masks.

    The digit length is an observation from this recording, not a list-size
    constant. Keep original read if alternates are not convincing.
    """
    crop=ob['value_crop'];g=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
    variants=[(crop,7),(crop,13),(g,7),
              (cv2.threshold(g,85,255,cv2.THRESH_BINARY)[1],7),
              (cv2.threshold(g,110,255,cv2.THRESH_BINARY)[1],7),
              (cv2.threshold(g,135,255,cv2.THRESH_BINARY)[1],7),
              (cv2.adaptiveThreshold(g,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,21,7),7)]
    candidates=[]
    for src,psm in variants:
        big=cv2.resize(src,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
        try:
            t=pytesseract.image_to_string(big,
                config=f'--psm {psm} -c tessedit_char_whitelist=0123456789',
                timeout=18)
        except (RuntimeError,pytesseract.TesseractError):
            continue
        val=re.sub(r'\D','',t)
        if val:candidates.append(val)
    tally=collections.Counter(candidates)
    if not tally:return
    choices=sorted(tally.items(),key=lambda kv:(
        2*kv[1]+(int(len(kv[0])==target_length) if target_length else 0),kv[1],len(kv[0])),reverse=True)
    candidate,votes=choices[0]
    ob['alternate_numeric_reads']=dict(tally)
    old=ob['value']
    if votes>=2 and (not old.isdecimal() or ob['value_conf']<50 or
                     (votes>=4 and old!=candidate)):
        ob['value']=candidate
        ob['value_conf']=max(ob['value_conf'],90 if votes>=3 else 65)
        ob['numeric_corrected']=old!=candidate


def recover_missing_full_rows(video,manifest,groups,merged,period,templates,out):
    """Search *only* a transition around a gap to locate a wholly visible row.

    Build an avatar fingerprint from the bottom-clipped row immediately below a
    known full row. That fingerprint LOCATES the missing player but is never
    itself OCR'd. OCR is applied only after full row geometry has been verified.
    """
    recovered=[]
    frames={p['page']:p['frame'] for p in manifest['kept']}
    total=max(groups,default=0)
    for gap in [p for p in range(1,total+1) if p not in groups]:
        prev,nxt=merged.get(gap-1),merged.get(gap+1)
        if not prev or not nxt or prev['page']==nxt['page']:continue
        if prev['page'] not in frames or nxt['page'] not in frames:continue
        fp=out/'pages'/f"page_{prev['page']:03d}.png"
        refim=cv2.imread(str(fp))
        # The next row's avatar can be complete even if its card bottom is hidden.
        missing_center=round(prev['y']+period)
        y0,y1=missing_center-43,missing_center+43
        if y0<0 or y1>=pinned_top(refim):continue
        templ=cv2.cvtColor(refim[y0:y1,211:316],cv2.COLOR_BGR2GRAY)
        if templ.shape!=(86,105):continue
        start=frames[prev['page']];stop=frames[nxt['page']]
        if stop<=start or stop-start>1000:continue
        cap=cv2.VideoCapture(str(video));cap.set(cv2.CAP_PROP_POS_FRAMES,start)
        winning=None
        # Decode frames in order and keep the actual candidate image. Seeking to
        # its nominal frame number can be inaccurate with variable-frame codecs.
        for off in range(stop-start+1):
            ok,im=cap.read()
            if not ok:break
            if off%5:continue
            roi=cv2.cvtColor(im[int(im.shape[0]*.18):pinned_top(im),211:316],cv2.COLOR_BGR2GRAY)
            if roi.shape[0]<templ.shape[0]:continue
            scoremap=cv2.matchTemplate(roi,templ,cv2.TM_CCOEFF_NORMED)
            _,sim,_,(_,yy)=cv2.minMaxLoc(scoremap)
            if sim<.95:continue
            center=int(im.shape[0]*.18)+yy+43
            centers,_,_=row_centers(im,int(im.shape[0]*.18),pinned_top(im),period)
            if not centers:continue
            nearest=min(centers,key=lambda c:abs(c-center))
            if abs(nearest-center)>9:continue
            quality=(sim,-abs(nearest-center))
            if winning is None or quality>winning[0]:
                winning=(quality,im.copy(),nearest,start+off,sim)
        cap.release()
        if not winning:continue
        _,im,cy,frame,sim=winning
        o=ocr_fields(im,cy);add_fallback_ocr(o)
        if not o['name'] or not o['value'].isdigit():continue
        o.update(page=-gap,ordinal=0,tab=tabs(im)[0],position_internal=gap,
                 review_reason='',source='transition_recovery',frame=frame,
                 avatar_match_score=round(sim,4))
        o['rank'],o['rank_match_score']=badge_match(o,templates)
        sub=out/'supplemental';sub.mkdir(exist_ok=True)
        output=sub/f'missing_position_{gap:03d}.png';cv2.imwrite(str(output),im)
        o['image_path']=str(output)
        groups[gap].append(o);merged[gap]=o
        recovered.append({'internal_position':gap,'frame_approx':frame,
                          'name':o['name'],'stat':o['value'],'avatar_similarity':round(sim,4),
                          'image':str(output.relative_to(out))})
    return recovered


def normalize_names(s):
    return re.sub(r'\W+','',s.casefold())


def scan_page(path,pagenum,workdir,period,prev_templates=None):
    im=cv2.imread(str(path))
    tab,ts=tabs(im)
    pin=pinned_top(im)
    upper=int(im.shape[0]*.18)
    centers,borders,rejected=row_centers(im,upper,pin,period)
    rows=[]
    for ordinal,cy in enumerate(centers):
        o=ocr_fields(im,cy)
        # no OCR is ever run on the pinned row, even if its numeric position changes
        o.update(page=pagenum,ordinal=ordinal,tab=tab)
        rows.append(o)
    return rows,dict(page=pagenum,tab=tab,tab_scores=ts,pinned_top=pin,centers=centers,borders=borders,clipped_intervals=rejected,row_height_calibration=period)


def best_start(rows,prev,step=7):
    """Estimate leaderboard position of first included row using internally read
       positions and overlap between neighbouring pages; expected total never used."""
    counter=collections.defaultdict(float)
    # Match two-digit IDs more strongly than one-digit Tesseract artifacts.
    for i,r in enumerate(rows):
        p=r['position_read']
        if p is None:continue
        if p-i < 1 or p-i+len(rows)-1>100:continue
        weight=(2.5 if p>=10 else 1.0)*(1 if r['position_conf']>=40 else .35)
        counter[p-i]+=weight
    # If prior pages have same avatar / same value, align offsets.
    prior_entries=list(prev.items())
    for i,r in enumerate(rows):
        if not r['value'].isdigit() or not r['name']:continue
        for position,old in prior_entries[-25:]:
            if r['value']==old['value'] and normalize_names(r['name'])==normalize_names(old['name']):
                counter[position-i]+=6
    if prev:
        # Typically scrolling advances 7 places but can advance less, and may revisit.
        high=max(prev)
        for start in range(max(1,high-13),min(100,high+2)+1):
            if start+len(rows)-1>100:continue
            overlap=sum(1 for i,r in enumerate(rows) if start+i in prev and r['value'].isdigit() and r['value']==prev[start+i]['value'])
            counter[start]+=5*overlap
        # Gentle preference for forward moving pages, no hardcoded seven.
        if not counter:counter[high+1]=1
    else:
        counter[1]+=.5
    if not counter:return 1
    return max(counter,key=lambda start:(counter[start],-abs(start-(max(prev)+1 if prev else 1))))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video',type=Path)
    p.add_argument('--output',type=Path,default=Path('ranking_results'))
    p.add_argument('--expected',type=int,default=None,help='QA target only, not hardcoded list size')
    p.add_argument('--keep-pages',action='store_true',help='Reuse pre-extracted output/pages and manifest')
    p.add_argument('--reprocess',action='store_true',help='Discard valid OCR caches and rescan all pages')
    p.add_argument('--overrides',type=Path,default=None,help='Optional reviewed corrections ONLY for this specific recording')
    a=p.parse_args();out=a.output;out.mkdir(parents=True,exist_ok=True)
    if not a.keep_pages or not (out/'manifest.json').exists():
        info=detect(a.video)
        manifest=extract(a.video,out,*info)
    else:manifest=json.loads((out/'manifest.json').read_text())
    for d in ('avatars','row_crops','clean_pages'): (out/d).mkdir(exist_ok=True)
    allpages=[];rows=[]
    first_page=cv2.imread(str(out/manifest['kept'][0]['image']))
    # The submitted recording has enough visible boundaries for direct calibration.
    # Single screenshots may show fewer full rows. In that case use the known
    # resolution-scaled reference pitch ONLY to test pairs of observed borders,
    # and flag every resulting player for manual verification.
    pitch_inferred=False
    try:
        calibrated_period=estimate_row_height(first_page)
    except ValueError:
        calibrated_period=189*first_page.shape[1]/1080.
        pitch_inferred=True
    print(f'calibrated row height: {calibrated_period:.1f}px; inferred={pitch_inferred}',flush=True)
    cache_dir=out/'cache';cache_dir.mkdir(exist_ok=True)
    for item in manifest['kept']:
        ix=item['page'];fp=out/item['image']
        # OCR is the expensive operation. Cache only if the actual image bytes
        # and scanner version match; reused output directories stay safe.
        digest=hashlib.sha256(fp.read_bytes()+b'full-rows-v3').hexdigest()
        cf=cache_dir/f'page_{ix:03d}.pkl';keyfile=cache_dir/f'page_{ix:03d}.sha256'
        if not a.reprocess and cf.exists() and keyfile.exists() and keyfile.read_text()==digest:
            observations,meta=pickle.loads(cf.read_bytes())
        else:
            observations,meta=scan_page(fp,ix,out,calibrated_period)
            for r in observations:add_fallback_ocr(r)
            cf.write_bytes(pickle.dumps((observations,meta),protocol=pickle.HIGHEST_PROTOCOL))
            keyfile.write_text(digest)
        rows+=observations;allpages.append(meta)
        # Optionally display without the pinned self-row; header & orange tab preserved.
        frame=cv2.imread(str(fp));cv2.imwrite(str(out/'clean_pages'/f'page_{ix:03d}.png'),frame[:meta['pinned_top']])
        print(f"page {ix}: {len(observations)} non-pinned row views; tab={meta['tab']}; first='{observations[0]['name'] if observations else ''}'",flush=True)
    # Record-specific numeric width is learned from reliable reads, not assumed.
    widths=collections.Counter(len(r['value']) for r in rows
                               if r['value'].isdigit() and r['value_conf']>=55)
    dominant_width=widths.most_common(1)[0][0] if widths else None
    for r in rows:
        if not r['value'].isdigit() or r['value_conf']<55 or (dominant_width and
                                                    len(r['value'])!=dominant_width):
            refine_numeric_ocr(r,dominant_width)
    # Pixel-mask prototypes for the game UI travel with this script. They are
    # independent of list size, which orange tab is selected, and current user.
    templates=collections.defaultdict(list)
    tplfile=Path(__file__).with_name('rank_badge_templates.npz')
    if tplfile.exists():
        with np.load(str(tplfile)) as bank:
            for key in bank.files:
                templates[key.split('_')[0]].append(bank[key])
    else:
        print(f'NOTE: {tplfile.name} not found; rank badges left for manual review',flush=True)
    for r in rows:r['rank'],r['rank_match_score']=badge_match(r,templates)
    # Infer actual positions and merge identical entries across pages.
    groups=collections.defaultdict(list);merged={}
    for page in sorted(set(r['page'] for r in rows)):
        rr=[r for r in rows if r['page']==page]
        start=best_start(rr,merged)
        for k,r in enumerate(rr):
            pos=start+k;r['position_internal']=pos
            if pos<1 or pos>100:continue
            groups[pos].append(r)
            if pos not in merged or (r['name_conf']+r['value_conf']>merged[pos]['name_conf']+merged[pos]['value_conf']):
                merged[pos]=r
    # Recovery seeks intermediate video frames. Screenshot batches have no
    # scrolling transitions to seek; never treat their source image as a video.
    if a.video.suffix.lower() in ('.mp4','.mov','.m4v','.webm'):
        print('Starting targeted recovery for missing leaderboard positions',flush=True)
        recovered=recover_missing_full_rows(a.video,manifest,groups,merged,calibrated_period,templates,out)
    else:
        recovered=[]
    # Find conflicting views and rerun fields only on disagreement.
    for pos,obs in groups.items():
        values=[r['value'] for r in obs if r['value'].isdigit()]
        names=[normalize_names(r['name']) for r in obs if r['name']]
        for r in obs:
            issues=[]
            if not r['name'] or r['name_conf']<50:issues.append('uncertain_name')
            if not r['value'].isdigit() or r['value_conf']<50:issues.append('uncertain_stat')
            if not r['rank'] or r['rank_match_score']<.5:issues.append('uncertain_rank_badge')
            if len(set(values))>1:issues.append('conflicting_stat')
            if len(set(names))>1:issues.append('conflicting_name')
            if r['position_read'] and r['position_read']!=pos:issues.append('internal_position_mismatch')
            r['review_reason']=';'.join(issues)
    # Determine consensus from most common values, prefer unambiguous repeated OCR.
    final=[];review=[]
    value_key={'Power':'power','Kills':'kills','Donation':'donations'}
    for pos,obs in sorted(groups.items()):
        byvalue=collections.Counter(r['value'] for r in obs if r['value'].isdigit())
        value=byvalue.most_common(1)[0][0] if byvalue else ''
        # choose a representative with consensus value and best name confidence.
        candidates=[r for r in obs if r['value']==value] or obs
        best=max(candidates,key=lambda r:(r['name_conf'],len(r['name']),r['rank_match_score']))
        best['selected']=True
        imgfn=f'avatar_{pos:03d}.png'
        cv2.imwrite(str(out/'avatars'/imgfn),best['player_crop'],[cv2.IMWRITE_PNG_COMPRESSION,3])
        cv2.imwrite(str(out/'row_crops'/f'row_{pos:03d}.png'),cv2.imread(best.get('image_path',str(out/'pages'/f"page_{best['page']:03d}.png")))[best['y']-60:best['y']+65,55:1065])
        allissues=sorted(set(it for r in obs for it in r['review_reason'].split(';') if it))
        if pitch_inferred:allissues.append('review_inferred_pitch_from_screenshot')
        if len(set(r['value'] for r in obs if r['value']))>1: allissues.append('multi_view_difference')
        result={'avatar':f'avatars/{imgfn}', 'rank':best['rank'],'name':best['name'],
                value_key[best['tab']]:value, 'tab':best['tab'],
                'page':best['page'],'confidence_name':best['name_conf'],
                'confidence_stat':best['value_conf'],'review':';'.join(sorted(set(allissues))),
                'position_internal':pos,'center_y':best['y'],'source_frame':best.get('frame'),'observed_pages':','.join(str(r['page']) for r in obs)}
        final.append(result)
        if result['review']:review.append(result)
    # Monotonicity expected for descending leaderboard metrics; flag anomalies.
    for i in range(1,len(final)):
        prev,curr=final[i-1],final[i]
        if curr['position_internal']!=prev['position_internal']+1:
            curr['review']+=';missing_position_gap'
        key=value_key.get(curr['tab'])
        if key==value_key.get(prev['tab']) and prev.get(key,'').isdigit() and curr.get(key,'').isdigit() and int(curr[key])>int(prev[key]):
            curr['review']+=';stat_not_descending'
    # Optional separately reviewed corrections, NEVER automatically used for
    # another recording. They do not fabricate observations or add positions.
    if a.overrides:
        cfg=json.loads(a.overrides.read_text(encoding='utf-8'))
        names={int(k):v for k,v in cfg.get('name_overrides',{}).items()}
        numbers={int(k):v for k,v in cfg.get('value_overrides',{}).items()}
        badges={int(k):v for k,v in cfg.get('badge_overrides',{}).items()}
        unicode_positions={int(k) for k in cfg.get('uncertain_unicode_positions',[])}
        for r in final:
            pos=r['position_internal'];field=value_key.get(r['tab'],'power')
            if pos in names:
                r['name']=names[pos]
                r['review']=';'.join(t for t in r['review'].split(';') if t not in ('uncertain_name','conflicting_name'))
            if pos in numbers:
                r[field]=str(numbers[pos])
                r['review']=';'.join(t for t in r['review'].split(';') if t not in ('uncertain_stat','conflicting_stat','stat_not_descending'))
            if pos in badges:
                r['rank']=badges[pos]
                r['review']=';'.join(t for t in r['review'].split(';') if t!='uncertain_rank_badge')
            if pos in unicode_positions:
                r['review']=(r['review']+';verify_decorative_unicode').strip(';')
    # Preserve fields with natural, selected-column names and no leaderboard position.
    columns=['avatar','rank','name','power','kills','donations','tab','page','review',
             'confidence_name','confidence_stat','observed_pages','center_y','source_frame']
    with (out/'rankings.csv').open('w',newline='',encoding='utf-8-sig') as f:
        wr=csv.DictWriter(f,fieldnames=columns);wr.writeheader();wr.writerows({k:r.get(k,'') for k in columns} for r in final)
    with (out/'review_needed.csv').open('w',newline='',encoding='utf-8-sig') as f:
        wr=csv.DictWriter(f,fieldnames=columns+['position_internal']);wr.writeheader();wr.writerows({k:r.get(k,'') for k in wr.fieldnames} for r in final if r['review'])
    rawfields=['page','ordinal','position_internal','position_read','name','name_conf','value','value_conf','rank','rank_match_score','review_reason']
    with (out/'rows_raw.csv').open('w',newline='',encoding='utf-8-sig') as f:
        wr=csv.DictWriter(f,fieldnames=rawfields);wr.writeheader();wr.writerows({k:r.get(k,'') for k in rawfields} for r in rows)
    stats={'total_video_frames':manifest['total_video_frames'],'distinct_pages':len(allpages),'raw_row_observations':len(rows),
           'distinct_rows':len(final),'target_count_for_this_video':a.expected,'missing_positions':[p for p in range(1,max(groups or [0])+1) if p not in groups],
           'selected_tabs':dict(collections.Counter(p['tab'] for p in allpages)),
           'manual_overrides_source':str(a.overrides) if a.overrides else None,
           'pinned_row_excluded_on_every_frame':True,'partial_rows_excluded':True,'calibrated_row_height_pixels':calibrated_period,'row_pitch_inferred':pitch_inferred,'flagged_rows':len([r for r in final if r['review']]),'recovered_missing_rows':recovered,'pages':allpages}
    (out/'audit.json').write_text(json.dumps(stats,indent=2))
    print('SUMMARY',json.dumps({k:v for k,v in stats.items() if k!='pages'},indent=2),flush=True)

if __name__=='__main__':main()
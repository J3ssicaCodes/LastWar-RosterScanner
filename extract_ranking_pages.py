#!/usr/bin/env python3
"""Extract unique dwell frames from a scrolling game ranking table.

Requirements: pip install opencv-python numpy pytesseract; install tesseract-ocr.
Usage: python extract_ranking_pages.py video.mp4 --outdir ranking_scan
Detects header once to exclude notifications and anything above it from motion checks.
Does not assume a fixed player count (supports up to the game's 100).
"""
import argparse,csv,json,math
from pathlib import Path
import cv2,numpy as np,pytesseract

def detect_table_header(first):
    h,w=first.shape[:2]
    crop=first[int(h*.105):int(h*.26),:]
    d=pytesseract.image_to_data(crop, config='--psm 11',output_type=pytesseract.Output.DICT)
    hits=[]
    for t,y,hh in zip(d['text'],d['top'],d['height']):
        if t.strip().lower().strip(':,.') in ('ranking','commander','power','donation','kills'):
            hits.append((t.lower(),int(h*.105)+y,int(hh)))
    # Table column labels are just below the tab selector, not the tab names.
    row_headers=[y+hh for t,y,hh in hits if t in ('ranking','commander')]
    end=max(row_headers) + int(h*.018) if row_headers else int(h*.185)
    return int(np.clip(end,int(h*.175),int(h*.225))),hits

def sign(im,top,bottom):
    region=cv2.cvtColor(im[top:bottom,65:1020],cv2.COLOR_BGR2GRAY)
    return cv2.resize(region,(112,184),interpolation=cv2.INTER_AREA)

def detect(path, min_pause=.38,threshold=2.5,stride=3,min_frames=15,progress=None):
    cap=cv2.VideoCapture(str(path));fps=cap.get(cv2.CAP_PROP_FPS);n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    ok,first=cap.read(); assert ok
    top,hits=detect_table_header(first)
    bottom=int(first.shape[0]*.79)
    min_samples=max(math.ceil(min_pause*fps/stride),math.ceil(min_frames/stride))
    print('video',n,'frames at',round(fps,3),'fps; content ROI',top,bottom,'min samples',min_samples,'header:',hits,flush=True)
    signatures=[(0,sign(first,top,bottom))]
    i=1
    while True:
        ok,frame=cap.read()
        if not ok:break
        if i%stride==0: signatures.append((i,sign(frame,top,bottom)))
        # Server jobs report frame analysis progress during long recordings,
        # instead of appearing stalled at the preparation stage.
        if progress and n>0 and i%max(90,n//25)==0:
            progress(2+round(23*i/n),100,f'Finding stable video pages: {i}/{n} frames')
        i+=1
    cap.release()
    runs=[];a=0;anchor=signatures[0][1]
    for j in range(1,len(signatures)):
        dist=float(cv2.absdiff(signatures[j][1],signatures[j-1][1]).mean())
        if dist>threshold:
            if j-a>=min_samples:runs.append((signatures[a][0],signatures[j-1][0]))
            a=j;anchor=signatures[j][1]
    if len(signatures)-a>=min_samples:runs.append((signatures[a][0],signatures[-1][0]))
    print('pause candidates',len(runs),[(round(a/fps,2),round(b/fps,2)) for a,b in runs],flush=True)
    return fps,n,top,bottom,runs

def extract(path,outdir,fps,n,top,bottom,runs,duplicate_threshold=2.2):
    out=Path(outdir);(out/'pages').mkdir(parents=True,exist_ok=True)
    cap=cv2.VideoCapture(str(path));seen=[];kept=[];duplicates=[]
    for k,(a,b) in enumerate(runs,1):
        mid=(a+b)//2;cap.set(cv2.CAP_PROP_POS_FRAMES,mid);ok,im=cap.read()
        if not ok:continue
        s=sign(im,top,bottom)
        near=min([(float(cv2.absdiff(s,t).mean()),idx) for idx,t in enumerate(seen)],default=(999,None))
        if near[0]<duplicate_threshold:
            duplicates.append(dict(run=k,mid_frame=mid,duplicate_page=kept[near[1]]['page'],delta=round(near[0],2)))
            continue
        page=len(kept)+1;name=f'page_{page:03}.png';cv2.imwrite(str(out/'pages'/name),im)
        kept.append(dict(page=page,frame=mid,seconds=round(mid/fps,2),duration=round((b-a+3)/fps,2),image='pages/'+name,original_run=k,closest_delta=round(near[0],2)))
        seen.append(s)
    cap.release()
    manifest=dict(video=str(path),fps=fps,total_video_frames=n,table_header_end_y=top,dwell_roi_bottom_y=bottom,threshold=2.5,min_pause=.38,kept=kept,duplicates=duplicates)
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2))
    if not kept:
        raise ValueError('No distinct stationary leaderboard pages detected in the video.')
    with (out/'manifest.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=kept[0].keys());w.writeheader();w.writerows(kept)
    print('saved',len(kept),'distinct pages; removed',len(duplicates),'duplicate dwell pages',flush=True)
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('video',type=Path);p.add_argument('--outdir',type=Path,default=Path('ranking_scan'));p.add_argument('--min-pause',type=float,default=.38);p.add_argument('--threshold',type=float,default=2.5);p.add_argument('--stride',type=int,default=3)
    a=p.parse_args();info=detect(a.video,min_pause=a.min_pause,threshold=a.threshold,stride=a.stride)
    extract(a.video,a.outdir,*info)
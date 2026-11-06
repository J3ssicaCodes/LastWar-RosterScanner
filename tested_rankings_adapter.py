"""Connect the supplied tested ranking scanner to Alliance Tools Media Upload.

The submitted scan_rankings.py and extract_ranking_pages.py own the OCR, page
alignment, row geometry, badge matching and transition recovery. This adapter
only prepares temporary page manifests and converts their CSV into API records.

Never use the sample-specific audit_overrides.json for other players.
"""
from __future__ import annotations

import base64
import csv
import json
import re
import shutil
import subprocess
import sys
import time
import select
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from extract_ranking_pages import detect, extract
from scan_rankings import tabs

from player_identity import corrected_name, name_key, NUMBER_REVIEW_PREFIX
from scan_rankings import add_fallback_ocr, ocr_fields, pinned_top, row_centers
from vs_points_ocr import recheck_neighbors


def recheck_ranking_group(records, anchors, sources, tab, period, resolve, progress):
    def read_frame(image, targets):
        if tabs(image)[0] != tab:
            return []
        upper = int(image.shape[0]*.18)
        centers, _, _ = row_centers(image,upper,pinned_top(image),period)
        readings = []
        scale = image.shape[1]/1080
        for center in centers:
            candidates = [row for row in targets if abs(row['center']-center) < period*.30]
            if len(candidates) != 1:
                continue
            row = candidates[0]
            crop = image[round(center-52*scale):round(center+54*scale),
                         round(211*scale):round(316*scale)]
            if not crop.size:
                continue
            signature = cv2.resize(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(24,24))
            if cv2.absdiff(signature,row['signature']).mean() >= 14:
                continue
            reading = ocr_fields(image,center,read_name=row['check_name'],read_position=False)
            add_fallback_ocr(reading,read_name=row['check_name'])
            readings.append({**reading,'matched_position':row['internal_position']})
        return readings

    confirmed = recheck_neighbors(anchors,sources,{pos:row['needs_name'] for pos,row in anchors.items()},
                                  resolve,progress,read_frame)
    field = TAB_FIELD[tab]
    for record in records:
        position = record['internal_position']
        if position not in anchors:
            continue
        row = anchors[position]
        if 'name' in confirmed[position]:
            record['name'] = row['name'].strip()
        if 'value' in confirmed[position]:
            record[field] = int(row['value'])
            record['review_reasons'] = [
                reason for reason in record['review_reasons']
                if reason not in ('uncertain_stat', 'Statistic unreadable', 'conflicting_stat', 'multi_view_difference',
                                  f'{NUMBER_REVIEW_PREFIX} the scanned number is unclear.')
            ]
        record['review_reasons'].extend(row.get('neighbor_conflicts',[]))
        record['review_required']=bool(record['review_reasons'])


VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm'}
TAB_FIELD = {'Power': 'power', 'Kills': 'kills', 'Donation': 'donations'}


def _record_from_csv(entry: dict, directory: Path, page_info: dict, tab: str):
    basename = str(entry.get('avatar') or '')
    match = re.fullmatch(r'avatars/avatar_(\d{3})\.png', basename)
    if not match:
        return None
    position = int(match.group(1))
    if not 1 <= position <= 100:
        return None
    avatar_file = directory / basename
    avatar_b64 = base64.b64encode(avatar_file.read_bytes()).decode() if avatar_file.exists() else None
    proof = directory / 'row_crops' / f'row_{position:03d}.png'
    proof_b64 = None
    if proof.exists():
        image = cv2.imread(str(proof))
        if image is not None and image.size:
            ok, buffer = cv2.imencode('.webp', image, [cv2.IMWRITE_WEBP_QUALITY, 76])
            if ok and len(buffer) < 180000:
                proof_b64 = base64.b64encode(buffer.tobytes()).decode()
    issues = [v.strip() for v in (entry.get('review') or '').split(';') if v.strip()]
    rank = entry.get('rank') or 'Unknown'
    name = (entry.get('name') or '').strip()
    field = TAB_FIELD[tab]
    raw_stat = str(entry.get(field) or '').replace(',', '').strip()
    statistic = int(raw_stat) if raw_stat.isdecimal() else None
    if rank not in ('R1', 'R2', 'R3', 'R4', 'R5'):
        rank = 'Unknown'
        issues.append('Rank badge not confidently recognized')
    if not name:
        issues.append('Name unreadable')
    if statistic is None:
        issues.append('Statistic unreadable')
    try:
        name_score = float(entry.get('confidence_name') or 0)
        number_score = float(entry.get('confidence_stat') or 0)
    except (ValueError, TypeError):
        name_score = number_score = 0
    if name_score < 55 or number_score < 60:
        issues.append('OCR confidence below recommended threshold')
    if statistic is None or number_score < 55:
        issues.append(f'{NUMBER_REVIEW_PREFIX} the scanned number is unclear.')
    page_number = int(entry.get('page') or 0)
    meta = page_info.get(page_number, {})
    src = meta.get('original_filename') or meta.get('source') or 'ranking upload'
    stamp = meta.get('seconds')
    source = src + (f'@{float(stamp):.2f}s' if stamp is not None else '')
    output = {
        'avatar_base64': avatar_b64,
        'rank': rank,
        'name': name,
        'power': statistic if field == 'power' else None,
        'kills': statistic if field == 'kills' else None,
        'donations': statistic if field == 'donations' else None,
        'tab': field,
        'review_required': bool(issues),
        'review_reasons': list(dict.fromkeys(issues)),
        'internal_position': position,
        'source_type': 'leaderboard',
        'source_image': source,
        'evidence_base64': proof_b64,
        'audit': {
            'scanner': 'user-supplied-ranking-scanner-v3',
            'internal_position': position,
            'source_page': page_number,
            'name_confidence': name_score,
            'stat_confidence': number_score,
            'observed_pages': entry.get('observed_pages'),
        },
    }
    return output


def _page_group(path: Path, staging: Path, idx: int, progress=None):
    """Keep screenshots and full-resolution video pauses in the same page format."""
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        info = detect(path,progress=progress)
        manifest = extract(path, staging, *info)
        pages = []
        for item in manifest['kept']:
            full = staging / item['image']
            image = cv2.imread(str(full))
            if image is None:
                continue
            selected, coverage = tabs(image)
            # Original video has an orange tab. If orange barely appears, do
            # not silently label the wrong statistic category.
            scores = sorted(coverage.values(), reverse=True)
            if scores[0] < .02 or (scores[1] > .01 and scores[0] < 1.25*scores[1]):
                raise ValueError('Selected Power/Kills/Donation tab is not clear; check recording.')
            pages.append({**item, 'original_filename': path.name,
                          'video_path': str(path), 'image_path': full,
                          'selected_tab': selected, 'input_kind': 'video',
                          'total_video_frames': manifest['total_video_frames'],
                          'fps': manifest['fps']})
        return pages, manifest['total_video_frames']
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError('Cannot decode screenshot: ' + path.name)
    selected, coverage = tabs(image)
    scores = sorted(coverage.values(), reverse=True)
    if scores[0] < .02 or (scores[1] > .01 and scores[0] < 1.25*scores[1]):
        raise ValueError('Orange ranking tab was not detected in screenshot: ' + path.name)
    # The page is used unchanged. Only a separate copy is created for the
    # original scanner's expected temporary manifest structure.
    target = staging / 'pages' / f'page_{idx:03d}.png'
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), image)
    return [{ 'page': idx, 'frame': None, 'seconds': None,
              'image_path': target, 'original_filename': path.name,
              'video_path': None, 'selected_tab': selected,
              'input_kind': 'image', 'fps': None }], 0


def scan_tested_rankings_upload(paths: list[Path], progress: Callable, corrections=None, master_keys=None):
    """Run original tested scan code on video pauses and uploaded screenshots.

    Each tab is scanned separately so Power position 1 and Kills position 1
    cannot be incorrectly treated as the same player. The approval pipeline
    handles corroborated identities after this function returns.
    """
    if not paths:
        raise ValueError('No media supplied.')
    records = []
    def resolve(name):
        return corrected_name(name,corrections or {},master_keys or set())
    diagnostics = {'scanner':'tested-user-supplied-v3', 'distinct_pages':0,
                   'source_frames':0, 'tabs':[], 'missing_positions':{},
                   'review_count':0, 'recovered_rows':0, 'complete_rows':0}
    warnings = []
    audit = []
    root = Path(paths[0]).parent
    with tempfile.TemporaryDirectory(prefix='rankings_verified_', dir=str(root)) as tmp:
        workspace = Path(tmp)
        groups = defaultdict(list)
        for idx, source in enumerate(paths,1):
            progress(2+round(23*(idx-1)/len(paths)), 100,
                     f'Finding stationary pages in {source.name}…')
            folder = workspace / ('input_' + str(idx))
            pages, frame_count = _page_group(Path(source),folder,idx,progress)
            diagnostics['source_frames'] += frame_count
            for page in pages:
                groups[(page['selected_tab'],page['input_kind'],page.get('video_path'))].append(page)
        if not groups:
            raise ValueError('No stable ranking pages could be found.')
        diagnostics['distinct_pages'] = sum(len(pages) for pages in groups.values())
        for group_idx,((tab,kind,video_path),pages) in enumerate(groups.items(),1):
            folder = workspace / f'group_{group_idx:03d}'
            (folder/'pages').mkdir(parents=True,exist_ok=True)
            kept=[]; metadata={}
            for idx,page in enumerate(pages,1):
                name=f'page_{idx:03d}.png'
                # Original pages are high-quality PNG; avoid recompression.
                shutil.copyfile(page['image_path'], folder/'pages'/name)
                item={'page':idx,'frame':page.get('frame') if kind=='video' else 0,
                      'seconds':page.get('seconds'),'duration':page.get('duration',0),
                      'image':'pages/'+name}
                kept.append(item)
                metadata[idx]=page
            manifest={'total_video_frames':diagnostics['source_frames'],
                      'fps':pages[0].get('fps') or 30, 'kept':kept}
            (folder/'manifest.json').write_text(json.dumps(manifest))
            progress(28+round(59*(group_idx-1)/len(groups)),100,
                     f'Reading {tab} complete rows, group {group_idx}/{len(groups)}…')
            # --keep-pages feeds the submitted source unchanged real screenshots.
            # Sample-specific manual overrides are deliberately NEVER supplied.
            # For screenshot groups, pass an actual image path, even when the
            # upload batch also contains a video. There are no intervening
            # frames to recover from still images.
            scan_input=(str(video_path) if kind=='video'
                        else str(root / pages[0]['original_filename']))
            command=[sys.executable,'-u',str(Path(__file__).with_name('scan_rankings.py')),
                     scan_input,'--output',str(folder),'--keep-pages']
            # Preserve the supplied OCR algorithm, but stream its per-page
            # milestones instead of blocking silently until a 15-minute timeout.
            # Python -u ensures that rendered status is available during a scan.
            scan_start=time.monotonic()
            output_tail=[]
            page_number=0
            last_heartbeat=scan_start
            deadline=scan_start+720
            process=subprocess.Popen(command,stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,text=True,bufsize=1)
            try:
                while True:
                    if time.monotonic()>deadline:
                        raise TimeoutError('Ranking OCR exceeded 12 minutes. Try a shorter recording.')
                    available,_,_=select.select([process.stdout],[],[],3)
                    if available:
                        line=process.stdout.readline()
                        if line:
                            output_tail.append(line.strip())
                            output_tail=output_tail[-12:]
                            found=re.match(r'page\s+(\d+):\s+(\d+)\s+non-pinned',line)
                            if found:
                                page_number=max(page_number,int(found.group(1)))
                                progress(29+round(56*page_number/max(1,len(kept))),100,
                                    f'Recognizing {tab} page {page_number} of {len(kept)} ({found.group(2)} rows)…')
                            elif 'calibrated row height:' in line:
                                progress(29,100,f'Calibrating {tab} player rows…')
                            elif 'Starting targeted recovery' in line:
                                progress(86,100,f'Checking missing {tab} leaderboard rows…')
                            elif 'SUMMARY' in line:
                                progress(90,100,f'Checking {tab} ranking order and gaps…')
                    if process.poll() is None and time.monotonic()-last_heartbeat>12:
                        last_heartbeat=time.monotonic()
                        progress(29+round(56*page_number/max(1,len(kept))),100,
                            f'Working on {tab} page {min(len(kept),page_number+1)} of {len(kept)}…')
                    if process.poll() is not None:
                        output_tail.extend(line.strip() for line in process.stdout if line.strip())
                        break
                if process.returncode:
                    # Never expose raw traceback or local file paths to users.
                    raise ValueError(f'The {tab} ranking OCR could not finish; check the scan details and retry.')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if process.stdout:
                    process.stdout.close()
            if not (folder/'rankings.csv').exists():
                raise ValueError('Ranking OCR produced no results.')
            report=json.loads((folder/'audit.json').read_text())
            group_records, anchors, sources = [], {}, {}
            with (folder/'rankings.csv').open(newline='',encoding='utf-8-sig') as f:
                for entry in csv.DictReader(f):
                    record=_record_from_csv(entry,folder,metadata,tab)
                    if not record:
                        continue
                    group_records.append(record)
                    unmatched = master_keys is not None and name_key(resolve(record['name'])) not in master_keys
                    if not video_path:
                        continue
                    if not entry.get('center_y'):
                        record['review_reasons'].append(f'{NUMBER_REVIEW_PREFIX} the row could not be located in three frames.')
                        record['review_required']=True
                        continue
                    page = record['audit']['source_page']
                    frame = entry.get('source_frame') or metadata.get(page,{}).get('frame')
                    if frame is None:
                        record['review_reasons'].append(f'{NUMBER_REVIEW_PREFIX} source frame unavailable.')
                        record['review_required']=True
                        continue
                    portrait = cv2.imread(str(folder/entry['avatar']),cv2.IMREAD_GRAYSCALE)
                    if portrait is None:
                        record['review_reasons'].append(f'{NUMBER_REVIEW_PREFIX} the row could not be matched across frames.')
                        record['review_required']=True
                        continue
                    pos = record['internal_position']
                    field = TAB_FIELD[tab]
                    sources[page] = (Path(video_path),int(frame))
                    anchors[pos] = {'internal_position':pos,'page':page,'center':int(entry['center_y']),
                                    'signature':cv2.resize(portrait,(24,24)),
                                    'needs_name':record['review_required'] or unmatched,
                                    'name':record['name'],'name_conf':record['audit']['name_confidence'],
                                    'value':str(record[field]) if record[field] is not None else '',
                                    'value_conf':record['audit']['stat_confidence']}
            if anchors:
                recheck_ranking_group(group_records,anchors,sources,tab,
                                      report['calibrated_row_height_pixels'],resolve,
                                      lambda _p,_n,message: progress(94,100,message))
            records.extend(group_records)
            audit.append({'source_group':group_idx,'tab':tab,'kind':kind,
                          'pages':len(kept),'scanner_report':report})
            diagnostics['tabs'].append(TAB_FIELD[tab])
            diagnostics['complete_rows'] += report.get('raw_row_observations',0)
            diagnostics['recovered_rows'] += len(report.get('recovered_missing_rows') or [])
            missing = report.get('missing_positions') or []
            diagnostics['missing_positions'].setdefault(TAB_FIELD[tab],[]).extend(missing)
            if missing:
                warnings.append(f'{tab}: missing positions still require review: '+
                                ', '.join(str(x) for x in missing[:15]))
        # Deduplicate exact same tab/position/name observations in a screenshot
        # plus video upload, but never merge different names or unknown positions.
        unique={}
        for row in records:
            key=(row['tab'],row['internal_position'],row['name'].casefold())
            prev=unique.get(key)
            if prev is None or (prev['review_required'] and not row['review_required']):
                unique[key]=row
            if prev is not None and prev[row['tab']] is not None and row[row['tab']] is not None and prev[row['tab']]!=row[row['tab']]:
                chosen=unique[key]
                chosen['review_reasons'].append(
                    f"{NUMBER_REVIEW_PREFIX} captures read {prev[row['tab']]} and {row[row['tab']]}.")
                chosen['review_required']=True
        records=list(unique.values())
        diagnostics['tabs']=sorted(set(diagnostics['tabs']))
        diagnostics['distinct_entries']=len(records)
        diagnostics['review_count']=sum(bool(p['review_required']) for p in records)
        progress(97,100,'Checking leaderboard coverage and review flags…')
    return {'players':records,'frames_examined':diagnostics['distinct_pages'],
            'warnings':warnings,'audit':audit,'diagnostics':diagnostics}
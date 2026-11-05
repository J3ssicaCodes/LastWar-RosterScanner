/* Shared media uploads and notifications for Alliance Tools.
 * The phone uploads once; the Render worker scans independently of the tab. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let destination='storm',files=[],busy=false,pending=[],notified=new Set();
  const NOTIFICATIONS='lastwar.media.notified.v1';
  try{notified=new Set(JSON.parse(localStorage.getItem(NOTIFICATIONS)||'[]'));}catch{}
  const css=document.createElement('style');
  css.textContent=`
  .media-top-button{display:inline-flex;gap:9px;align-items:center;justify-content:center;min-width:155px;white-space:nowrap;margin-left:auto}
  .media-top-button .media-count{min-width:20px;border-radius:12px;padding:2px 5px;background:#9a578c;color:white;font-size:11px}
  #mediaDialog{width:min(580px,calc(100vw - 22px));max-height:90vh;border:1px solid var(--stroke);border-radius:17px;background:var(--panel);color:var(--text);box-shadow:0 18px 70px #0009;padding:23px;overflow:auto}
  #mediaDialog::backdrop{background:#040712b3}
  .media-titlebar,.media-actions{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
  .media-titlebar h2{margin:0;font-size:22px}.media-titlebar p{margin:4px 0 0;color:var(--muted)}
  .media-destinations{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:23px 0 18px}
  .media-destination{border:1px solid var(--stroke);border-radius:12px;padding:15px 7px;background:var(--panel2);color:var(--text);cursor:pointer;font:inherit;font-weight:700}
  .media-destination[aria-pressed=true]{border-color:#bb7fcc;background:color-mix(in srgb,var(--violet) 16%,var(--panel));box-shadow:inset 0 0 0 1px #bb7fcc}
  .media-destination:disabled{opacity:.4;cursor:not-allowed}
  .media-select{border:2px dashed #a77bba;border-radius:14px;padding:23px 14px;text-align:center;cursor:pointer;background:var(--panel2);margin:8px 0 16px}
  .media-select strong{display:block;margin:6px 0;font-size:17px}.media-select small{display:block;color:var(--muted)}
  .media-files{white-space:normal;overflow-wrap:anywhere;color:var(--muted);font-size:12px;margin:11px 0}
  .media-progress{margin:14px 0}.media-progress progress{width:100%;accent-color:#a67ac1;height:11px}
  .media-progress small{display:block;margin-top:4px;color:var(--muted)}
  .media-banner{border:1px solid #9476b0;border-radius:11px;padding:12px 15px;margin:12px 0 18px;background:var(--panel2)}
  .media-banner[hidden],.media-progress[hidden]{display:none}
  .media-review{margin-top:18px}.media-review[hidden]{display:none}
  .media-review table{min-width:700px}.media-review td input,.media-review td select{width:100%;min-width:70px;box-sizing:border-box;font:inherit}
  .media-review .media-review-actions{display:flex;justify-content:flex-end;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:11px}
  .media-review .media-review-actions .btn{margin:0}
  .media-review th:first-child,.media-review td:first-child{width:45px}
  .media-review .media-flag{display:block;font-size:11px;color:#f4bd80;line-height:1.4;margin-top:4px}
  .media-evidence{margin-top:6px;font-size:12px;color:var(--muted)}
  .media-evidence summary{cursor:pointer;text-decoration:underline}
  .media-evidence img{display:block;max-width:min(520px,75vw);width:100%;height:auto;border:1px solid var(--stroke);border-radius:7px;margin:6px 0;image-rendering:auto}
  #mediaDiagnostics{color:var(--muted);margin:7px 0;font-size:13px}
  #mediaDiagnostics[hidden]{display:none}
  .pl-rank-heading th{background:var(--panel2);padding:0!important;border-top:1px solid var(--stroke)}
  .pl-rank-heading button{font:inherit;font-weight:700;color:var(--text);display:flex;width:100%;border:0;background:transparent;justify-content:space-between;padding:13px 18px;cursor:pointer;align-items:center;text-align:left}
  .pl-rank-heading small{color:var(--muted);font-size:12px}.pl-rank-heading strong{font-size:15px}
  #playerListView .pl-upload-grid,#playerListView .pl-scan-progress,#playerListView #plReviewPanel{display:none!important}
  #rosterScannerView [aria-labelledby="uploadHeading"]{display:none!important}
  @media(max-width:680px){.media-top-button{width:100%;margin:4px 0}.media-destination{font-size:12px;padding:13px 4px}#mediaDialog{padding:15px}.media-titlebar h2{font-size:20px}}
  `;
  document.head.append(css);

  const top=document.querySelector('header.top');
  const topButton=document.createElement('button');
  topButton.id='mediaUploadButton';topButton.className='btn primary media-top-button';
  topButton.type='button';topButton.innerHTML='<span aria-hidden="true">⇧</span> Media Upload <span class="media-count" id="mediaBadge" hidden>0</span>';
  top?.append(topButton);

  const dialog=document.createElement('dialog');
  dialog.id='mediaDialog';dialog.setAttribute('aria-labelledby','mediaTitle');
  dialog.innerHTML=`<div class="media-titlebar"><div><div class="eyebrow">Alliance Tools</div><h2 id="mediaTitle">Media Upload</h2><p>Scan on Render, even after you lock your screen.</p></div><button type="button" class="btn" id="mediaClose" aria-label="Close media uploader">✕ Close</button></div>
  <div class="media-destinations" role="group" aria-label="Choose media destination">
    <button type="button" class="media-destination" data-dest="storm" aria-pressed="true">▦ Storm Roster</button>
    <button type="button" class="media-destination" data-dest="players" aria-pressed="false">♙ Player List</button>
    <button type="button" class="media-destination" disabled title="Coming later" aria-disabled="true">▤ VS Scores<br><small>Coming soon</small></button>
  </div>
  <div class="media-select" role="button" tabindex="0" id="mediaSelect"><span style="font-size:32px" aria-hidden="true">⇧</span><strong id="mediaSelectTitle">Choose Storm Roster screenshots</strong><small id="mediaSelectHint">PNG, JPG or WebP images only.</small><input type="file" id="mediaFileInput" accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.m4v" multiple hidden></div>
  <div id="mediaFileNames" class="media-files">No files selected</div>
  <div class="media-progress" id="mediaProgress" hidden><progress id="mediaBar" max="100" value="0"></progress><small id="mediaProgressText">Uploading…</small></div>
  <div id="mediaMessage" class="media-banner" role="status" aria-live="polite" hidden></div>
  <div class="media-actions"><small style="color:var(--muted)">Uploaded media is deleted when scanning completes.</small><button class="btn primary" id="mediaSubmit" disabled>Upload and scan</button></div>`;
  document.body.append(dialog);
  const banner=document.createElement('div');banner.id='mediaNotification';banner.className='media-banner';banner.hidden=true;banner.setAttribute('role','status');
  top?.after(banner);

  const playerView=$('playerListView');
  const pendingPanel=document.createElement('section');
  pendingPanel.id='mediaPendingPanel';pendingPanel.className='panel pl-section media-review';pendingPanel.hidden=true;
  pendingPanel.innerHTML=`<div class="pl-title-row"><div><div class="eyebrow">Awaiting your approval</div><h2>Player changes to review</h2><p>New uploads are staged here, not saved to the official Player List until approved.</p></div><span id="mediaPendingCount" class="pl-count"></span></div>
    <div class="media-review-actions"><button class="btn" type="button" id="mediaDismiss">Dismiss unchecked</button><button class="btn primary" type="button" id="mediaApprove">Approve checked changes</button></div>
    <p id="mediaDiagnostics" hidden></p>
    <div class="pl-table-wrap"><table class="pl-table"><thead><tr><th>Use</th><th>Change</th><th>Photo</th><th>Rank</th><th>Player</th><th>Power</th><th>Kills</th><th>Donations</th><th>Power (M) / Profile</th><th>HQ Lv.</th></tr></thead><tbody id="mediaPendingRows"></tbody></table></div>
    <p class="pl-hint">Check the entries to approve. Unchecked entries remain pending until you explicitly dismiss them. Correct uncertain names and numbers before approval.</p>`;
  const directory=playerView?.querySelector('[aria-labelledby="plDirectoryTitle"]');
  directory?.before(pendingPanel);

  function announce(text){banner.hidden=false;banner.textContent=text;}
  function message(text,error=false){const el=$('mediaMessage');el.hidden=!text;el.textContent=text;el.style.borderColor=error?'#db7284':'#9476b0';}
  function updateTarget(){
    dialog.querySelectorAll('[data-dest]').forEach(btn=>btn.setAttribute('aria-pressed',String(btn.dataset.dest===destination)));
    const isStorm=destination==='storm';
    $('mediaSelectTitle').textContent=isStorm?'Choose Storm Roster screenshots':'Choose leaderboard screenshots or an MP4 recording';
    $('mediaSelectHint').textContent=isStorm?'PNG, JPG or WebP images only. MP4 is not allowed for Storm Roster.':'PNG, JPG, WebP, MP4, MOV or WebM. Record the Power or Kills leaderboard while scrolling; pause on each page.';
    fileInput.accept=isStorm?'image/png,image/jpeg,image/webp':"image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.m4v";
    if(isStorm&&files.some(f=>/\.(mp4|mov|m4v|webm)$/i.test(f.name))){
      files=[];fileInput.value='';$('mediaFileNames').textContent='No files selected';
      $('mediaSubmit').disabled=true;
      message('Storm Roster accepts screenshots only. Choose images again.');
    }
  }
  topButton.onclick=()=>{message('');dialog.showModal();};
  $('mediaClose').onclick=()=>dialog.close();
  dialog.querySelectorAll('[data-dest]').forEach(btn=>btn.addEventListener('click',()=>{destination=btn.dataset.dest;updateTarget();}));
  const fileInput=$('mediaFileInput');
  $('mediaSelect').onclick=()=>{if(!busy)fileInput.click();};
  $('mediaSelect').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();if(!busy)fileInput.click();}};
  fileInput.onchange=()=>{
    files=[...fileInput.files||[]];
    const nVideo=files.filter(f=>/\.(mp4|mov|m4v|webm)$/i.test(f.name)).length;
    if(destination==='storm'&&nVideo>0){
      message('Storm Roster accepts image screenshots only. Videos are disabled.',true);
      files=[];fileInput.value='';
    }else if(nVideo>1){
      message('Choose one recording at a time. You can add screenshots.',true);
      files=[];fileInput.value='';
    }
    $('mediaFileNames').textContent=files.length?files.map(f=>f.name).join(' · '):'No files selected';
    $('mediaSubmit').disabled=!files.length||busy;
  };
  updateTarget();
  function setProgress(n,text){$('mediaProgress').hidden=false;$('mediaBar').value=n;$('mediaProgressText').textContent=text;}
  $('mediaSubmit').onclick=async()=>{
    if(!files.length||busy)return;
    busy=true;$('mediaSubmit').disabled=true;message('');setProgress(1,'Uploading media to Render…');
    try{
      const form=new FormData();files.forEach(f=>form.append('files',f,f.name));
      const job=await new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest();
        const url='/api/media/upload?destination='+encodeURIComponent(destination);
        xhr.open('POST',url);xhr.timeout=600000;
        xhr.upload.onprogress=e=>{if(e.lengthComputable)setProgress(Math.min(99,Math.round(e.loaded/e.total*100)),'Uploading: '+Math.round(e.loaded/e.total*100)+'%');};
        xhr.onload=()=>{let payload={};try{payload=JSON.parse(xhr.responseText);}catch{}if(xhr.status<200||xhr.status>=300)reject(Error(payload.detail||'Upload failed: '+xhr.status));else resolve(payload);};
        xhr.onerror=()=>reject(Error('Upload connection failed. Check your connection and retry.'));
        xhr.ontimeout=()=>reject(Error('Upload timed out before Render received the recording.'));
        xhr.send(form);
      });
      setProgress(100,'Upload accepted. Render is scanning in the background.');
      message('Upload complete! You may close this window or lock your phone. A notice will appear when the scan is finished.');
      files=[];fileInput.value='';$('mediaFileNames').textContent='No files selected';
      announce('✓ '+(destination==='storm'?'Storm Roster':'Player List')+' upload accepted. Scanning continues on Render.');
      await refreshJobs();
    }catch(e){message(e.message,true);}
    finally{busy=false;$('mediaSubmit').disabled=!files.length;}
  };

  async function getJson(url,options){const r=await fetch(url,{cache:'no-store',...options});let data={};try{data=await r.json();}catch{}if(!r.ok)throw Error(data.detail||'Request failed ('+r.status+')');return data;}
  async function refreshPending(){
    try{
      const result=await getJson('/api/media/pending');pending=result.players||[];
      pendingPanel.hidden=!pending.length;
      $('mediaPendingCount').textContent=pending.length+' waiting';
                  $('mediaPendingRows').innerHTML=pending.map(p=>{
        const flag=(p.review_required?(p.review_reasons||[]).join('; '):'');
        const avatar=p.avatar_base64&&/^[A-Za-z0-9+/=]+$/.test(p.avatar_base64)?
          '<img class="pl-avatar" src="data:image/'+(p.avatar_base64.startsWith('UklGR')?'webp':'png')+';base64,'+p.avatar_base64+'" alt="Player avatar">':'♡';
        const evidence=p.evidence_base64 && p.evidence_base64.length<350000 && /^[A-Za-z0-9+/=]+$/.test(p.evidence_base64) ?
          '<details class="media-evidence"><summary>View captured leaderboard row</summary><img loading="lazy" src="data:image/webp;base64,'+
          p.evidence_base64+'" alt="Cropped original leaderboard row for '+escape(p.name||'this player')+'"></details>' : '';
        const numeric=(field,step='1')=>'<td><input data-field="'+field+'" type="number" step="'+step+'" min="0" value="'+(p[field]??'')+'" aria-label="'+field+'"></td>';
        const type=p.change_kind==='new'?'New':p.change_kind==='updated'?'Updated':'Needs review';
        return '<tr data-pending-id="'+p.id+'"><td><input type="checkbox" data-field="include" checked aria-label="Include '+escape(p.name||'unrecognized player')+'"></td>'+
          '<td><span class="pl-status '+(type==='New'?'new':type==='Updated'?'active':'removed')+'">'+type+'</span></td>'+
          '<td>'+avatar+'</td><td><select data-field="rank">'+['Unknown','R5','R4','R3','R2','R1'].map(rank=>'<option value="'+rank+'" '+(p.rank===rank?'selected':'')+'>'+rank+'</option>').join('')+'</select></td>'+
          '<td><input data-field="name" value="'+escape(p.name||'')+'" maxlength="80" aria-label="Player name">'+
          (p.tab?'<small class="pl-source-label">'+escape(p.tab.toUpperCase())+'</small>':'')+
          (p.source_image?'<small class="pl-source-label" title="Original file and timestamp">'+escape(p.source_image)+'</small>':'')+
          (flag?'<small class="media-flag">'+escape(flag)+'</small>':'')+evidence+'</td>'+
          numeric('power')+numeric('kills')+numeric('donations')+numeric('power_millions','0.1')+numeric('hq_level')+'</tr>';
      }).join('');
      await loadDiagnostics();
      $('mediaBadge').hidden=!pending.length;$('mediaBadge').textContent=String(pending.length);
    }catch(e){console.warn('Could not load pending media:',e.message);}
  }
  async function loadDiagnostics(){
    const jobs=new Set(pending.map(p=>p.job_id));
    const box=$('mediaDiagnostics');
    if(!jobs.size){box.hidden=true;return;}
    const parts=[];
    for(const id of [...jobs].slice(0,3)){
      try{
        const data=await getJson('/api/media/jobs/'+encodeURIComponent(id)+'/diagnostics');
        const d=data.diagnostics||{};
        if(!d.tabs?.length)continue;
        parts.push('Categories: '+d.tabs.join(', ')+' · '+(d.distinct_pages??0)+' distinct pages · '+
          (d.complete_rows??0)+' complete rows · '+(d.distinct_entries??0)+' entries · '+
          (d.missing_count??0)+' gaps · '+(d.recovered_rows??0)+' recovered · '+
          (d.review_count??0)+' needing review');
      }catch{}
    }
    box.textContent=parts.join(' | ');
    box.hidden=!parts.length;
  }
  $('mediaApprove').onclick=async()=>{
    const rows=[...$('mediaPendingRows').querySelectorAll('tr[data-pending-id]')].map(tr=>{
      const val=field=>tr.querySelector('[data-field="'+field+'"]');
      const n=v=>v===''?null:Number(v);
      return {id:Number(tr.dataset.pendingId),include:val('include').checked,name:val('name').value.trim(),
              rank:val('rank').value,power_millions:n(val('power_millions').value),hq_level:n(val('hq_level').value),
              power:n(val('power').value),kills:n(val('kills').value),donations:n(val('donations').value)};
    });
    if(!rows.length)return;
    const invalid=rows.filter(r=>r.include && (
      !r.name || r.name.length<2 || !/[\\p{L}]/u.test(r.name) ||
      !/^R[1-5]$/.test(r.rank) ||
      ['power','kills','donations'].some(field=>
         r[field]!==null&&(!Number.isSafeInteger(r[field])||r[field]<0)) ||
      (r.hq_level!==null&&(!Number.isInteger(r.hq_level)||r.hq_level<1||r.hq_level>99))
    ));
    if(invalid.length){
      announce('Review required: correct missing player names, ranks and invalid statistics, or uncheck those rows before approving.');
      return;
    }
    if(!rows.some(r=>r.include)){announce('Check at least one reviewed player to approve.');return;}
    $('mediaApprove').disabled=true;
    try{
      const result=await getJson('/api/media/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rows})});
      announce('✓ Player List updated: '+result.approved+' approved; unchecked entries remain pending.');
      await Promise.all([refreshPending(),window.playerListRefresh?.()]);
    }catch(e){announce('Unable to approve player changes: '+e.message);}
    finally{$('mediaApprove').disabled=false;}
  };
  $('mediaDismiss').onclick=async()=>{
    const ids=[...$('mediaPendingRows').querySelectorAll('tr[data-pending-id]')]
      .filter(tr=>!tr.querySelector('[data-field="include"]').checked)
      .map(tr=>Number(tr.dataset.pendingId));
    if(!ids.length){announce('Uncheck the incorrect entries you want to dismiss.');return;}
    if(!confirm('Permanently dismiss '+ids.length+' unchecked OCR entries? They will not update your player database.'))return;
    $('mediaDismiss').disabled=true;
    try{
      const result=await getJson('/api/media/dismiss',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
      announce('Dismissed '+result.dismissed+' unwanted OCR entries.');await refreshPending();
    }catch(e){announce('Could not dismiss entries: '+e.message);}
    finally{$('mediaDismiss').disabled=false;}
  };
  async function refreshJobs(){
    try{
      const {jobs=[]}=await getJson('/api/media/jobs');
      const newAlerts=[];
      for(const job of jobs){
        if(['ready','review','error','interrupted'].includes(job.status)){
          const key=job.id+':'+job.status;
          if(!notified.has(key)){
            newAlerts.push(job);notified.add(key);
          }
        }
      }
      if(newAlerts.length){
        for(const job of newAlerts){
          if(job.status==='error'||job.status==='interrupted'){
            announce('⚠ '+(job.destination==='storm'?'Storm Roster':'Player List')+
              ' scan did not finish: '+(job.message||'Server processing was interrupted. Please retry.'));
          }else if(job.destination==='storm'){
            await window.loadServerStormRoster?.();
            announce('✓ Storm Roster scanned! '+job.result_count+' players loaded into Roster Scanner.');
          }else{
            await refreshPending();
            announce(job.result_count?
              '✓ Player List scan finished. '+job.result_count+' new, changed, or uncertain entries are ready for approval.' :
              '✓ Player List scan finished. No player changes were found.');
          }
        }
      }
      try{localStorage.setItem(NOTIFICATIONS,JSON.stringify([...notified].slice(-100)));}catch{}
      if(jobs.some(j=>j.status==='scanning'||j.status==='queued')){
        const j=jobs.find(j=>j.status==='scanning'||j.status==='queued');
        if(!newAlerts.length)announce('⌛ '+(j.destination==='storm'?'Storm Roster':'Player List')+' scan '+j.progress+'%: '+j.message);
      }
    }catch(e){console.warn('Media jobs temporarily unavailable:',e.message);}
  }
  window.refreshMediaUploads=()=>Promise.all([refreshJobs(),refreshPending()]);
  window.addEventListener('focus',()=>refreshJobs());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)window.refreshMediaUploads();});
  setInterval(refreshJobs,8000);
  window.loadServerStormRoster?.();
  window.refreshMediaUploads();
})();
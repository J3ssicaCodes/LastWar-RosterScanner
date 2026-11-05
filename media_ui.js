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
  <div class="media-select" role="button" tabindex="0" id="mediaSelect"><span style="font-size:32px" aria-hidden="true">⇧</span><strong>Choose screenshots or a screen recording</strong><small>PNG, JPG, WebP, MP4, MOV or WebM. One video plus optional screenshots.</small><input type="file" id="mediaFileInput" accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.m4v" multiple hidden></div>
  <div id="mediaFileNames" class="media-files">No files selected</div>
  <label id="mediaCompleteLabel" style="display:none;gap:10px;align-items:flex-start;margin:12px 0"><input type="checkbox" id="mediaComplete"> <span><b>Complete Player List recording</b><small style="display:block;color:var(--muted)">Only enable if the entire member list was captured. Missing players can then be flagged after review.</small></span></label>
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
    <div class="media-review-actions"><label><input type="checkbox" id="mediaApproveComplete"> This was a complete list</label><button class="btn primary" type="button" id="mediaApprove">Approve reviewed changes</button></div>
    <div class="pl-table-wrap"><table class="pl-table"><thead><tr><th>Use</th><th>Photo</th><th>Rank</th><th>Player</th><th>Power (M)</th><th>HQ Lv.</th></tr></thead><tbody id="mediaPendingRows"></tbody></table></div>
    <p class="pl-hint">Uncheck false detections to dismiss them. Correct names and values here before approval.</p>`;
  const directory=playerView?.querySelector('[aria-labelledby="plDirectoryTitle"]');
  directory?.before(pendingPanel);

  function announce(text){banner.hidden=false;banner.textContent=text;}
  function message(text,error=false){const el=$('mediaMessage');el.hidden=!text;el.textContent=text;el.style.borderColor=error?'#db7284':'#9476b0';}
  function updateTarget(){dialog.querySelectorAll('[data-dest]').forEach(btn=>btn.setAttribute('aria-pressed',String(btn.dataset.dest===destination)));$('mediaCompleteLabel').style.display=destination==='players'?'flex':'none';$('mediaComplete').checked=false;}
  topButton.onclick=()=>{message('');dialog.showModal();};
  $('mediaClose').onclick=()=>dialog.close();
  dialog.querySelectorAll('[data-dest]').forEach(btn=>btn.addEventListener('click',()=>{destination=btn.dataset.dest;updateTarget();}));
  const fileInput=$('mediaFileInput');
  $('mediaSelect').onclick=()=>{if(!busy)fileInput.click();};
  $('mediaSelect').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();if(!busy)fileInput.click();}};
  fileInput.onchange=()=>{
    files=[...fileInput.files||[]];
    const nVideo=files.filter(f=>/\.(mp4|mov|m4v|webm)$/i.test(f.name)).length;
    if(nVideo>1){message('Choose one recording at a time. You can add screenshots.',true);files=[];}
    $('mediaFileNames').textContent=files.length?files.map(f=>f.name).join(' · '):'No files selected';
    $('mediaSubmit').disabled=!files.length||busy;
  };
  function setProgress(n,text){$('mediaProgress').hidden=false;$('mediaBar').value=n;$('mediaProgressText').textContent=text;}
  $('mediaSubmit').onclick=async()=>{
    if(!files.length||busy)return;
    busy=true;$('mediaSubmit').disabled=true;message('');setProgress(1,'Uploading media to Render…');
    try{
      const form=new FormData();files.forEach(f=>form.append('files',f,f.name));
      const job=await new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest();
        const url='/api/media/upload?destination='+destination+'&full_capture='+$('mediaComplete').checked;
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
      $('mediaApproveComplete').checked=false;
      $('mediaApproveComplete').disabled=new Set(pending.map(p=>p.job_id)).size!==1 || !pending.every(p=>p.full_capture);
      $('mediaPendingRows').innerHTML=pending.map(p=>`<tr data-pending-id="${p.id}"><td><input type="checkbox" data-field="include" checked aria-label="Include ${escape(p.name)}"></td><td>${p.avatar_base64?`<img class="pl-avatar" src="data:image/webp;base64,${p.avatar_base64}" alt="Video portrait">`:'♡'}</td><td><select data-field="rank">${['R5','R4','R3','R2','R1'].map(r=>`<option ${p.rank===r?'selected':''}>${r}</option>`).join('')}</select></td><td><input data-field="name" value="${escape(p.name)}" maxlength="80" aria-label="Player name"></td><td><input data-field="power_millions" type="number" step="0.01" min="0" value="${p.power_millions??''}" aria-label="Power millions"></td><td><input data-field="hq_level" type="number" min="1" max="99" value="${p.hq_level??''}" aria-label="HQ level"></td></tr>`).join('');
      $('mediaBadge').hidden=!pending.length;$('mediaBadge').textContent=String(pending.length);
    }catch(e){console.warn('Could not load pending media:',e.message);}
  }
  $('mediaApprove').onclick=async()=>{
    const rows=[...$('mediaPendingRows').querySelectorAll('tr[data-pending-id]')].map(tr=>{
      const val=field=>tr.querySelector('[data-field="'+field+'"]');
      const n=v=>v===''?null:Number(v);
      return {id:Number(tr.dataset.pendingId),include:val('include').checked,name:val('name').value.trim(),
              rank:val('rank').value,power_millions:n(val('power_millions').value),hq_level:n(val('hq_level').value)};
    });
    if(!rows.length)return;
    $('mediaApprove').disabled=true;
    try{
      const result=await getJson('/api/media/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rows,full_capture:$('mediaApproveComplete').checked})});
      announce('✓ Player List updated: '+result.approved+' approved, '+result.dismissed+' dismissed.');
      await Promise.all([refreshPending(),window.playerListRefresh?.()]);
    }catch(e){announce('Unable to approve player changes: '+e.message);}
    finally{$('mediaApprove').disabled=false;}
  };
  async function refreshJobs(){
    try{
      const {jobs=[]}=await getJson('/api/media/jobs');
      const newAlerts=[];
      for(const job of jobs){
        if(job.status==='ready'||job.status==='review'){
          const key=job.id+':'+job.status;
          if(!notified.has(key)){
            newAlerts.push(job);notified.add(key);
          }
        }
      }
      if(newAlerts.length){
        for(const job of newAlerts){
          if(job.destination==='storm'){
            await window.loadServerStormRoster?.();
            announce('✓ Storm Roster scanned! '+job.result_count+' players loaded into Roster Scanner.');
          }else{
            await refreshPending();
            announce('✓ Player List scan finished. '+job.result_count+' entries are waiting for your review.');
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
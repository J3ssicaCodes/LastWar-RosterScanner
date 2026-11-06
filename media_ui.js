/* Shared media uploads and notifications for Alliance Tools.
 * The phone uploads once; the Render worker scans independently of the tab. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let destination='storm',files=[],busy=false,pending=[],notified=new Set(),driveSelection=null;
  const destinationLabel=value=>value==='storm'?'Storm Roster':value==='vs'?'VS Points':'Player List';
  const localDate=()=>{const d=new Date();return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');};
  const NOTIFICATIONS='lastwar.media.notified.v1';
  try{notified=new Set(JSON.parse(localStorage.getItem(NOTIFICATIONS)||'[]'));}catch{}
  const css=document.createElement('style');
  css.textContent=`
  .media-top-button{display:inline-flex;gap:9px;align-items:center;justify-content:center;white-space:nowrap}
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
  .media-source-options{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:-5px 0 15px}
  .media-drive-trash{display:flex;align-items:flex-start;gap:9px;font-size:13px;line-height:1.45;padding:7px 0}
  .media-drive-trash[hidden]{display:none}
  .media-files{white-space:normal;overflow-wrap:anywhere;color:var(--muted);font-size:12px;margin:11px 0}
  .media-progress{margin:14px 0}.media-progress progress{width:100%;accent-color:#a67ac1;height:11px}
  .media-progress small{display:block;margin-top:4px;color:var(--muted)}
  .media-banner{border:1px solid #9476b0;border-radius:11px;padding:12px 15px;margin:12px 0 18px;background:var(--panel2)}
  .media-banner[hidden],.media-progress[hidden]{display:none}
  .media-notification-trigger{display:block;width:100%;text-align:left;color:var(--text);font:inherit;cursor:pointer;line-height:1.5}
  .media-notification-trigger:hover,.media-notification-trigger:focus-visible{border-color:#c7a2e3;outline-offset:2px}
  #mediaQueueDialog{width:min(680px,calc(100vw - 22px));max-height:88vh;overflow:auto;border:1px solid var(--stroke);border-radius:17px;background:var(--panel);color:var(--text);box-shadow:0 18px 70px #0009;padding:22px}
  #mediaQueueDialog::backdrop{background:#040712b3}
  .media-queue-summary{margin:14px 0;color:var(--muted);font-size:14px}
  .media-queue-section{margin:16px 0}.media-queue-section h3{font-size:15px;margin:0 0 9px}
  .media-queue-list{list-style:none;padding:0;margin:0;display:grid;gap:9px}
  .media-queue-entry{border:1px solid var(--stroke);border-radius:11px;padding:12px;background:var(--panel2)}
  .media-queue-line{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
  .media-queue-entry strong{font-size:14px}.media-queue-entry small{display:block;color:var(--muted);font-size:12px;margin-top:4px}
  .media-queue-state{font-size:12px;font-weight:700;color:var(--text);white-space:nowrap}
  .media-queue-entry progress{width:100%;height:9px;accent-color:#a67ac1;margin-top:10px}
  .media-queue-entry .media-queue-message{overflow-wrap:anywhere;color:var(--muted);font-size:13px;margin-top:6px}
  .media-queue-empty{color:var(--muted);padding:14px 0}
  .media-queue-footer{display:flex;justify-content:flex-end;margin-top:18px}
  .media-review{margin-top:18px}.media-review[hidden]{display:none}
  .review-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:12px 0}
  .review-toolbar .btn,.review-card .btn{margin:0}
  .review-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,350px),1fr));gap:12px;align-items:start}
  .review-card{border:1px solid var(--stroke);background:var(--panel2);border-radius:12px;padding:14px;min-width:0}
  .review-card-head,.review-card-actions{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin-bottom:8px}
  .review-card-actions{justify-content:flex-end;margin:12px 0 0}
  .review-fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,130px),1fr));gap:8px}
  .review-fields label{display:grid;gap:3px;font-size:13px}
  .review-fields input,.review-fields select{width:100%;min-width:0!important;font:inherit}
  .review-name{grid-column:1/-1}
  .review-card [data-review-warning]{color:var(--text);background:rgba(190,120,0,.12);border-left:3px solid #b9781d;padding:6px 9px;margin:8px 0;overflow-wrap:anywhere}
  .review-card details{margin-top:8px}
  .review-card details img{display:block;width:100%;height:auto;margin-top:6px}
  .review-verify{display:flex;align-items:center;gap:6px;margin:10px 0}
  [data-review-warning][hidden]{display:none!important}
  [data-review-warning]{display:block}

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
  @media(max-width:680px){.media-destination{font-size:12px;padding:13px 4px}.media-queue-footer{gap:8px;flex-wrap:wrap}#mediaDialog,#mediaQueueDialog{padding:15px}.media-titlebar h2{font-size:20px}}
  `;
  document.head.append(css);

  const utilities=$('workspaceUtilities');
  const topButton=document.createElement('button');
  topButton.id='mediaUploadButton';topButton.className='btn primary media-top-button';
  topButton.setAttribute('aria-haspopup','dialog');
  topButton.setAttribute('aria-controls','mediaDialog');
  topButton.type='button';topButton.innerHTML='<span aria-hidden="true">⇧</span> Media Upload <span class="media-count" id="mediaBadge" hidden>0</span>';
  const queueButton=document.createElement('button');
  queueButton.type='button';queueButton.id='mediaQueueButton';queueButton.className='btn';
  queueButton.textContent='Scan Queue';queueButton.setAttribute('aria-haspopup','dialog');
  queueButton.setAttribute('aria-controls','mediaQueueDialog');
  utilities.append(topButton,queueButton);

  const dialog=document.createElement('dialog');
  dialog.id='mediaDialog';dialog.setAttribute('aria-labelledby','mediaTitle');
  dialog.innerHTML=`<div class="media-titlebar"><div><div class="eyebrow">Alliance Tools</div><h2 id="mediaTitle">Media Upload</h2><p>Choose where to import your media.</p></div><button type="button" class="btn" id="mediaClose" aria-label="Close media uploader">✕ Close</button></div>
  <div class="media-destinations" role="group" aria-label="Choose media destination">
    <button type="button" class="media-destination" data-dest="storm" aria-pressed="true">▦ Storm Roster</button>
    <button type="button" class="media-destination" data-dest="players" aria-pressed="false">♙ Player List</button>
    <button type="button" class="media-destination" data-dest="vs" aria-pressed="false">▤ VS Points</button>
  </div>
  <div class="media-select" role="button" tabindex="0" id="mediaSelect"><span style="font-size:32px" aria-hidden="true">⇧</span><strong id="mediaSelectTitle">Choose Storm Roster screenshots</strong><small id="mediaSelectHint">PNG, JPG or WebP images only.</small><input type="file" id="mediaFileInput" accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.m4v" multiple hidden></div>
  <div class="media-source-options"><button type="button" id="mediaDriveButton" class="btn">Select from Google Drive</button></div>
  <div id="mediaFileNames" class="media-files">No files selected</div>
  <label class="media-drive-trash" id="mediaDriveTrashWrap" hidden><input type="checkbox" id="mediaDriveTrash"> Move the original Google Drive file to Trash after the scan finishes successfully</label>
  <div class="media-progress" id="mediaProgress" hidden><progress id="mediaBar" max="100" value="0"></progress><small id="mediaProgressText">Uploading…</small></div>
  <div id="mediaMessage" class="media-banner" role="status" aria-live="polite" hidden></div>
  <div class="media-actions"><small style="color:var(--muted)">Known players merge automatically. Review new players and VS scores before saving.</small><button class="btn primary" id="mediaSubmit" disabled>Upload and scan</button></div>`;
  document.body.append(dialog);
  const banner=document.createElement('button');
  banner.type='button';banner.id='mediaNotification';
  banner.className='media-banner media-notification-trigger';banner.hidden=true;
  banner.setAttribute('aria-live','polite');
  banner.setAttribute('aria-haspopup','dialog');
  banner.setAttribute('aria-controls','mediaQueueDialog');
  banner.title='View current scanning status and queue';
  document.querySelector('.workspace-tabs').after(banner);
  const queueDialog=document.createElement('dialog');
  queueDialog.id='mediaQueueDialog';queueDialog.setAttribute('aria-labelledby','mediaQueueTitle');
  queueDialog.innerHTML=`<div class="media-titlebar">
    <div><div class="eyebrow">Media Upload</div><h2 id="mediaQueueTitle">Scan Queue</h2>
      <p>Current progress, waiting uploads and recent results.</p></div>
    <button type="button" class="btn" id="mediaQueueClose" aria-label="Close scan queue">✕ Close</button>
  </div>
  <p class="media-queue-summary" id="mediaQueueSummary" role="status" aria-live="polite">Loading scans…</p>
  <div id="mediaQueueEntries"><p class="media-queue-empty">Checking the queue…</p></div>
  <div class="media-queue-footer"><button type="button" class="btn" id="mediaClearHistory">Clear old results</button><button type="button" class="btn" id="mediaQueueRefresh">↻ Refresh status</button></div>`;
  document.body.append(queueDialog);
  $('mediaQueueClose').onclick=()=>queueDialog.close();
  queueButton.onclick=banner.onclick=()=>{
    if(queueDialog.open)return;
    queueDialog.showModal();
    refreshJobs();
  };
  $('mediaQueueRefresh').onclick=()=>refreshJobs();
  $('mediaClearHistory').onclick=async()=>{
    if(!confirm('Clear completed, approved, failed and interrupted scan history? Active scans and pending player or VS reviews will be kept.'))return;
    $('mediaClearHistory').disabled=true;
    try{
      const result=await getJson('/api/media/jobs/history',{method:'DELETE'});
      announce('Cleared '+result.cleared+' old scan result'+(result.cleared===1?'':'s')+'.');
      await refreshJobs();
    }catch(err){$('mediaQueueSummary').textContent='Could not clear scan history: '+err.message;}
    finally{$('mediaClearHistory').disabled=false;}
  };
  // All existing native modal dialogs close when their backdrop is tapped.
  document.addEventListener('click',event=>{
    const target=event.target;
    if(!(target instanceof HTMLDialogElement)||!target.open)return;
    const bounds=target.getBoundingClientRect();
    if(event.clientX<bounds.left||event.clientX>bounds.right||
       event.clientY<bounds.top||event.clientY>bounds.bottom)target.close();
  });

  const playerView=$('playerListView');
  const pendingPanel=document.createElement('section');
  pendingPanel.id='mediaPendingPanel';pendingPanel.className='panel pl-section media-review';pendingPanel.hidden=true;
  pendingPanel.innerHTML=`<div class="pl-title-row"><div><div class="eyebrow">Scan review</div><h2>Player changes to review</h2><p>Name-only corrections can save automatically. If a number is flagged, check it against the capture and save the row when ready.</p></div><span id="mediaPendingCount" class="pl-count"></span></div>
    <div class="review-toolbar">
      <label><input type="checkbox" id="mediaSelectAll" aria-label="Select all Player List review rows"> Select all</label>
      <span id="mediaSelectedCount"></span>
      <button class="btn" type="button" id="mediaDismiss">Dismiss selected</button>
      <button class="btn" type="button" id="mediaApproveClean">Save rows without warnings</button>
      <button class="btn primary" type="button" id="mediaApprove">Save selected</button>
    </div>
    <p id="mediaReviewMessage" role="status" aria-live="polite" tabindex="-1" hidden></p>
    <details><summary>Scan details</summary><p id="mediaDiagnostics" hidden></p></details>
    <div id="mediaPendingRows" class="review-grid"></div>`;
  const directory=playerView?.querySelector('[aria-labelledby="plDirectoryTitle"]');
  directory?.before(pendingPanel);
  const masterNames=document.createElement('datalist');masterNames.id='playerMasterNames';document.body.append(masterNames);
  const reviewNames=window.reviewIssues.scanNames(masterNames);
  let autoTail=Promise.resolve();
  function showMediaSuggestions(input){
    const id=Number(input.closest('[data-pending-id]')?.dataset.pendingId);
    const original=pending.find(row=>row.id===id);
    if(original)reviewNames.show(original.job_id);
  }
  pendingPanel.addEventListener('focusin',event=>{
    if(event.target.matches('[data-field="name"]'))showMediaSuggestions(event.target);
  });


  function announce(text){
    banner.hidden=false;
    const label=text+' · View scan queue';
    if(banner.textContent!==label)banner.textContent=label;
  }
  function message(text,error=false){const el=$('mediaMessage');el.hidden=!text;el.textContent=text;el.style.borderColor=error?'#db7284':'#9476b0';}
  function updateTarget(){
    dialog.querySelectorAll('[data-dest]').forEach(btn=>btn.setAttribute('aria-pressed',String(btn.dataset.dest===destination)));
    const isStorm=destination==='storm';
    if(driveSelection&&isStorm&&/\.(mp4|mov|m4v|webm)$/i.test(driveSelection.name)){
      driveSelection=null;
      $('mediaDriveTrashWrap').hidden=true;
      $('mediaFileNames').textContent='No files selected';
      message('Storm Roster only accepts screenshots. Please select an image.',true);
    }
    $('mediaSubmit').disabled=busy||(!driveSelection&&!files.length);
    $('mediaSelectTitle').textContent=isStorm?'Choose Storm Roster screenshots':destination==='vs'?'Choose VS Daily Rank screenshots or a recording':'Choose leaderboard screenshots or an MP4 recording';
    $('mediaSelectHint').textContent=isStorm?'PNG, JPG or WebP images only. MP4 is not allowed for Storm Roster.':'PNG, JPG, WebP, MP4, MOV or WebM. Record the Power or Kills leaderboard while scrolling; pause on each page.';
    if(destination==='vs')$('mediaSelectHint').textContent='Include the selected weekday at the top of Daily Rank. The earned date is detected within your local upload week, ending Sunday, and can be reviewed before saving.';
    fileInput.accept=isStorm?'image/png,image/jpeg,image/webp':"image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,.m4v";
    if(isStorm&&files.some(f=>/\.(mp4|mov|m4v|webm)$/i.test(f.name))){
      files=[];fileInput.value='';$('mediaFileNames').textContent='No files selected';
      $('mediaSubmit').disabled=true;
      message('Storm Roster accepts screenshots only. Choose images again.');
    }
  }
  function openUpload(target){
    const staged=busy||driveSelection||files.length;
    if(!staged&&target)destination=target;
    message(staged&&target&&target!==destination?'Your selected files are still set to '+destinationLabel(destination)+'. Change the destination above to switch.':'');
    updateTarget();
    if(!dialog.open)dialog.showModal();
  }
  topButton.onclick=()=>{
    const view=document.querySelector('[data-workspace-tab][aria-selected="true"]')?.dataset.workspaceTab;
    openUpload(view==='scanner'?'storm':view==='players'||view==='vs'?view:undefined);
  };
  document.querySelectorAll('[data-open-upload]').forEach(button=>{
    button.setAttribute('aria-haspopup','dialog');
    button.setAttribute('aria-controls','mediaDialog');
    button.onclick=()=>openUpload(button.dataset.openUpload);
  });
  $('mediaClose').onclick=()=>dialog.close();
  dialog.querySelectorAll('[data-dest]').forEach(btn=>btn.addEventListener('click',()=>{destination=btn.dataset.dest;updateTarget();}));
  const fileInput=$('mediaFileInput');
  $('mediaDriveButton').onclick=()=>{
    if(busy)return;
    const picker=window.AllianceGoogleDrive;
    if(!picker){message('Google Drive is not available yet. Refresh the page and try again.',true);return;}
    if(!picker.isReady()){
      message(picker.error()||'Google Drive is connecting. Tap Select from Google Drive again shortly.',true);
      picker.prepare().catch(()=>{});
      return;
    }
    if(dialog.open)dialog.close(); // Google Picker must not sit behind a native modal.
    picker.choose({
      destination,
      onSelected:selected=>{
        driveSelection=selected;
        files=[];fileInput.value='';
        $('mediaDriveTrashWrap').hidden=false;
        $('mediaDriveTrash').checked=false;
        $('mediaFileNames').textContent=selected.name+' · Google Drive';
        $('mediaSubmit').disabled=false;
        message('Google Drive file selected. Choose Upload and scan to start.');
        if(!dialog.open)dialog.showModal();
      },
      onCancel:()=>{if(!dialog.open)dialog.showModal();},
      onError:error=>{
        if(!dialog.open)dialog.showModal();
        message(error.message||'Could not open Google Drive.',true);
      }
    });
  };
  $('mediaSelect').onclick=()=>{if(!busy)fileInput.click();};
  $('mediaSelect').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();if(!busy)fileInput.click();}};
  fileInput.onchange=()=>{
    driveSelection=null;
    $('mediaDriveTrashWrap').hidden=true;
    $('mediaDriveTrash').checked=false;
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
    if((!files.length&&!driveSelection)||busy)return;
    busy=true;$('mediaSubmit').disabled=true;message('');setProgress(1,'Preparing upload…');
    try{
      let job;
      if(driveSelection){
        if(Date.now()>driveSelection.expires_at-30000)
          throw Error('Google Drive authorization expired. Select the Drive file again to reconnect.');
        setProgress(5,'Queuing Google Drive file…');
        job=await getJson('/api/media/google-import',{method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({destination,file_id:driveSelection.file_id,
            access_token:driveSelection.access_token,
            move_to_trash:$('mediaDriveTrash').checked,upload_date:localDate()})});
      }else{
      const form=new FormData();files.forEach(f=>form.append('files',f,f.name));
      job=await new Promise((resolve,reject)=>{
        const xhr=new XMLHttpRequest();
        const url='/api/media/upload?destination='+encodeURIComponent(destination)+'&upload_date='+localDate();
        xhr.open('POST',url);xhr.timeout=600000;
        xhr.upload.onprogress=e=>{if(e.lengthComputable)setProgress(Math.min(99,Math.round(e.loaded/e.total*100)),'Uploading: '+Math.round(e.loaded/e.total*100)+'%');};
        xhr.onload=()=>{let payload={};try{payload=JSON.parse(xhr.responseText);}catch{}if(xhr.status<200||xhr.status>=300)reject(Error(payload.detail||'Upload failed: '+xhr.status));else resolve(payload);};
        xhr.onerror=()=>reject(Error('Upload connection failed. Check your connection and retry.'));
        xhr.ontimeout=()=>reject(Error('Upload timed out before the recording was accepted.'));
        xhr.send(form);
      });
      }
      // Close only after the service acknowledges the upload and queues the scan.
      // The server continues processing; status and completion appear outside this dialog.
      files=[];driveSelection=null;fileInput.value='';
      $('mediaDriveTrashWrap').hidden=true;$('mediaDriveTrash').checked=false;
      $('mediaFileNames').textContent='No files selected';
      $('mediaProgress').hidden=true;
      message('');
      announce('✓ '+destinationLabel(destination)+' upload accepted. Scan queued. You can continue using the app.');
      if(dialog.open)dialog.close();
      await refreshJobs();
    }catch(e){message(e.message,true);}
    finally{busy=false;$('mediaSubmit').disabled=!files.length&&!driveSelection;}
  };

  async function getJson(url,options){const r=await fetch(url,{cache:'no-store',...options});let data={};try{data=await r.json();}catch{}if(!r.ok){
      const detail=Array.isArray(data.detail)?data.detail.map(e=>(e.loc||[]).slice(1).join(' ')+' '+e.msg).join('; '):data.detail;
      throw Error(detail||'Request failed ('+r.status+')');
    }return data;}
  let pendingSnapshot,pendingRequest=0;
  async function refreshPending(){
    const request=++pendingRequest;
    try{
      const result=await getJson('/api/media/pending');
      if(request!==pendingRequest)return;
      reviewNames.update(result.updated_by_job);
      if(document.activeElement?.matches('#mediaPendingRows [data-field="name"]'))showMediaSuggestions(document.activeElement);
      const snapshot=JSON.stringify(result.players||[]);
      if(snapshot===pendingSnapshot)return;
      const previous=new Map(pending.map(row=>[row.id,JSON.stringify(row)]));
      const drafts=new Map([...$('mediaPendingRows').querySelectorAll('[data-pending-id]')].map(row=>[
        Number(row.dataset.pendingId),[...row.querySelectorAll('[data-field]')].map(input=>[
          input.dataset.field,input.type==='checkbox'?input.checked:input.value
        ])
      ]));
      pendingSnapshot=snapshot;
      pending=result.players||[];
      pendingPanel.hidden=!pending.length;
      $('mediaPendingCount').textContent=pending.length+' waiting';
      $('mediaPendingRows').innerHTML=pending.map(p=>{
        const flag=p.review_required?(window.reviewIssues.displayWarnings(p.review_reasons||[])||'Verify this row against the capture.'):'';
        const avatar=p.avatar_base64&&/^[A-Za-z0-9+/=]+$/.test(p.avatar_base64)?
          '<img class="pl-avatar" src="data:image/'+(p.avatar_base64.startsWith('UklGR')?'webp':'png')+';base64,'+p.avatar_base64+'" alt="Player avatar">':'♡';
        const evidence=p.evidence_base64 && p.evidence_base64.length<350000 && /^[A-Za-z0-9+/=]+$/.test(p.evidence_base64) ?
          '<details class="media-evidence"><summary>View captured leaderboard row</summary><img loading="lazy" src="data:image/webp;base64,'+
          p.evidence_base64+'" alt="Cropped original leaderboard row for '+escape(p.name||'this player')+'"></details>' : '';
        const requiresCheck=window.reviewIssues.numberNeedsReview(p);
        const numeric=(field,label)=>'<label>'+label+'<input data-field="'+field+'" data-review-number inputmode="'+
          (field==='power_millions'?'decimal':'numeric')+'" value="'+escape(window.reviewIssues.formatNumber(p[field]))+'"></label>';
        const type=p.change_kind==='new'?'New player':p.change_kind==='updated'?'Update':'Needs review';
        const fields=[['power','Power'],['kills','Kills'],['donations','Donations'],
          ['power_millions','Power (millions)'],['hq_level','HQ level']]
          .filter(([field])=>field===p.tab||p[field]!=null||(!p.tab&&['power_millions','hq_level'].includes(field)));
        return '<article class="review-card" data-pending-id="'+p.id+'"><div class="review-card-head">'+
          '<label><input type="checkbox" data-field="include" aria-label="Select '+escape(p.name||'unrecognized player')+'"> Select</label>'+
          '<span class="pl-status '+(type==='New player'?'new':type==='Update'?'active':'removed')+'">'+type+'</span></div>'+
          '<div class="review-fields"><label class="review-name">Player name<input data-field="name" list="playerMasterNames" value="'+escape(p.name||'')+'" maxlength="80"></label>'+
          '<label>Rank<select data-field="rank">'+['Unknown','R5','R4','R3','R2','R1'].map(rank=>'<option value="'+rank+'" '+(p.rank===rank?'selected':'')+'>'+rank+'</option>').join('')+'</select></label>'+
          fields.map(([field,label])=>numeric(field,label)).join('')+'</div>'+
          '<small data-review-warning '+(flag?'':'hidden')+'>'+escape(flag)+'</small>'+
          (p.source_image?'<small class="pl-source-label">'+escape(p.source_image)+'</small>':'')+
          evidence+(requiresCheck?'<label class="review-verify"><input type="checkbox" data-field="number_checked"> I checked the numbers</label>':'')+
          '<p data-review-feedback role="status" hidden></p><div class="review-card-actions">'+avatar+'<button class="btn primary" type="button" data-save-media>Save row</button></div></article>';
      }).join('');
      const originals=new Map(pending.map(row=>[row.id,row]));
      for(const row of $('mediaPendingRows').querySelectorAll('[data-pending-id]')){
        const id=Number(row.dataset.pendingId);
        if(previous.get(id)!==JSON.stringify(originals.get(id)))continue;
        for(const [field,value] of drafts.get(id)||[]){
          const input=row.querySelector('[data-field="'+field+'"]');
          if(input.type==='checkbox')input.checked=value;
          else input.value=value;
        }
        validateMediaRow(row,originals.get(id));
      }
      updateMediaSelection();
      await loadDiagnostics();
      $('mediaBadge').hidden=!pending.length;$('mediaBadge').textContent=String(pending.length);
      try{const master=await getJson('/api/player-list');if(request===pendingRequest){reviewNames.roster(master.players);if(document.activeElement?.matches('#mediaPendingRows [data-field="name"]'))showMediaSuggestions(document.activeElement);}}catch{}
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
  function updateMediaSelection(event){
    const all=$('mediaSelectAll');
    const rows=[...$('mediaPendingRows').querySelectorAll('[data-field="include"]')];
    if(event?.target===all)rows.forEach(row=>{row.checked=all.checked;});
    const checked=rows.filter(row=>row.checked).length;
    all.checked=rows.length>0&&checked===rows.length;
    all.indeterminate=checked>0&&checked<rows.length;
    $('mediaSelectedCount').textContent=checked+' selected';
  }
  pendingPanel.addEventListener('change',event=>{
    if(event.target.matches('#mediaSelectAll,[data-field="include"]'))updateMediaSelection(event);
  });
  function validateMediaRow(row,original,names){
    original??=pending.find(p=>p.id===Number(row.dataset.pendingId));
    if(!original)return;
    const values=Object.fromEntries([...row.querySelectorAll('[data-field]')].map(input=>[input.dataset.field,input.value]));
    names??=reviewNames.names();
    values.number_checked=row.querySelector('[data-field="number_checked"]')?.checked||false;
    const issues=window.reviewIssues(original,values,names,'media');
    const errors=window.reviewIssues.mediaInputIssues(original,values);
    if(window.reviewIssues.numberNeedsReview(original)&&!values.number_checked)errors.push('Check the number against the capture');
    const warning=row.querySelector('[data-review-warning]');
    warning.textContent=window.reviewIssues.displayWarnings([...issues,...errors]);
    warning.hidden=!issues.length&&!errors.length;
    return {row,original,values,issues,errors};
  }
  pendingPanel.addEventListener('input',event=>{
    if(!event.target.matches('[data-review-number]'))return;
    const confirmation=event.target.closest('.review-card')?.querySelector('[data-field="number_checked"]');
    if(confirmation)confirmation.checked=false;
  });
  pendingPanel.addEventListener('focusout',event=>{
    const row=event.target.closest('[data-pending-id]');
    if(!row||!event.target.matches('[data-field]:not([data-field="include"])'))return;
    if(event.target.matches('[data-review-number]'))event.target.value=window.reviewIssues.formatNumber(event.target.value);
    validateMediaRow(row);
    if(event.relatedTarget?.closest('#mediaApprove,#mediaApproveClean,#mediaDismiss,[data-save-media]'))return;
    autoTail=autoTail.then(()=>autoSaveMedia(Number(row.dataset.pendingId)));
  });
  function mediaPayload(original,values){
    const numbers=Object.fromEntries(['power','kills','donations','power_millions','hq_level']
      .map(field=>[field,values[field]===''||values[field]==null?null:window.reviewIssues.numberValue(values[field])]));
    return {id:original.id,name:values.name.trim(),rank:values.rank,number_checked:values.number_checked,...numbers};
  }
  function removeSavedMedia(ids){
    ++pendingRequest;
    pending=pending.filter(row=>!ids.has(row.id));
    pendingSnapshot=JSON.stringify(pending);
    for(const row of $('mediaPendingRows').querySelectorAll('[data-pending-id]')){
      if(ids.has(Number(row.dataset.pendingId)))row.remove();
    }
    pendingPanel.hidden=!pending.length;
    $('mediaPendingCount').textContent=pending.length+' waiting';
    $('mediaBadge').hidden=!pending.length;
    $('mediaBadge').textContent=String(pending.length);
    updateMediaSelection();
  }
  async function autoSaveMedia(id){
    const row=$('mediaPendingRows').querySelector('[data-pending-id="'+id+'"]');
    if(!row||savingReview)return;
    const item=validateMediaRow(row);
    if(!item||window.reviewIssues.numberNeedsReview(item.original)||item.values.name.trim()===(item.original.name||'').trim())return;
    const player=reviewNames.match(item.original.job_id,item.values.name);
    if(!player)return;
    const values={...item.values,name:player.name,rank:item.values.rank==='Unknown'?player.rank:item.values.rank};
    if(window.reviewIssues.mediaInputIssues(item.original,values).length)return;
    try{
      await getJson('/api/media/approve',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({auto_match:true,rows:[mediaPayload(item.original,values)]})});
      reviewNames.mark(item.original.job_id,[player.id]);
      removeSavedMedia(new Set([id]));
      if(document.activeElement?.matches('#mediaPendingRows [data-field="name"]'))showMediaSuggestions(document.activeElement);
      reviewMessage('Saved '+player.name+' to the Player List.');
      window.playerListRefresh?.();
    }catch(error){reviewMessage('Could not save '+player.name+': '+error.message);}
  }
  function reviewMessage(text){
    const status=$('mediaReviewMessage');
    if(pending.length)$('mediaDiagnostics').before(status);
    else pendingPanel.after(status);
    status.hidden=false;
    status.textContent=text;
  }
  let savingReview=false;
  async function approveRows(onlyClean,onlyRow){
    await autoTail;
    if(savingReview||onlyRow&&!onlyRow.isConnected)return;
    const action=onlyRow?.querySelector('[data-save-media]');
    const feedback=onlyRow?.querySelector('[data-review-feedback]');
    const notify=text=>{
      if(feedback?.isConnected){feedback.hidden=false;feedback.textContent=text;}
      reviewMessage(text);
    };
    try{
      const originals=new Map(pending.map(item=>[item.id,item]));
      const names=reviewNames.names();
      const candidates=[...$('mediaPendingRows').querySelectorAll('[data-pending-id]')]
        .filter(row=>onlyRow?row===onlyRow:onlyClean||row.querySelector('[data-field="include"]').checked)
        .map(row=>validateMediaRow(row,originals.get(Number(row.dataset.pendingId)),names)).filter(Boolean);
      const selected=onlyClean?candidates.filter(item=>!item.issues.length&&!item.errors.length):candidates;
      if(!selected.length){
        reviewMessage(onlyClean?'No rows without concerns are ready to add.':'Check at least one row to approve.');
        return;
      }
      for(const item of selected){
        const known=reviewNames.match(item.original.job_id,item.values.name);
        if(known&&item.values.rank==='Unknown'){
          item.values.rank=known.rank;
          item.errors=item.errors.filter(error=>error!=='Choose a rank from R1 to R5');
        }
      }
      const invalid=selected.find(item=>item.errors.length);
      if(invalid){
        const error=(invalid.values.name||'Unnamed row')+': '+invalid.errors.join(' · ')+'.';
        const rowFeedback=invalid.row.querySelector('[data-review-feedback]');
        rowFeedback.hidden=false;rowFeedback.textContent=error;
        notify(error);
        invalid.row.scrollIntoView({block:'nearest'});
        return;
      }
      const rows=selected.map(({original,values})=>mediaPayload(original,values));
      savingReview=true;
      for(const id of ['mediaApprove','mediaApproveClean','mediaDismiss'])$(id).disabled=true;
      if(action)action.disabled=true;
      notify(rows.length===1?'Saving row…':'Saving '+rows.length+' rows…');
      const result=await getJson('/api/media/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rows})});
      removeSavedMedia(new Set(rows.map(row=>row.id)));
      const summary='Added '+result.approved+' rows to the Player List.';
      reviewMessage(summary+(pending.length?' '+pending.length+' rows still awaiting review.':''));
      await Promise.all([refreshPending(),window.playerListRefresh?.()]);
    }catch(error){
      notify('Could not save: '+error.message);
    }finally{
      if(action)action.disabled=false;
      savingReview=false;
      for(const id of ['mediaApprove','mediaApproveClean','mediaDismiss'])$(id).disabled=false;
    }
  }
  pendingPanel.addEventListener('click',event=>{
    const action=event.target.closest('[data-save-media]');
    if(action)approveRows(false,action.closest('[data-pending-id]'));
  });
  $('mediaApprove').onclick=()=>approveRows(false);
  $('mediaApproveClean').onclick=()=>approveRows(true);
  $('mediaDismiss').onclick=async()=>{
    await autoTail;
    if(savingReview)return;
    const ids=[...$('mediaPendingRows').querySelectorAll('[data-pending-id]')]
      .filter(tr=>tr.querySelector('[data-field="include"]').checked)
      .map(tr=>Number(tr.dataset.pendingId));
    if(!ids.length){announce('Check the entries you want to dismiss.');return;}
    if(!confirm('Permanently dismiss '+ids.length+' checked OCR entries? They will not update your player database.'))return;
    savingReview=true;
    for(const id of ['mediaApprove','mediaApproveClean','mediaDismiss'])$(id).disabled=true;
    try{
      const result=await getJson('/api/media/dismiss',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
      announce('Dismissed '+result.dismissed+' unwanted OCR entries.');await refreshPending();
    }catch(e){announce('Could not dismiss entries: '+e.message);}
    finally{savingReview=false;for(const id of ['mediaApprove','mediaApproveClean','mediaDismiss'])$(id).disabled=false;}
  };
  function processingTime(start,end=Date.now()){
    const seconds=Math.max(0,Math.floor((end-start)/1000));
    return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');
  }
  function tickScanTimers(){
    for(const timer of $('mediaQueueEntries').querySelectorAll('[data-scan-start]')){
      timer.textContent='Processing '+processingTime(Number(timer.dataset.scanStart));
    }
  }
  function renderScanQueue(jobs){
    const active=jobs.filter(job=>job.status==='scanning')
      .sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)));
    const waiting=jobs.filter(job=>job.status==='queued')
      .sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)));
    const history=jobs.filter(job=>!['queued','scanning'].includes(job.status))
      .sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));
    $('mediaQueueSummary').textContent=active.length+' scanning · '+waiting.length+' waiting · '+history.length+' recent results';
    const label=job=>destinationLabel(job.destination);
    const statuses={scanning:'Scanning',queued:'Waiting',review:'Awaiting player review',
      ready:'Completed',approved:'Approved',error:'Failed',interrupted:'Interrupted'};
    function item(job,queueIndex){
      const pct=Math.min(100,Math.max(0,Number(job.progress)||0));
      const date=new Date(job.created_at);
      const time=Number.isNaN(date.getTime())?'':date.toLocaleString();
      const started=Date.parse(job.started_at);
      const finished=Date.parse(job.finished_at);
      let timer='';
      if(Number.isFinite(started)){
        if(job.status==='scanning')timer='<small data-scan-start="'+started+'">Processing '+processingTime(started)+'</small>';
        else if(Number.isFinite(finished))timer='<small>Processing time '+processingTime(started,finished)+'</small>';
      }
      const statusText=statuses[job.status]||job.status;
      const position=queueIndex!=null?' · Queue position '+(queueIndex+1):'';
      const count=Number(job.result_count)||0;
      const countText=['ready','review','approved'].includes(job.status)?' · '+count+' results':'';
      return '<li class="media-queue-entry"><div class="media-queue-line"><strong>'+
        escape(label(job))+'</strong><span class="media-queue-state">'+escape(statusText+position+countText)+
        '</span></div><small>'+escape(time)+'</small>'+timer+
        (['queued','scanning'].includes(job.status)?'<progress max="100" value="'+pct+
        '" aria-label="'+escape(label(job)+' scanning progress')+'"></progress><small>'+pct+'% completed</small>':'')+
        '<div class="media-queue-message">'+escape(job.message||'No further details yet.')+'</div></li>';
    }
    const section=(heading,list,queue=false)=>list.length?
      '<section class="media-queue-section"><h3>'+heading+'</h3><ol class="media-queue-list">'+
      list.map((job,i)=>item(job,queue?i:null)).join('')+'</ol></section>':'';
    $('mediaQueueEntries').innerHTML=
      section('Currently scanning',active)+section('Waiting in queue',waiting,true)+
      section('Recent results',history.slice(0,15))+
      (!jobs.length?'<p class="media-queue-empty">No scans have been uploaded yet.</p>':'');
  }
  async function refreshJobs(){
    try{
      const {jobs=[]}=await getJson('/api/media/jobs');
      if(queueDialog.open)renderScanQueue(jobs);
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
            announce('⚠ '+destinationLabel(job.destination)+
              ' scan did not finish: '+(job.message||'Server processing was interrupted. Please retry.'));
          }else if(job.destination==='storm'){
            await window.loadServerStormRoster?.();
            announce('✓ Storm Roster scanned! '+job.result_count+' players loaded into Roster Scanner.');
          }else if(job.destination==='vs'){
            await window.vsPointsRefresh?.();
            announce('✓ '+(job.message||'VS points are ready to review in VS Points.'));
          }else{
            await window.playerListRefresh?.();
            await refreshPending();
            announce('✓ '+(job.message||'Player List scan finished.'));
          }
        }
      }
      try{localStorage.setItem(NOTIFICATIONS,JSON.stringify([...notified].slice(-100)));}catch{}
      const active=jobs.filter(j=>j.status==='scanning')
        .sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)));
      const waiting=jobs.filter(j=>j.status==='queued')
        .sort((a,b)=>String(a.created_at).localeCompare(String(b.created_at)));
      const queuedCount=waiting.length;
      const queuedText=queuedCount?(' · '+queuedCount+' scan job'+(queuedCount===1?'':'s')+' waiting'):' · No jobs waiting';
      if(active.length){
        const j=active[0];
        announce('⌛ '+destinationLabel(j.destination)+
          ' · '+(Number(j.progress)||0)+'% · '+(j.message||'Scanning…')+queuedText);
      }else if(waiting.length){
        const j=waiting[0];
        announce('⌛ '+destinationLabel(j.destination)+
          ' waiting to scan · '+queuedCount+' scan job'+(queuedCount===1?'':'s')+' waiting');
      }else if(!newAlerts.length){
        const reviews=jobs.filter(j=>j.status==='review').length;
        if(reviews)announce(reviews+' scan result'+(reviews===1?'':'s')+' awaiting review');
        else banner.hidden=true;
      }
    }catch(e){
      console.warn('Media jobs temporarily unavailable:',e.message);
      if(queueDialog.open)$('mediaQueueSummary').textContent='Unable to refresh. Check your connection and try again.';
    }
  }
  window.refreshMediaUploads=()=>Promise.all([refreshJobs(),refreshPending()]);
  window.addEventListener('focus',()=>refreshJobs());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)window.refreshMediaUploads();});
  setInterval(refreshJobs,8000);
  setInterval(()=>{if(queueDialog.open)tickScanTimers();},1000);
  window.loadServerStormRoster?.();
  window.refreshMediaUploads();
  window.addEventListener('player-profile-updated',async()=>{
    try{const data=await getJson('/api/player-list');reviewNames.roster(data.players);}catch{}
  });
})();

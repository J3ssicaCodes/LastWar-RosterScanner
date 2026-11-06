/* VS daily scores, with an explicit date and review before saving. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const style=document.createElement('style');
  style.textContent='#vsRows .vs-unmatched{color:var(--red);color:color-mix(in srgb,var(--red) 80%,var(--text))}';
  document.head.append(style);
  let saved=[],pending=[],days=[],weekRequest=0;
  const localDate=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),
    String(d.getDate()).padStart(2,'0')].join('-');
  let selectedWeek=weekBounds(localDate(new Date()))[0];
  const shiftDate=(date,offset)=>{
    const value=new Date(date+'T12:00:00');value.setDate(value.getDate()+offset);return localDate(value);
  };
  const displayDate=date=>new Date(date+'T12:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});
  const sortScores=window.allianceTableSort($('vsWeekColumns'),null,-1,render);
  const calendar=$('vsWeekCalendar');
  function setWeek(day){
    selectedWeek=weekBounds(day)[0];
    load().catch(error=>{$('vsMessage').textContent=error.message;});
  }
  async function api(path,options){
    const r=await fetch(path,{cache:'no-store',...options});const data=await r.json();
    if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Check the selected names, dates and points.');return data;
  }
  function render(){
    const query=$('vsSearch').value.trim().toLocaleLowerCase();
    const players=new Map();
    for(const row of saved){
      if(!players.has(row.player_key))players.set(row.player_key,{name:row.name,rosterId:row.roster_id,scores:new Map()});
      const player=players.get(row.player_key);
      player.rosterId??=row.roster_id;
      if(!player.scores.has(row.score_date))player.scores.set(row.score_date,BigInt(row.points));
    }
    const rows=sortScores([...players.values()]
      .filter(row=>row.name.toLocaleLowerCase().includes(query))
      .map(row=>({...row,total:[...row.scores.values()].reduce((sum,value)=>sum+value,0n)})),
      (row,key)=>key==null?row.total:key==='name'||key==='total'?row[key]:row.scores.get(days[Number(key)]));
    $('vsRows').innerHTML=rows.map(row=>'<tr><th scope="row">'+(row.rosterId?
      '<button type="button" class="player-profile-link" data-open-player-profile="'+Number(row.rosterId)+'">'+esc(row.name)+'</button>':
      '<span class="vs-unmatched" title="Not on the Player List">'+esc(row.name)+'<span class="sr-only"> (not on the Player List)</span></span>')+'</th>'+
      days.map(day=>'<td>'+(row.scores.has(day)?row.scores.get(day).toLocaleString():'—')+'</td>').join('')+
      '<td>'+row.total.toLocaleString()+'</td></tr>').join('')||'<tr><td colspan="8">No scores for this week.</td></tr>';
    const unmatched=rows.filter(row=>!row.rosterId).length;
    $('vsSummary').textContent=rows.length+' players · '+rows.reduce((sum,row)=>sum+row.total,0n).toLocaleString()+' points shown'+
      (unmatched?' · '+unmatched+' unmatched '+(unmatched===1?'name':'names')+' in red':'');
    $('vsExport').href='/api/vs-points/export.csv?week_start='+encodeURIComponent(selectedWeek);
  }
  async function load(){
    const chosen=selectedWeek;
    $('vsWeekLabel').textContent='Week of '+displayDate(chosen)+' to '+displayDate(shiftDate(chosen,6));
    const request=++weekRequest;
    const data=await api('/api/vs-points/weekly?week_start='+encodeURIComponent(chosen));
    if(request!==weekRequest)return;
    saved=data.rows||[];
    days=data.days||[];
    render();
  }
  function weekBounds(upload){
    const d=new Date(upload+'T12:00:00');const sunday=new Date(d);sunday.setDate(d.getDate()+(7-d.getDay())%7);
    const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
    const monday=new Date(sunday);monday.setDate(sunday.getDate()-6);
    const sat=new Date(sunday);sat.setDate(sunday.getDate()-1);return [iso(monday),iso(sat)<upload?iso(sat):upload];
  }
  const reviewNames=window.reviewIssues.scanNames($('vsPlayerNames'));
  let autoTail=Promise.resolve();
  $('vsReviewJobs').addEventListener('focusin',event=>{
    if(event.target.matches('[data-vs-name]'))reviewNames.show(event.target.closest('[data-vs-job]').dataset.vsJob);
  });
  function refreshFocusedSuggestions(){
    const focused=document.activeElement;
    if(focused?.matches('#vsReviewJobs [data-vs-name]'))reviewNames.show(focused.closest('[data-vs-job]').dataset.vsJob);
  }
  let pendingRequest=0;
  let pendingSnapshot;
  async function loadPending(){
    const request=++pendingRequest;
    const data=await api('/api/vs-points/pending');
    if(request!==pendingRequest)return;
    reviewNames.update(data.updated_by_job);
    refreshFocusedSuggestions();
    const snapshot=JSON.stringify(data.rows||[]);
    if(snapshot===pendingSnapshot)return;
    const drafts=new Map([...$('vsReviewJobs').querySelectorAll('[data-vs-row]')].map(row=>[
      Number(row.dataset.vsRow),[...row.querySelectorAll('input')].map(input=>[
        input.getAttributeNames().find(name=>name.startsWith('data-vs-')),
        input.type==='checkbox'?input.checked:input.value
      ])
    ]));
    const dates=new Map([...$('vsReviewJobs').querySelectorAll('[data-vs-job]')].map(group=>[
      group.dataset.vsJob,group.querySelector('[data-vs-date]').value
    ]));
    pendingSnapshot=snapshot;
    pending=data.rows||[];
    $('vsPending').hidden=!pending.length;
    const groups=new Map();for(const row of pending){if(!groups.has(row.job_id))groups.set(row.job_id,[]);groups.get(row.job_id).push(row);}
    $('vsReviewJobs').innerHTML=[...groups].map(([job,rows])=>{
      const first=rows[0],[min,max]=weekBounds(first.upload_date);
      return '<section class="panel" style="padding:16px;margin:14px 0" data-vs-job="'+esc(job)+'"><h4>'+esc(first.weekday)+' · '+rows.length+' scores to review</h4>'+
        '<label>Score date <input data-vs-date type="date" value="'+esc(dates.get(job)||first.score_date)+'" min="'+min+'" max="'+max+'"></label>'+
        '<div class="review-toolbar"><label><input type="checkbox" data-vs-select-all aria-label="Select all scores in this batch"> Select all</label>'+
        '<span data-vs-selected></span><button class="btn" data-vs-select-clear type="button">Select clear rows</button><button class="btn" data-vs-dismiss type="button">Dismiss selected</button><button class="btn primary" data-vs-approve type="button">Save selected</button></div><p data-vs-feedback role="status" hidden></p>'+
        '<div class="review-grid">'+rows.map(r=>
          '<article class="review-card" data-vs-row="'+r.id+'"><div class="review-card-head"><label><input type="checkbox" data-vs-use aria-label="Select score for '+esc(r.name)+'"> Select</label></div>'+
          '<div class="review-fields"><label class="review-name">Player name<input data-vs-name list="vsPlayerNames" maxlength="80" value="'+esc(r.name)+'"></label>'+
          '<label>VS points<input data-vs-points inputmode="numeric" value="'+esc(window.reviewIssues.formatNumber(r.points))+'"></label></div>'+
          '<small data-review-warning '+(r.review_reasons?.length?'':'hidden')+'>'+esc(window.reviewIssues.displayWarnings(r.review_reasons||[]))+'</small>'+
          (r.evidence_base64?'<details><summary>View captured row</summary><img loading="lazy" src="data:image/webp;base64,'+r.evidence_base64+'" alt="Captured score row"></details>':'')+
          (window.reviewIssues.numberNeedsReview(r)?'<label class="review-verify"><input type="checkbox" data-vs-number-checked> I checked the number</label>':'')+
          '<p data-vs-row-feedback role="status" hidden></p><div class="review-card-actions"><button class="btn primary" type="button" data-vs-save-row>Save row</button></div></article>'
        ).join('')+'</div></section>';
    }).join('');
    for(const row of $('vsReviewJobs').querySelectorAll('[data-vs-row]')){
      for(const [attr,value] of drafts.get(Number(row.dataset.vsRow))||[]){
        if(!attr)continue;
        const input=row.querySelector('['+attr+']');
        if(!input)continue;
        if(input.type==='checkbox')input.checked=value;
        else input.value=value;
      }
    }
    for(const group of $('vsReviewJobs').querySelectorAll('[data-vs-job]'))updateVSSelection(group);
    try{const players=await api('/api/player-list');if(request===pendingRequest){reviewNames.roster(players.players);refreshFocusedSuggestions();}}catch{}
  }
  function updateVSSelection(group,target){
    const all=group.querySelector('[data-vs-select-all]');
    const rows=[...group.querySelectorAll('[data-vs-use]')];
    if(target===all)rows.forEach(row=>{row.checked=all.checked;});
    const checked=rows.filter(row=>row.checked).length;
    all.checked=rows.length>0&&checked===rows.length;
    all.indeterminate=checked>0&&checked<rows.length;
    group.querySelector('[data-vs-selected]').textContent=checked+' selected';
  }
  $('vsReviewJobs').addEventListener('change',event=>{
    if(event.target.matches('[data-vs-select-all],[data-vs-use]'))updateVSSelection(event.target.closest('[data-vs-job]'),event.target);
  });
  $('vsReviewJobs').addEventListener('input',event=>{
    if(!event.target.matches('[data-vs-points]'))return;
    const confirmation=event.target.closest('.review-card')?.querySelector('[data-vs-number-checked]');
    if(confirmation)confirmation.checked=false;
  });
  function validateVSGroup(group){
    const rows=[...group.querySelectorAll('[data-vs-row]')];
    const names=reviewNames.names();
    rows.forEach(row=>{
      const original=pending.find(p=>p.id===Number(row.dataset.vsRow));
      const values={name:row.querySelector('[data-vs-name]').value.trim(),points:row.querySelector('[data-vs-points]').value};
      const issues=window.reviewIssues(original,values,names,'vs');
      const warning=row.querySelector('[data-review-warning]');
      warning.textContent=window.reviewIssues.displayWarnings(issues);warning.hidden=!issues.length;
      row.dataset.reviewInvalid=issues.length?'true':'false';
    });
  }
  $('vsReviewJobs').addEventListener('focusout',event=>{
    const group=event.target.closest('[data-vs-job]');
    const row=event.target.closest('[data-vs-row]');
    if(!group||!row||!event.target.matches('[data-vs-name],[data-vs-points]'))return;
    if(event.target.matches('[data-vs-points]'))event.target.value=window.reviewIssues.formatNumber(event.target.value);
    validateVSGroup(group);
    if(event.relatedTarget?.closest('[data-vs-approve],[data-vs-dismiss],[data-vs-save-row]'))return;
    autoTail=autoTail.then(()=>autoSaveVS(Number(row.dataset.vsRow)));
  });
  async function autoSaveVS(id){
    const row=$('vsReviewJobs').querySelector('[data-vs-row="'+id+'"]');
    const original=pending.find(item=>item.id===id);
    if(!row||!original||window.reviewIssues.numberNeedsReview(original))return;
    const group=row.closest('[data-vs-job]');
    if(group.querySelector('button:disabled'))return;
    const name=row.querySelector('[data-vs-name]').value.trim();
    if(name===(original.name||'').trim())return;
    const player=reviewNames.match(original.job_id,name);
    const points=row.querySelector('[data-vs-points]');
    const date=group.querySelector('[data-vs-date]');
    if(!player||!window.reviewIssues.numberValid(points.value,'points')||
       !date.value||!date.validity.valid)return;
    try{
      await api('/api/vs-points/approve',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({auto_match:true,job_id:original.job_id,score_date:date.value,
          rows:[{id,name:player.name,points:window.reviewIssues.numberValue(points.value)}]})});
      reviewNames.mark(original.job_id,[player.id]);
      ++pendingRequest;
      pending=pending.filter(item=>item.id!==id);
      pendingSnapshot=JSON.stringify(pending);
      $('vsReviewJobs').querySelector('[data-vs-row="'+id+'"]')?.remove();
      const currentGroup=[...$('vsReviewJobs').querySelectorAll('[data-vs-job]')].find(node=>node.dataset.vsJob===original.job_id);
      if(currentGroup){
        const remaining=currentGroup.querySelectorAll('[data-vs-row]').length;
        if(!remaining)currentGroup.remove();
        else{currentGroup.querySelector('h4').textContent=original.weekday+' · '+remaining+' scores to review';updateVSSelection(currentGroup);}
      }
      $('vsPending').hidden=!pending.length;
      refreshFocusedSuggestions();
      $('vsMessage').textContent='Saved '+player.name+'’s VS score for '+date.value+'.';
      await load();
    }catch(err){$('vsMessage').textContent=err.message;}
  }
  $('vsReviewJobs').addEventListener('click',async event=>{
    const clear=event.target.closest('[data-vs-select-clear]');
    if(clear){
      const group=clear.closest('[data-vs-job]');
      const candidates=[...group.querySelectorAll('[data-vs-row]')].map(row=>{
        const original=pending.find(item=>item.id===Number(row.dataset.vsRow));
        const name=row.querySelector('[data-vs-name]').value.trim();
        const points=row.querySelector('[data-vs-points]').value;
        const player=reviewNames.match(group.dataset.vsJob,name);
        const eligible=original&&player&&!original.review_reasons?.length&&name===original.name&&
          window.reviewIssues.numberValid(points,'points')&&window.reviewIssues.numberValue(points)===original.points;
        return {row,player,eligible};
      });
      const counts=new Map();
      for(const item of candidates)if(item.eligible)counts.set(item.player.id,1+(counts.get(item.player.id)||0));
      for(const item of candidates)item.row.querySelector('[data-vs-use]').checked=item.eligible&&counts.get(item.player.id)===1;
      updateVSSelection(group);
      const feedback=group.querySelector('[data-vs-feedback]');
      const selected=[...counts.values()].some(count=>count===1);
      feedback.hidden=selected;
      feedback.textContent=selected?'':'No unedited scores without warnings match the Player List in this batch.';
      return;
    }
    const action=event.target.closest('[data-vs-approve],[data-vs-dismiss],[data-vs-save-row]');if(!action)return;
    await autoTail;
    if(!action.isConnected||action.disabled)return;
    const group=action.closest('[data-vs-job]');const rows=[...group.querySelectorAll('[data-vs-row]')];
    const feedback=action.closest('[data-vs-row]')?.querySelector('[data-vs-row-feedback]')||group.querySelector('[data-vs-feedback]');
    const report=text=>{feedback.hidden=false;feedback.textContent=text;$('vsMessage').textContent=text;};
    action.disabled=true;
    try{
      if(action.hasAttribute('data-vs-dismiss')){
        const ids=rows.filter(r=>r.querySelector('[data-vs-use]').checked).map(r=>Number(r.dataset.vsRow));
        if(!ids.length)throw Error('Check the rows you want to dismiss.');
        if(!confirm('Dismiss '+ids.length+' checked VS scores?'))return;
        await api('/api/vs-points/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
      }else{
        validateVSGroup(group);
        const chosen=action.hasAttribute('data-vs-save-row')?[action.closest('[data-vs-row]')]:
          rows.filter(row=>row.querySelector('[data-vs-use]').checked);
        const selected=chosen.map(row=>({
          id:Number(row.dataset.vsRow),name:row.querySelector('[data-vs-name]').value.trim(),
          points:row.querySelector('[data-vs-points]').value,
          number_checked:row.querySelector('[data-vs-number-checked]')?.checked||false
        }));
        if(!selected.length)throw Error('Select at least one score.');
        if(selected.some(r=>!window.reviewIssues.numberValid(r.points,'points')||r.name.length<2))
          throw Error('Check names and enter whole-number points for every selected row.');
        const unverified=selected.find(row=>!row.number_checked&&window.reviewIssues.numberNeedsReview(pending.find(p=>p.id===row.id)));
        if(unverified)throw Error('Compare '+unverified.name+'’s score with the capture and mark it checked.');
        const score_date=group.querySelector('[data-vs-date]').value;
        await api('/api/vs-points/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:group.dataset.vsJob,score_date,rows:selected.map(r=>({...r,points:window.reviewIssues.numberValue(r.points)}))})});
        // Show the week that contains the scores just saved.
        selectedWeek=weekBounds(score_date)[0];
        $('vsMessage').textContent='Saved '+selected.length+' scores for '+score_date+'. Existing scores for those players on that date were updated.';
      }
      await refresh();window.refreshMediaUploads?.();
    }catch(err){report(err.message);}
    finally{action.disabled=false;}
  });
  async function refresh(){try{await Promise.all([load(),loadPending()]);}catch(err){$('vsMessage').textContent='Could not refresh VS Points: '+err.message;}}
  window.vsPointsRefresh=refresh;
  $('vsRefresh').onclick=refresh;$('vsSearch').oninput=render;
  $('vsPrevious').onclick=()=>setWeek(shiftDate(selectedWeek,-7));
  $('vsNext').onclick=()=>setWeek(shiftDate(selectedWeek,7));
  $('vsWeekLabel').onclick=()=>{
    const date=$('vsCalendarDate');date.value=selectedWeek;calendar.showModal();
    try{date.showPicker?.();}catch{}
  };
  $('vsCalendarCancel').onclick=()=>calendar.close();
  $('vsCalendarDate').onchange=event=>{
    if(!event.target.value)return;
    setWeek(event.target.value);calendar.close();
  };
  calendar.querySelector('form').onsubmit=()=>setWeek($('vsCalendarDate').value);
  calendar.addEventListener('click',event=>{
    if(event.target===calendar)calendar.close();
  });
  document.getElementById('vsPointsTab').addEventListener('click',refresh);
  window.addEventListener('focus',refresh);refresh();
  window.addEventListener('player-profile-updated',async()=>{
    try{const data=await api('/api/player-list');reviewNames.roster(data.players);refreshFocusedSuggestions();}catch{}
  });
})();

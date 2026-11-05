/* VS daily scores, with an explicit date and review before saving. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let saved=[],pending=[],days=[],weekRequest=0;
  const weekdays=['Mon','Tue','Wed','Thu','Fri','Sat'];
  const localDate=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),
    String(d.getDate()).padStart(2,'0')].join('-');
  async function api(path,options){
    const r=await fetch(path,{cache:'no-store',...options});const data=await r.json();
    if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Check the selected names, dates and points.');return data;
  }
  function render(){
    const query=$('vsSearch').value.trim().toLocaleLowerCase();
    const players=new Map();
    for(const row of saved){
      if(!players.has(row.player_key))players.set(row.player_key,{name:row.name,scores:new Map()});
      const player=players.get(row.player_key);
      if(!player.scores.has(row.score_date))player.scores.set(row.score_date,BigInt(row.points));
    }
    const rows=[...players.values()]
      .filter(row=>row.name.toLocaleLowerCase().includes(query))
      .map(row=>({...row,total:[...row.scores.values()].reduce((sum,value)=>sum+value,0n)}))
      .sort((a,b)=>a.total===b.total?a.name.localeCompare(b.name):a.total>b.total?-1:1);
    $('vsWeekColumns').innerHTML='<th scope="col">Player</th>'+days.map((day,i)=>
      '<th scope="col">'+weekdays[i]+' '+esc(day.slice(5))+'</th>').join('')+'<th scope="col">Total</th>';
    $('vsRows').innerHTML=rows.map(row=>'<tr><th scope="row">'+esc(row.name)+'</th>'+
      days.map(day=>'<td>'+(row.scores.has(day)?row.scores.get(day).toLocaleString():'—')+'</td>').join('')+
      '<td>'+row.total.toLocaleString()+'</td></tr>').join('')||'<tr><td colspan="8">No scores for this week.</td></tr>';
    $('vsSummary').textContent=rows.length+' players · '+rows.reduce((sum,row)=>sum+row.total,0n).toLocaleString()+' points shown';
    $('vsExport').href='/api/vs-points/export.csv?week_start='+encodeURIComponent($('vsDate').value);
  }
  async function load(){
    const currentWeek=weekBounds(localDate(new Date()))[0];
    const chosen=$('vsDate').value||currentWeek;
    const request=++weekRequest;
    const data=await api('/api/vs-points/weekly?week_start='+encodeURIComponent(chosen));
    if(request!==weekRequest)return;
    saved=data.rows||[];
    days=data.days||[];
    const weeks=[...new Set([...data.weeks,currentWeek])].sort().reverse();
    $('vsDate').innerHTML=weeks.map(week=>'<option value="'+esc(week)+'">'+
      (week===currentWeek?'Current week · ':'Week of ')+esc(week)+'</option>').join('');
    $('vsDate').value=data.week_start;
    render();
  }
  function weekBounds(upload){
    const d=new Date(upload+'T12:00:00');const sunday=new Date(d);sunday.setDate(d.getDate()+(7-d.getDay())%7);
    const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
    const monday=new Date(sunday);monday.setDate(sunday.getDate()-6);
    const sat=new Date(sunday);sat.setDate(sunday.getDate()-1);return [iso(monday),iso(sat)<upload?iso(sat):upload];
  }
  let pendingSnapshot;
  async function loadPending(){
    const data=await api('/api/vs-points/pending');
    const snapshot=JSON.stringify(data.rows||[]);
    if(snapshot===pendingSnapshot)return;
    pendingSnapshot=snapshot;
    pending=data.rows||[];
    $('vsPending').hidden=!pending.length;
    const groups=new Map();for(const row of pending){if(!groups.has(row.job_id))groups.set(row.job_id,[]);groups.get(row.job_id).push(row);}
    $('vsReviewJobs').innerHTML=[...groups].map(([job,rows])=>{
      const first=rows[0],[min,max]=weekBounds(first.upload_date);
      return '<section class="panel" style="padding:16px;margin:14px 0" data-vs-job="'+esc(job)+'"><h4>'+esc(first.weekday)+' selected · '+rows.length+' scores</h4><label>Points earned on <input data-vs-date type="date" value="'+esc(first.score_date)+'" min="'+min+'" max="'+max+'"></label><p>Uploaded '+esc(first.upload_date)+' · Review names and points against the captured row.</p><div class="pl-table-wrap"><table class="pl-table"><thead><tr><th><label><input type="checkbox" data-vs-select-all checked aria-label="Select all scores in this batch"> Use</label></th><th>Player</th><th>Points</th><th>Captured row</th></tr></thead><tbody>'+rows.map(r=>
        '<tr data-vs-row="'+r.id+'"><td><input type="checkbox" data-vs-use checked aria-label="Include score"></td><td><input data-vs-name list="vsPlayerNames" maxlength="80" aria-label="Player name" value="'+esc(r.name)+'">'+'<small class="media-flag" data-review-warning '+(r.review_reasons?.length?'':'hidden')+'>'+esc((r.review_reasons||[]).join(' · '))+'</small>'+'</td><td><input data-vs-points type="number" min="0" step="1" aria-label="VS points" value="'+(r.points??'')+'"></td><td>'+(r.evidence_base64?'<details><summary>View row</summary><img style="max-width:550px;width:min(70vw,550px)" src="data:image/webp;base64,'+r.evidence_base64+'" alt="Captured score row"></details>':'')+'</td></tr>').join('')+'</tbody></table></div><div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px"><button class="btn primary" data-vs-approve type="button">Save checked scores</button><button class="btn" data-vs-dismiss type="button">Dismiss checked</button></div></section>';
    }).join('');
    try{const players=await api('/api/player-list');$('vsPlayerNames').innerHTML=players.players.map(p=>'<option value="'+esc(p.name)+'">').join('');}catch{}
  }
  $('vsReviewJobs').addEventListener('change',event=>{
    if(!event.target.matches('[data-vs-select-all],[data-vs-use]'))return;
    const group=event.target.closest('[data-vs-job]');
    const all=group.querySelector('[data-vs-select-all]');
    const rows=[...group.querySelectorAll('[data-vs-use]')];
    if(event.target===all)rows.forEach(row=>{row.checked=all.checked;});
    const checked=rows.filter(row=>row.checked).length;
    all.checked=checked===rows.length;
    all.indeterminate=checked>0&&checked<rows.length;
  });
  function validateVSGroup(group){
    const rows=[...group.querySelectorAll('[data-vs-row]')];
    const names=[...$('vsPlayerNames').options].map(option=>option.value);
    rows.forEach((row,i)=>{
      const original=pending.find(p=>p.id===Number(row.dataset.vsRow));
      const values={name:row.querySelector('[data-vs-name]').value.trim(),points:row.querySelector('[data-vs-points]').value};
      const issues=window.reviewIssues(original,values,names,'vs',rows[i-1]?.querySelector('[data-vs-points]').value,rows[i+1]?.querySelector('[data-vs-points]').value);
      const warning=row.querySelector('[data-review-warning]');
      warning.textContent=issues.join(' · ');warning.hidden=!issues.length;
      row.dataset.reviewInvalid=issues.length?'true':'false';
    });
  }
  $('vsReviewJobs').addEventListener('focusout',event=>{
    const group=event.target.closest('[data-vs-job]');
    if(group&&event.target.matches('[data-vs-name],[data-vs-points]'))validateVSGroup(group);
  });
  $('vsReviewJobs').addEventListener('click',async event=>{
    const action=event.target.closest('[data-vs-approve],[data-vs-dismiss]');if(!action)return;
    const group=action.closest('[data-vs-job]');const rows=[...group.querySelectorAll('[data-vs-row]')];
    action.disabled=true;
    try{
      if(action.hasAttribute('data-vs-dismiss')){
        const ids=rows.filter(r=>r.querySelector('[data-vs-use]').checked).map(r=>Number(r.dataset.vsRow));
        if(!ids.length)throw Error('Check the rows you want to dismiss.');
        if(!confirm('Dismiss '+ids.length+' checked VS scores?'))return;
        await api('/api/vs-points/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
      }else{
        validateVSGroup(group);
        if(rows.some(row=>row.querySelector('[data-vs-use]').checked&&row.dataset.reviewInvalid==='true')){
          throw Error('Correct the remaining warnings before saving the checked rows.');
        }
        const selected=rows.filter(r=>r.querySelector('[data-vs-use]').checked).map(r=>({id:Number(r.dataset.vsRow),name:r.querySelector('[data-vs-name]').value.trim(),points:r.querySelector('[data-vs-points]').value}));
        if(!selected.length)throw Error('Select at least one score.');
        if(selected.some(r=>r.points===''||!Number.isSafeInteger(Number(r.points))||Number(r.points)<0||r.name.length<2))throw Error('Check names and enter whole-number points for every selected row.');
        const score_date=group.querySelector('[data-vs-date]').value;
        await api('/api/vs-points/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:group.dataset.vsJob,score_date,rows:selected.map(r=>({...r,points:Number(r.points)}))})});
        // Show the week that contains the scores just saved.
        const week=weekBounds(score_date)[0];
        $('vsDate').innerHTML='<option value="'+esc(week)+'">'+esc(week)+'</option>';
        $('vsMessage').textContent='Saved '+selected.length+' scores for '+score_date+'. Existing scores for those players on that date were updated.';
      }
      await refresh();window.refreshMediaUploads?.();
    }catch(err){$('vsMessage').textContent=err.message;}
    finally{action.disabled=false;}
  });
  async function refresh(){try{await Promise.all([load(),loadPending()]);}catch(err){$('vsMessage').textContent='Could not refresh VS Points: '+err.message;}}
  window.vsPointsRefresh=refresh;
  $('vsRefresh').onclick=refresh;$('vsDate').onchange=load;$('vsSearch').oninput=render;
  document.getElementById('vsPointsTab').addEventListener('click',refresh);
  window.addEventListener('focus',refresh);refresh();
  window.addEventListener('player-profile-updated',async()=>{
    try{const data=await api('/api/player-list');document.getElementById('vsPlayerNames').innerHTML=data.players.map(p=>'<option value="'+esc(p.name)+'">').join('');}catch{}
  });
})();

/* VS daily scores, with an explicit date and review before saving. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let saved=[],pending=[];
  const number=n=>Number(n).toLocaleString();
  async function api(path,options){
    const r=await fetch(path,{cache:'no-store',...options});const data=await r.json();
    if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Check the selected names, dates and points.');return data;
  }
  function render(){
    const query=$('vsSearch').value.trim().toLocaleLowerCase();
    const rows=saved.filter(r=>r.name.toLocaleLowerCase().includes(query));
    $('vsRows').innerHTML=rows.map(r=>'<tr><td>'+esc(r.score_date)+'</td><td>'+esc(r.name)+'</td><td>'+number(r.points)+'</td></tr>').join('')||'<tr><td colspan="3">No scores for this date.</td></tr>';
    $('vsSummary').textContent=saved.length+' players · '+number(saved.reduce((n,r)=>n+Number(r.points),0))+' total points';
    $('vsExport').href='/api/vs-points/export.csv'+($('vsDate').value?'?score_date='+encodeURIComponent($('vsDate').value):'');
  }
  async function load(){
    const chosen=$('vsDate').value;
    const data=await api('/api/vs-points'+(chosen?'?score_date='+encodeURIComponent(chosen):''));
    saved=data.rows||[];
    $('vsDate').innerHTML=data.dates.map(d=>'<option value="'+esc(d)+'">'+esc(d)+'</option>').join('')||'<option value="">No saved dates</option>';
    if(data.score_date)$('vsDate').value=data.score_date;render();
  }
  function weekBounds(upload){
    const d=new Date(upload+'T12:00:00');const sunday=new Date(d);sunday.setDate(d.getDate()+(7-d.getDay())%7);
    const iso=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
    const monday=new Date(sunday);monday.setDate(sunday.getDate()-6);
    const sat=new Date(sunday);sat.setDate(sunday.getDate()-1);return [iso(monday),iso(sat)<upload?iso(sat):upload];
  }
  async function loadPending(){
    const data=await api('/api/vs-points/pending');pending=data.rows||[];
    $('vsPending').hidden=!pending.length;
    const groups=new Map();for(const row of pending){if(!groups.has(row.job_id))groups.set(row.job_id,[]);groups.get(row.job_id).push(row);}
    $('vsReviewJobs').innerHTML=[...groups].map(([job,rows])=>{
      const first=rows[0],[min,max]=weekBounds(first.upload_date);
      return '<section class="panel" style="padding:16px;margin:14px 0" data-vs-job="'+esc(job)+'"><h4>'+esc(first.weekday)+' selected · '+rows.length+' scores</h4><label>Points earned on <input data-vs-date type="date" value="'+esc(first.score_date)+'" min="'+min+'" max="'+max+'"></label><p>Uploaded '+esc(first.upload_date)+' · Review names and points against the captured row.</p><div class="pl-table-wrap"><table class="pl-table"><thead><tr><th>Use</th><th>Player</th><th>Points</th><th>Captured row</th></tr></thead><tbody>'+rows.map(r=>
        '<tr data-vs-row="'+r.id+'"><td><input type="checkbox" data-vs-use checked aria-label="Include score"></td><td><input data-vs-name list="vsPlayerNames" maxlength="80" aria-label="Player name" value="'+esc(r.name)+'"><small style="display:block">'+esc((r.review_reasons||[]).join('; '))+'</small></td><td><input data-vs-points type="number" min="0" step="1" aria-label="VS points" value="'+(r.points??'')+'"></td><td>'+(r.evidence_base64?'<details><summary>View row</summary><img style="max-width:550px;width:min(70vw,550px)" src="data:image/webp;base64,'+r.evidence_base64+'" alt="Captured score row"></details>':'')+'</td></tr>').join('')+'</tbody></table></div><div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:12px"><button class="btn primary" data-vs-approve type="button">Save checked scores</button><button class="btn" data-vs-dismiss type="button">Dismiss unchecked</button></div></section>';
    }).join('');
    try{const players=await api('/api/player-list');$('vsPlayerNames').innerHTML=players.players.map(p=>'<option value="'+esc(p.name)+'">').join('');}catch{}
  }
  $('vsReviewJobs').addEventListener('click',async event=>{
    const action=event.target.closest('[data-vs-approve],[data-vs-dismiss]');if(!action)return;
    const group=action.closest('[data-vs-job]');const rows=[...group.querySelectorAll('[data-vs-row]')];
    action.disabled=true;
    try{
      if(action.hasAttribute('data-vs-dismiss')){
        const ids=rows.filter(r=>!r.querySelector('[data-vs-use]').checked).map(r=>Number(r.dataset.vsRow));
        if(!ids.length)throw Error('Uncheck the rows you want to dismiss.');
        await api('/api/vs-points/dismiss',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});
      }else{
        const selected=rows.filter(r=>r.querySelector('[data-vs-use]').checked).map(r=>({id:Number(r.dataset.vsRow),name:r.querySelector('[data-vs-name]').value.trim(),points:r.querySelector('[data-vs-points]').value}));
        if(!selected.length)throw Error('Select at least one score.');
        if(selected.some(r=>r.points===''||!Number.isSafeInteger(Number(r.points))||Number(r.points)<0||r.name.length<2))throw Error('Check names and enter whole-number points for every selected row.');
        const score_date=group.querySelector('[data-vs-date]').value;
        await api('/api/vs-points/approve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job_id:group.dataset.vsJob,score_date,rows:selected.map(r=>({...r,points:Number(r.points)}))})});
        // Select the date just saved, including a newly created date.
        $('vsDate').innerHTML='<option value="'+esc(score_date)+'">'+esc(score_date)+'</option>';
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
})();

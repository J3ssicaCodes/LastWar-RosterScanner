(() => {
  const $=id=>document.getElementById(id);
  const view=$('performanceView'), picker=$('performanceDate');
  const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const style=document.createElement('style');
  style.textContent=`
    .performance-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:14px;margin:16px 0 22px}
    .performance-grid section{border:1px solid var(--stroke);border-radius:12px;padding:14px;min-width:0;background:var(--panel2)}
    .performance-grid h3{margin-top:0}
    .performance-grid p,#performanceStatus,#performanceContent>p{color:var(--muted)}
    #performanceView input,#performanceView select{color:var(--text);background:var(--panel2);border:1px solid var(--stroke);border-radius:7px;padding:7px;font:inherit}
    #performanceView .pl-table{min-width:860px}
    .performance-bar-row{display:grid;grid-template-columns:3.2rem 1fr;gap:8px;align-items:center;margin:7px 0}
    .performance-bar-label{font-size:.85rem;overflow-wrap:anywhere}
    .performance-bar-track{height:7px;background:var(--stroke);border-radius:5px;overflow:hidden}
    .performance-bar-track span{display:block;height:100%;background:#a29df4;border-radius:5px}
    .performance-bar-detail{display:block;font-size:.78rem;color:var(--muted)}
    .performance-leaders{padding-left:24px;margin-bottom:0}
    .performance-leaders li{padding:4px 0;overflow-wrap:anywhere}
    .performance-leaders small{display:block;color:var(--muted)}
    .performance-missing{color:var(--muted)}
  `;
  document.head.append(style);

  const iso=date=>[date.getFullYear(),String(date.getMonth()+1).padStart(2,'0'),String(date.getDate()).padStart(2,'0')].join('-');
  const dateLabel=(date,options)=>new Date(date+'T12:00:00').toLocaleDateString(undefined,options);
  const count=value=>BigInt(value).toLocaleString();
  const change=value=>value==null?'—':(BigInt(value)>0n?'+':'')+count(value);
  const sort=window.allianceTableSort($('performanceColumns'),null,-1,renderTable);
  const days=['Mon','Tue','Wed','Thu','Fri','Sat'];
  let model,request=0;
  picker.value=iso(new Date());

  function compare(a,b,key,ascending){
    const x=BigInt(a[key]),y=BigInt(b[key]);
    return x===y?a.name.localeCompare(b.name):(x<y?-1:1)*(ascending?1:-1);
  }
  function leaderList(id,players,key,detail,ascending=false){
    const rows=players.filter(player=>player[key]!=null).sort((a,b)=>compare(a,b,key,ascending)).slice(0,10);
    $(id).innerHTML=rows.length?'<ol class="performance-leaders">'+rows.map(p=>
      '<li><b>'+esc(p.name)+'</b> · '+(key==='vs_points'?count(p[key]):change(p[key]))+
      '<small>'+esc(detail(p))+'</small></li>').join('')+'</ol>':'<p>No comparable records for this week.</p>';
  }
  function bars(id,rows){
    const max=rows.reduce((best,row)=>Math.max(best,Number(row.points)),0);
    $(id).innerHTML=rows.map(row=>{
      const width=max?100*Number(row.points)/max:0;
      return '<div class="performance-bar-row"><span class="performance-bar-label">'+esc(row.label)+'</span><div>'+
        '<div class="performance-bar-track" aria-hidden="true"><span style="width:'+width+'%"></span></div>'+
        '<small class="performance-bar-detail">'+esc(row.detail)+'</small></div></div>';
    }).join('');
  }
  function renderGrowth(){
    if(!model)return;
    const key=$('performanceMetric').value;
    leaderList('performanceGrowth',model.players.filter(p=>p[key]!=null&&BigInt(p[key])>0n),key,p=>p.rank+' · observed change');
  }
  function renderTable(){
    if(!model)return;
    const query=$('performanceSearch').value.trim().toLocaleLowerCase();
    const rows=sort(model.players.filter(p=>p.name.toLocaleLowerCase().includes(query)),(p,key)=>{
      if(key==='name'||key==='rank'||key==='days_recorded')return p[key];
      const value=p[key??'vs_points'];
      return value==null?null:BigInt(value);
    });
    const captured=model.daily.filter(day=>day.players).length;
    $('performanceRows').innerHTML=rows.map(p=>'<tr><th scope="row"><button type="button" class="player-profile-link" data-open-player-profile="'+p.id+'">'+esc(p.name)+'</button></th><td>'+esc(p.rank)+'</td>'+
      '<td>'+(p.vs_points==null?'<span class="performance-missing">—</span>':count(p.vs_points))+'</td>'+
      '<td>'+p.days_recorded+' / '+captured+'</td><td>'+change(p.vs_change)+
      (p.days_compared?'<small class="performance-bar-detail">'+p.days_compared+' comparable days</small>':'')+'</td>'+
      ['power_change','kills_change','donations_change'].map(key=>'<td>'+change(p[key])+'</td>').join('')+'</tr>').join('')||
      '<tr><td colspan="8">No players to display.</td></tr>';
  }
  function render(){
    const active=model.players.length, captured=model.daily.filter(day=>day.players).length;
    const anyScore=model.players.filter(p=>p.days_recorded>0).length;
    const complete=captured?model.players.filter(p=>p.days_recorded===captured):[];
    const points=model.daily.reduce((sum,day)=>sum+BigInt(day.points),0n);
    $('performanceSummary').innerHTML=[
      ['VS points recorded',count(points)],['Players with scores',anyScore+' / '+active],
      ['Days captured',captured+' / 6'],['Players missing captured days',captured?active-complete.length:'—'],
    ].map(([label,value])=>'<div><small>'+label+'</small><strong>'+value+'</strong></div>').join('');
    bars('performanceDaily',model.daily.map((row,i)=>({...row,label:days[i],
      detail:row.players?count(row.points)+' points · '+row.players+'/'+active+' players':'No scores recorded'})));
    bars('performanceWeekly',model.weekly.map(row=>({...row,label:dateLabel(row.week_end,{month:'short',day:'numeric'}),
      detail:row.days_recorded?count(row.points)+' points · '+row.days_recorded+'/6 days':'No scores recorded'})));
    const participation=p=>p.rank+' · '+p.days_recorded+'/'+captured+' days recorded';
    leaderList('performanceTop',model.players,'vs_points',participation);
    leaderList('performanceLow',complete,'vs_points',participation,true);
    const followUp=model.players.filter(p=>p.days_recorded<captured)
      .sort((a,b)=>a.days_recorded-b.days_recorded||a.name.localeCompare(b.name));
    $('performanceFollowUp').innerHTML=followUp.length?
      '<ul class="performance-leaders">'+followUp.slice(0,10).map(p=>'<li><b>'+esc(p.name)+'</b><small>'+
      esc(participation(p))+'</small></li>').join('')+'</ul>'+
      (followUp.length>10?'<p>'+followUp.length+' players in total. Sort the scorecard by days recorded to see the rest.</p>':''):
      '<p>'+(captured?'Every player has a score for each captured day.':'No alliance scores recorded for this week.')+'</p>';
    leaderList('performanceGains',model.players.filter(p=>p.vs_change!=null&&BigInt(p.vs_change)>0n),
      'vs_change',p=>p.rank+' · '+p.days_compared+' comparable days');
    renderGrowth();renderTable();
    $('performanceContent').hidden=false;
  }
  async function load(){
    if(view.hidden||!picker.value)return;
    const current=++request;
    $('performanceContent').hidden=true;
    $('performanceStatus').textContent='Loading player performance…';
    try{
      const response=await fetch('/api/player-performance?week_start='+encodeURIComponent(picker.value),{cache:'no-store'});
      if(!response.ok)throw Error('Could not load player performance. Change the date or reopen the tab to retry.');
      const data=await response.json();
      if(current!==request)return;
      model=data;
      $('performanceStatus').textContent='Week of '+dateLabel(data.week_start,{dateStyle:'medium'})+' to '+dateLabel(data.week_end,{dateStyle:'medium'})+'.';
      render();
    }catch(error){
      if(current===request)$('performanceStatus').textContent=error.message;
    }
  }
  new MutationObserver(()=>{if(!view.hidden)load();}).observe(view,{attributes:true,attributeFilter:['hidden']});
  picker.addEventListener('change',load);
  $('performanceMetric').addEventListener('change',renderGrowth);
  $('performanceSearch').addEventListener('input',renderTable);
  window.addEventListener('player-profile-updated',()=>{if(!view.hidden)load();});
  if(!view.hidden)load();
})();

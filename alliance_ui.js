(() => {
  'use strict';
  const slug=location.pathname.split('/')[2];
  const $=id=>document.getElementById(id);
  let week=null,installPrompt=null;
  const showError=error=>{$('pageError').textContent=error.message||String(error);};
  function dayOffset(start,offset){
    const date=new Date(start+'T12:00:00Z');
    date.setUTCDate(date.getUTCDate()+offset);
    return date.toISOString().slice(0,10);
  }
  function cell(row,value){
    const td=document.createElement('td');
    td.textContent=value;
    row.append(td);
  }
  async function load(){
    const suffix=week?'?week_start='+encodeURIComponent(week):'';
    const response=await fetch('/api/public/'+encodeURIComponent(slug)+suffix,{cache:'no-store'});
    if(!response.ok)throw Error('Could not load this alliance.');
    const data=await response.json();
    week=data.week_start;
    document.title=data.alliance.name+' · Alliance Tools';
    $('allianceName').textContent=data.alliance.name;
    $('allianceTag').textContent='['+data.alliance.tag+'] · Server '+data.alliance.server;
    $('weekLabel').textContent=week+' to '+dayOffset(week,6);
    const grouped=new Map();
    for(const score of data.scores){
      if(!grouped.has(score.name))grouped.set(score.name,new Map());
      grouped.get(score.name).set(score.date,score.points);
    }
    const rows=[...grouped].map(([name,days])=>({name,days,total:[...days.values()].reduce((a,b)=>a+b,0)}))
      .sort((a,b)=>b.total-a.total||a.name.localeCompare(b.name));
    $('standings').replaceChildren();
    for(const item of rows){
      const row=document.createElement('tr');
      cell(row,item.name);
      for(let i=0;i<6;i++){
        const score=item.days.get(dayOffset(week,i));
        cell(row,score===undefined?'—':score.toLocaleString());
      }
      cell(row,item.total.toLocaleString());
      $('standings').append(row);
    }
    $('noScores').hidden=rows.length>0;
    $('trains').replaceChildren();
    for(const train of data.trains){
      const li=document.createElement('li');
      li.textContent=train.date+' · Conductor '+train.conductor+' · VIP '+train.vip;
      $('trains').append(li);
    }
    $('noTrains').hidden=data.trains.length>0;
    $('rosterSection').hidden=!data.roster_fields.length;
    if(data.roster_fields.length){
      const labels={rank:'Role',power:'Power',hq_level:'HQ',kills:'Kills',donations:'Donations'};
      const header=document.createElement('tr');
      for(const title of ['Player',...data.roster_fields.map(field=>labels[field])]){
        const th=document.createElement('th');th.textContent=title;header.append(th);
      }
      $('rosterHead').replaceChildren(header);$('rosterRows').replaceChildren();
      for(const player of data.roster){
        const row=document.createElement('tr');cell(row,player.name);
        for(const field of data.roster_fields)cell(row,player[field]===null?'—':String(player[field]));
        $('rosterRows').append(row);
      }
    }
  }
  $('previousWeek').onclick=()=>{week=dayOffset(week,-7);load().catch(showError);};
  $('nextWeek').onclick=()=>{week=dayOffset(week,7);load().catch(showError);};
  $('leadershipButton').onclick=async()=>{
    try{
      const result=await fetch('/api/auth/me',{cache:'no-store'});
      if(result.ok){
        const account=await result.json();
        if(account.workspaces.some(workspace=>workspace.slug===slug)){
          location.href='/a/'+slug+'/manage';return;
        }
      }
    }catch{}
    $('loginDialog').showModal();
  };
  $('closeLogin').onclick=()=>$('loginDialog').close();
  $('loginForm').onsubmit=async event=>{
    event.preventDefault();
    const form=new FormData(event.currentTarget);
    const setup=new URLSearchParams(location.hash.slice(1)).get('setup')||'';
    try{
      const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:form.get('username'),password:form.get('password'),setup_code:setup})});
      const result=await response.json();
      if(!response.ok)throw Error(result.detail||'Could not sign in.');
      history.replaceState(null,'',location.pathname);
      location.href='/a/'+slug+'/manage';
    }catch(error){$('loginError').textContent=error.message;}
  };
  window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;});
  $('installButton').onclick=async()=>{
    if(installPrompt){await installPrompt.prompt();installPrompt=null;}
    else $('installDialog').showModal();
  };
  $('closeInstall').onclick=()=>$('installDialog').close();
  for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',event=>{
    if(event.target===dialog)dialog.close();
  });
  const manifest=document.createElement('link');manifest.rel='manifest';manifest.href='/a/'+slug+'/manifest.webmanifest';document.head.append(manifest);
  if('serviceWorker' in navigator)navigator.serviceWorker.register('/service-worker.js').catch(()=>{});
  load().catch(showError);
})();

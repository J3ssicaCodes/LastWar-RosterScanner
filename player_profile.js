(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:440px;max-width:92vw;max-height:85vh;overflow:auto;border:1px solid var(--stroke);border-radius:18px;padding:24px;background:var(--panel);color:var(--text)';
  dialog.setAttribute('aria-label','Player profile');document.body.append(dialog);
  const directory=document.getElementById('plDirectoryBody');
  const styles=document.createElement('style');
  styles.textContent=`
    #plDirectoryBody tr[data-player-profile]{cursor:pointer}
    #plDirectoryBody tr[data-player-profile]:hover,#plDirectoryBody tr[data-player-profile]:focus-within{background:var(--panel2)}
    #plDirectoryBody button[data-player-profile]{background:none;border:0;box-shadow:none;padding:0;color:inherit;font:inherit;text-align:left}
    .player-profile-link{border:0;background:none;color:inherit;font:inherit;font-weight:inherit;text-align:left;padding:0;cursor:pointer}
    .player-profile-link:hover,.player-profile-link:focus-visible{text-decoration:underline}
    .player-merge-panel{margin:12px 0;padding:12px;border:1px solid var(--stroke);border-radius:10px}
    .player-merge-panel select{display:block;max-width:100%;margin:8px 0}
    .player-merge-panel [data-merge-preview]:not([hidden]){display:flex;align-items:center;gap:8px;margin:10px 0}
    .profile-history{margin-top:20px}
    .profile-history figure{margin:12px 0;padding:12px;border:1px solid var(--stroke);border-radius:10px}
    .profile-history svg{display:block;width:100%;height:auto;overflow:visible}
    .profile-history svg text{fill:var(--muted);font:10px system-ui}
    .profile-history figcaption{font-weight:700}
    .profile-history small{color:var(--muted)}
    .profile-vs-legend{display:flex;flex-wrap:wrap;gap:6px 14px;margin:6px 0;color:var(--muted);font-size:.85rem}
    .profile-vs-legend span{display:inline-flex;align-items:center;gap:6px}
    .profile-vs-legend i{display:inline-block;width:18px;border-top:3px solid var(--cyan)}
    .profile-vs-legend .previous{border-top:3px dashed var(--violet)}
    .profile-vs-legend .goal{border-top:2px dotted var(--green)}
    .player-profile-name{display:flex;align-items:center;gap:8px;margin-top:10px}
    .player-profile-name h2{margin:0;min-width:0;overflow-wrap:anywhere}
    .player-profile-name input{flex:1;min-width:0}
    .player-profile-name .row-edit-trigger{flex:none}
    .player-extra{margin:16px 0;padding:12px;border:1px solid var(--stroke);border-radius:10px}
    .player-extra label{display:block;margin:10px 0}.player-extra input[type="date"],.player-extra textarea{display:block;width:100%;max-width:100%;padding:8px;border:1px solid var(--stroke);border-radius:6px;background:var(--panel2);color:var(--text);font:inherit}.player-extra textarea{min-height:90px;resize:vertical}
    .player-profile-name input,[aria-label="Player profile"] select{padding:6px 8px;background:var(--panel2);color:var(--text);border:1px solid var(--stroke);border-radius:6px;font:inherit}
  `;
  document.head.append(styles);
  let player,roster=[],request=0,history=null;
  const editIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v3M7 8h6M7 12h3"/><path d="m18 10 4 4-7 7h-4v-4Z"/></svg>';
  const saveIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v6h8"/></svg>';
  const number=v=>v==null?'—':Number(v).toLocaleString();
  function vsComparisonChart(comparison){
    const caption='<figcaption>Daily VS points · This week vs. last week</figcaption>';
    const current=comparison?.current||[],previous=comparison?.previous||[];
    const values=[...current,...previous].filter(value=>value!=null);
    if(!values.length)return '<figure>'+caption+'<p>No VS scores recorded for either week.</p></figure>';
    const days=['Mon','Tue','Wed','Thu','Fri','Sat'];
    const goal=7_200_000;
    const max=Math.max(8_000_000,Math.ceil(Math.max(...values)/2)*2);
    const x=index=>52+59*index,y=value=>108-92*value/max;
    const short=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1});
    const grid=[0,max/2,max].map(value=>
      '<path d="M52 '+y(value)+' H347" stroke="var(--stroke)"/><text x="47" y="'+(y(value)+3)+'" text-anchor="end">'+esc(short.format(value))+'</text>').join('');
    const ticks=days.map((day,i)=>'<text x="'+x(i)+'" y="127" text-anchor="middle">'+day+'</text>').join('');
    function plot(scores,label,color,dashed=false){
      let path='',dots='';
      for(const [i,score] of scores.entries()){
        if(score==null)continue;
        path+=(scores[i-1]==null?'M':'L')+x(i)+' '+y(score)+' ';
        dots+='<circle cx="'+x(i)+'" cy="'+y(score)+'" r="'+(dashed?5:3)+'" fill="'+(dashed?'var(--panel)':color)+'" stroke="'+color+'" stroke-width="2"><title>'+
          esc(label+' · '+days[i]+' · '+number(score))+'</title></circle>';
      }
      return '<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="2"'+(dashed?' stroke-dasharray="5 4"':'')+'/>'+dots;
    }
    const goalLine='<path d="M52 '+y(goal)+' H347" stroke="var(--green)" stroke-dasharray="1 4" stroke-linecap="round"/>';
    return '<figure>'+caption+'<div class="profile-vs-legend"><span><i aria-hidden="true"></i>This week</span><span><i class="previous" aria-hidden="true"></i>Last week</span><span><i class="goal" aria-hidden="true"></i>7.2M goal</span></div>'+
      '<svg viewBox="0 0 370 136" role="img" aria-label="Daily VS points for this week and last week, Monday through Saturday, with a 7.2 million point goal">'+
      grid+goalLine+plot(previous,'Last week','var(--violet)',true)+plot(current,'This week','var(--cyan)')+ticks+'</svg></figure>';
  }
  function historyChart(label,axis,rows){
    const caption='<figcaption>'+label+'</figcaption><small>'+axis+'</small>';
    if(!rows.length)return '<figure>'+caption+'<p>No history yet.</p></figure>';
    const dates=rows.map(row=>Date.parse(row.date+'T00:00:00Z'));
    const values=rows.map(row=>Number(row.value));
    const first=dates[0],last=dates.at(-1);
    const high=Math.max(...values),low=Math.min(...values);
    const span=high-low||Math.max(high*.05,1);
    const min=Math.max(0,low-span*.12),max=high+span*.12;
    const x=i=>first===last?209:56+306*(dates[i]-first)/(last-first);
    const y=value=>109-95*(value-min)/(max-min);
    const short=new Intl.NumberFormat(undefined,{notation:'compact',maximumFractionDigits:1});
    const formatDate=value=>new Date(value).toLocaleDateString(undefined,{timeZone:'UTC',month:'short',day:'numeric',year:'2-digit'});
    const grid=[min,(min+max)/2,max].map(value=>
      '<path d="M56 '+y(value)+' H362" stroke="var(--stroke)"/><text x="51" y="'+(y(value)+3)+'" text-anchor="end">'+esc(short.format(value))+'</text>').join('');
    const ticks=[...new Set([0,Math.floor((dates.length-1)/2),dates.length-1])].map(i=>
      '<text x="'+x(i)+'" y="128" text-anchor="'+(first===last?'middle':i===0?'start':i===dates.length-1?'end':'middle')+'">'+esc(formatDate(dates[i]))+'</text>').join('');
    const coords=values.map((value,i)=>x(i)+','+y(value)).join(' ');
    const dots=values.map((value,i)=>'<circle cx="'+x(i)+'" cy="'+y(value)+'" r="3" fill="#a29df4"><title>'+
      esc(formatDate(dates[i])+', '+number(value))+'</title></circle>').join('');
    return '<figure>'+caption+'<svg viewBox="0 0 370 138" role="img" aria-label="'+label+' history. Latest '+esc(number(values.at(-1)))+'">'+
      grid+'<polyline fill="none" stroke="#a29df4" stroke-width="2" points="'+coords+'"/>'+dots+ticks+'</svg>'+
      '<small>Latest '+esc(number(values.at(-1)))+'</small></figure>';
  }
  function renderHistory(){
    const target=dialog.querySelector('[data-history]');
    if(!target)return;
    target.innerHTML='<h3>History</h3>'+(history===null?'<p role="status">Loading stats history…</p>':
      history.error?'<p role="status">Could not load stats history.</p>':vsComparisonChart(history.vs_comparison)+[
        ['Weekly VS points','Week ending Sunday',history.vs],
        ['Power','Upload date',history.power],['Kills','Upload date',history.kills],
        ['Donations','Upload date',history.donations],
      ].map(([label,axis,rows])=>historyChart(label,axis,rows||[])).join(''));
  }
  function render(edit=false){
    const p=player;
    const action=edit?'Save player changes':'Edit player details';
    const nameControl=edit
      ? '<input name="name" aria-label="Player name" minlength="2" maxlength="80" required value="'+esc(p.name)+'">'
      : '<h2>'+esc(p.name)+'</h2>';
    const rankControl=edit
      ? '<label>Rank <select name="rank" aria-label="Player rank">'+['R5','R4','R3','R2','R1'].map(rank=>'<option '+(rank===p.rank?'selected':'')+'>'+rank+'</option>').join('')+'</select></label>'
      : esc(p.rank);
    dialog.innerHTML='<button class="btn" type="button" data-close style="float:right" aria-label="Close player profile">✕</button>'+
      (p.has_avatar?'<img class="pl-avatar" style="width:72px;height:72px" src="'+esc(p.avatar_url)+'" alt="Player portrait">':'')+
      (edit?'<form>':'')+
      '<div class="player-profile-name">'+nameControl+
      '<button class="row-edit-trigger '+(edit?'saving':'')+'" '+(edit?'type="submit"':'type="button" data-edit')+' aria-label="'+action+'" title="'+action+'">'+(edit?saveIcon:editIcon)+'</button></div>'+
      '<p>'+rankControl+' · '+(p.possibly_removed?'Possibly removed':p.is_new?'New player':'Active')+'</p>'+
      (edit?'<p data-error role="status"></p></form>':'')+
      '<dl style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'+[
        ['Power',p.power==null?(p.power_millions==null?'—':number(p.power_millions)+'M'):number(p.power)],
        ['Kills',number(p.kills)],['Donations',number(p.donations)],['HQ',number(p.hq_level)],
        ['Birthday',p.birthday||'—'],['Last seen',p.last_seen_at?new Date(p.last_seen_at+'Z').toLocaleDateString():'—']
      ].map(([label,value])=>'<dt>'+label+'</dt><dd style="margin:0">'+esc(value)+'</dd>').join('')+'</dl>'+
      (edit?'':'<form class="player-extra" data-player-details><h3>Player details</h3><label><input type="checkbox" name="has_t11" '+(p.has_t11?'checked':'')+'> Has T11</label><label>Away from<input type="date" name="away_start" value="'+esc(p.away_start||'')+'"></label><label>Away through<input type="date" name="away_end" value="'+esc(p.away_end||'')+'"></label><label>Player notes<textarea name="notes" maxlength="5000">'+esc(p.player_notes||'')+'</textarea></label><button type="submit" class="btn primary">Save details</button><p data-details-status role="status"></p></form>')+
      (edit?'':'<p><label><input type="checkbox" data-train-restriction disabled> Disable train assignments</label> <span data-train-restriction-status role="status"></span></p><button type="button" class="btn" data-merge>Merge duplicate</button> <button type="button" class="btn" data-claim-vs>Claim VS entry</button> <button type="button" class="btn" data-archive-player>Archive player</button><p data-merge-message role="status" hidden></p>')+
      '<section class="profile-history" data-history></section>';
    renderHistory();
    loadTrainRestriction(p);
    dialog.querySelector('[data-merge]')?.addEventListener('click',openMerge);
    dialog.querySelector('[data-claim-vs]')?.addEventListener('click',openVSClaim);
    dialog.querySelector('[data-archive-player]')?.addEventListener('click',async event=>{
      const selected=player;
      if(!window.confirm('Archive '+selected.name+'? They will be hidden from the active player list and analytics. Their history will be kept, and you can reactivate them in Admin → Player Archive.'))return;
      const button=event.currentTarget;button.disabled=true;
      try{
        const response=await fetch('/api/media/players/'+selected.id+'/archive',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_name:selected.name})});
        const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not archive this player.');
        if(player===selected&&dialog.open)dialog.close();
        await Promise.all([window.playerListRefresh?.(),window.vsPointsRefresh?.()]);
        window.dispatchEvent(new Event('player-archive-changed'));
        window.dispatchEvent(new Event('player-profile-updated'));
      }catch(error){const message=dialog.querySelector('[data-merge-message]');if(message){message.hidden=false;message.textContent=error.message;}}
      finally{button.disabled=false;}
    });
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.querySelector('[data-edit]')?.addEventListener('click',()=>{
      render(true);
      const input=dialog.querySelector('[name="name"]');
      input.focus();
      input.select();
    });
    dialog.querySelector('[data-player-details]')?.addEventListener('submit',async event=>{
      event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),status=form.querySelector('[data-details-status]'),edited=player;
      const start=form.elements.away_start.value||null,end=form.elements.away_end.value||null;
      if(Boolean(start)!==Boolean(end)||start&&end<start){status.textContent='Enter both dates, with the end on or after the start.';return;}
      button.disabled=true;status.textContent='Saving…';
      try{
        const response=await fetch('/api/player-list/'+edited.id+'/details',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          has_t11:form.elements.has_t11.checked,away_start:start,away_end:end,notes:form.elements.notes.value,
          expected_has_t11:!!edited.has_t11,expected_away_start:edited.away_start||null,expected_away_end:edited.away_end||null,expected_notes:edited.player_notes||''})});
        const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not save player details.');
        Object.assign(edited,result);if(form.isConnected)status.textContent='Saved.';
        await window.playerListRefresh?.();window.dispatchEvent(new Event('player-profile-updated'));
      }catch(error){if(form.isConnected)status.textContent=error.message;}
      finally{button.disabled=false;}
    });
    dialog.querySelector('form:not([data-player-details])')?.addEventListener('submit',async event=>{
      event.preventDefault();const form=event.target,button=form.querySelector('[type="submit"]');button.disabled=true;
      const edited=player;
      try{
        const response=await fetch('/api/player-list/'+edited.id+'/profile',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:form.elements.name.value.trim(),rank:form.elements.rank.value,expected_name:edited.name,expected_rank:edited.rank})});
        const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Check the player name and rank.');
        Object.assign(edited,result);
        if(player===edited&&form.isConnected&&dialog.open){
          render();
          dialog.querySelector('[data-edit]').focus();
        }
        await window.playerListRefresh?.();window.vsPointsRefresh?.();
        window.dispatchEvent(new Event('player-profile-updated'));
      }catch(err){if(form.isConnected)form.querySelector('[data-error]').textContent=err.message;}
      finally{button.disabled=false;}
    });
  }
  async function loadTrainRestriction(p){
    const toggle=dialog.querySelector('[data-train-restriction]');
    if(!toggle)return;
    const status=dialog.querySelector('[data-train-restriction-status]');
    const path='/api/train/restriction/'+p.id;
    try{
      const response=await fetch(path,{cache:'no-store'});
      if(!response.ok)throw Error();
      const data=await response.json();
      if(!toggle.isConnected)return;
      toggle.checked=data.disabled;toggle.disabled=false;
    }catch{if(toggle.isConnected)status.textContent='Could not load train eligibility.';return;}
    toggle.onchange=async()=>{
      const disabled=toggle.checked;
      if(!window.confirm((disabled?'Disallow ':'Allow ')+p.name+' to be awarded a train?')){
        toggle.checked=!disabled;return;
      }
      toggle.disabled=true;
      try{
        const response=await fetch(path,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({disabled})});
        if(!response.ok)throw Error();
        status.textContent=disabled?'Train awards disabled.':'Train awards allowed.';
        window.dispatchEvent(new Event('train-restriction-changed'));
      }catch{toggle.checked=!disabled;status.textContent='Could not update train eligibility.';}
      finally{toggle.disabled=false;}
    };
  }
  function openMerge(event){
    dialog.querySelector('[data-claim-vs]').disabled=true;
    const keep=player;
    const candidates=roster.filter(p=>p.id!==keep.id).sort((a,b)=>a.name.localeCompare(b.name));
    const form=document.createElement('form');
    form.className='player-merge-panel';
    form.innerHTML='<h3>Keep '+esc(keep.name)+'</h3>'+
      '<label>Merge this duplicate into '+esc(keep.name)+'<select name="duplicate" required>'+
      '<option value="">Choose duplicate…</option>'+candidates.map(p=>'<option value="'+p.id+'">'+esc(p.name)+' · '+esc(p.rank)+'</option>').join('')+
      '</select></label><div data-merge-preview hidden></div>'+
      '<p>The retained player’s filled-in details and same-day VS scores take priority. Other stats and history carry over, and the duplicate name becomes an OCR alias.</p>'+
      '<button type="submit" class="btn primary" disabled>Confirm merge</button> <button type="button" class="btn" data-merge-cancel>Cancel</button>'+
      '<p role="status" data-merge-error></p>';
    event.currentTarget.replaceWith(form);
    const select=form.elements.duplicate,preview=form.querySelector('[data-merge-preview]');
    const submit=form.querySelector('[type="submit"]'),error=form.querySelector('[data-merge-error]');
    form.querySelector('[data-merge-cancel]').onclick=()=>{render();dialog.querySelector('[data-merge]').focus();};
    select.onchange=()=>{
      const duplicate=candidates.find(p=>p.id===Number(select.value));
      preview.hidden=!duplicate;submit.disabled=!duplicate;
      preview.innerHTML=duplicate?
        (duplicate.has_avatar?'<img class="pl-avatar" src="'+esc(duplicate.avatar_url)+'" alt="Duplicate player portrait">':'')+
        '<strong>'+esc(duplicate.name)+' → '+esc(keep.name)+'</strong>':'';
    };
    form.onsubmit=async event=>{
      event.preventDefault();
      const duplicate=candidates.find(p=>p.id===Number(select.value));
      if(!duplicate||submit.disabled)return;
      submit.disabled=true;select.disabled=true;error.textContent='Merging player records…';
      try{
        const response=await fetch('/api/player-list/'+keep.id+'/merge',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({duplicate_id:duplicate.id,expected_name:keep.name,expected_duplicate_name:duplicate.name})});
        const result=await response.json();
        if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not merge players.');
        if(form.isConnected&&dialog.open){
          const conflicts=result.overlapping_vs_scores;
          await openProfile(keep.id,'Merged '+result.merged_name+' into '+result.name+'.'+
            (conflicts?' Kept the retained record’s score on '+conflicts+' overlapping date'+(conflicts===1?'':'s')+'.':''));
        }
        await window.playerListRefresh?.();window.vsPointsRefresh?.();
        window.dispatchEvent(new Event('player-profile-updated'));
      }catch(err){if(form.isConnected)error.textContent=err.message;}
      finally{if(form.isConnected){submit.disabled=false;select.disabled=false;}}
    };
    select.focus();
  }
  async function openVSClaim(event){
    dialog.querySelector('[data-merge]').disabled=true;
    const keep=player;
    const button=event.currentTarget;
    const form=document.createElement('form');
    form.className='player-merge-panel';
    form.innerHTML='<h3>Claim VS scores for '+esc(keep.name)+'</h3>'+
      '<label>Unmatched VS name <select name="claim" required disabled><option value="">Loading names…</option></select></label>'+
      '<p>All saved dates under the selected name will move here. Where both names have a score on the same day, this player’s score takes priority.</p>'+
      '<button type="submit" class="btn primary" disabled>Confirm VS claim</button> <button type="button" class="btn" data-claim-cancel>Cancel</button>'+
      '<p data-claim-status role="status"></p>';
    button.replaceWith(form);
    const select=form.elements.claim,submit=form.querySelector('[type="submit"]');
    const status=form.querySelector('[data-claim-status]');
    const endpoint='/api/vs-points/claims/'+keep.id;
    form.querySelector('[data-claim-cancel]').onclick=()=>{render();dialog.querySelector('[data-claim-vs]').focus();};
    select.onchange=()=>{submit.disabled=!select.value;};
    try{
      const response=await fetch(endpoint,{cache:'no-store'});
      if(!response.ok)throw Error('Could not load unmatched VS names.');
      const {options}=await response.json();
      if(!form.isConnected||!dialog.open)return;
      select.innerHTML='<option value="">'+(options.length?'Choose a VS name…':'No unmatched VS names')+'</option>'+
        options.map(row=>'<option value="'+esc(row.name_key)+'">'+esc(row.name)+' · '+row.score_days+' day'+(row.score_days===1?'':'s')+'</option>').join('');
      select.disabled=!options.length;
      if(options.length)select.focus();
    }catch(error){if(form.isConnected)status.textContent=error.message;}
    form.onsubmit=async event=>{
      event.preventDefault();
      if(!select.value||submit.disabled)return;
      submit.disabled=true;select.disabled=true;status.textContent='Claiming VS scores…';
      try{
        const response=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({name_key:select.value,expected_player_name:keep.name})});
        const result=await response.json();
        if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not claim these VS scores.');
        if(form.isConnected&&dialog.open){
          const overlap=result.overlapping_vs_scores;
          await openProfile(keep.id,'Claimed '+result.claimed_name+' for '+keep.name+'.'+
            (overlap?' Kept the existing score on '+overlap+' overlapping date'+(overlap===1?'':'s')+'.':''));
        }
        window.vsPointsRefresh?.();
        window.dispatchEvent(new Event('player-profile-updated'));
      }catch(error){if(form.isConnected)status.textContent=error.message;}
      finally{if(form.isConnected){submit.disabled=false;select.disabled=false;}}
    };
  }
  dialog.addEventListener('click',event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))dialog.close();});
  async function openProfile(id,notice=''){
    const current=++request;history=null;dialog.innerHTML='<p role="status">Loading player…</p>';if(!dialog.open)dialog.showModal();
    try{
      const response=await fetch('/api/player-list',{cache:'no-store'});if(!response.ok)throw Error('Could not load player profile.');
      const data=await response.json();if(current!==request||!dialog.open)return;
      roster=data.players;
      player=roster.find(p=>p.id===id);if(!player)throw Error('Player not found.');render();
      const message=dialog.querySelector('[data-merge-message]');
      if(notice&&message){message.hidden=false;message.textContent=notice;}
      try{
        const today=new Date();
        const localDay=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');
        const stats=await fetch('/api/player-list/'+player.id+'/history?week_start='+localDay,{cache:'no-store'});
        if(!stats.ok)throw Error('History unavailable.');
        const result=await stats.json();
        if(current===request&&dialog.open){history=result;renderHistory();}
      }catch{
        if(current===request&&dialog.open){history={error:true};renderHistory();}
      }
    }catch(err){if(current===request&&dialog.open)dialog.textContent=err.message;}
  }
  document.addEventListener('click',event=>{
    const link=event.target.closest('[data-open-player-profile]');
    if(link)openProfile(Number(link.dataset.openPlayerProfile));
  });
  window.openPlayerProfile=openProfile;
  directory.addEventListener('click',event=>{
    if(event.target.closest('[data-player-birthday]'))return;
    const row=event.target.closest('tr[data-player-profile]');
    if(row)openProfile(Number(row.dataset.playerProfile));
  });
})();

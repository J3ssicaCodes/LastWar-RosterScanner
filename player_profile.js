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
    .profile-history{margin-top:20px}
    .profile-history figure{margin:12px 0;padding:12px;border:1px solid var(--stroke);border-radius:10px}
    .profile-history svg{display:block;width:100%;height:auto;overflow:visible}
    .profile-history svg text{fill:var(--muted);font:10px system-ui}
    .profile-history figcaption{font-weight:700}
    .profile-history small{color:var(--muted)}
    .player-profile-name{display:flex;align-items:center;gap:8px;margin-top:10px}
    .player-profile-name h2{margin:0;min-width:0;overflow-wrap:anywhere}
    .player-profile-name input{flex:1;min-width:0}
    .player-profile-name .row-edit-trigger{flex:none}
    .player-profile-name input,[aria-label="Player profile"] select{padding:6px 8px;background:var(--panel2);color:var(--text);border:1px solid var(--stroke);border-radius:6px;font:inherit}
  `;
  document.head.append(styles);
  let player,request=0,history=null;
  const editIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v3M7 8h6M7 12h3"/><path d="m18 10 4 4-7 7h-4v-4Z"/></svg>';
  const saveIcon='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v6h8"/></svg>';
  const number=v=>v==null?'—':Number(v).toLocaleString();
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
      history.error?'<p role="status">Could not load stats history.</p>':[
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
      ].map(([label,value])=>'<dt>'+label+'</dt><dd style="margin:0">'+esc(value)+'</dd>').join('')+'</dl><section class="profile-history" data-history></section>';
    renderHistory();
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.querySelector('[data-edit]')?.addEventListener('click',()=>{
      render(true);
      const input=dialog.querySelector('[name="name"]');
      input.focus();
      input.select();
    });
    dialog.querySelector('form')?.addEventListener('submit',async event=>{
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
  dialog.addEventListener('click',event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))dialog.close();});
  directory.addEventListener('click',async event=>{
    if(event.target.closest('[data-player-birthday]'))return;
    const row=event.target.closest('tr[data-player-profile]');
    if(!row)return;
    const current=++request;history=null;dialog.innerHTML='<p role="status">Loading player…</p>';if(!dialog.open)dialog.showModal();
    try{
      const response=await fetch('/api/player-list',{cache:'no-store'});if(!response.ok)throw Error('Could not load player profile.');
      const data=await response.json();if(current!==request||!dialog.open)return;
      player=data.players.find(p=>p.id===Number(row.dataset.playerProfile));if(!player)throw Error('Player not found.');render();
      try{
        const stats=await fetch('/api/player-list/'+player.id+'/history',{cache:'no-store'});
        if(!stats.ok)throw Error('History unavailable.');
        const result=await stats.json();
        if(current===request&&dialog.open){history=result;renderHistory();}
      }catch{
        if(current===request&&dialog.open){history={error:true};renderHistory();}
      }
    }catch(err){if(current===request&&dialog.open)dialog.textContent=err.message;}
  });
})();

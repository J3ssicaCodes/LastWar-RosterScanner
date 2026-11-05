(() => {
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:440px;max-width:92vw;max-height:85vh;overflow:auto;border:1px solid var(--stroke);border-radius:18px;padding:24px;background:var(--panel);color:var(--text)';
  dialog.setAttribute('aria-label','Player profile');document.body.append(dialog);
  let player,request=0;
  const number=v=>v==null?'—':Number(v).toLocaleString();
  function render(edit=false){
    const p=player;
    dialog.innerHTML='<button class="btn" type="button" data-close style="float:right" aria-label="Close player profile">✕</button>'+
      (p.has_avatar?'<img class="pl-avatar" style="width:72px;height:72px" src="'+esc(p.avatar_url)+'" alt="Player portrait">':'')+
      '<h2>'+esc(p.name)+'</h2><p>'+esc(p.rank)+' · '+(p.possibly_removed?'Possibly removed':p.is_new?'New player':'Active')+'</p>'+
      '<dl style="display:grid;grid-template-columns:1fr 1fr;gap:12px">'+[
        ['Power',p.power==null?(p.power_millions==null?'—':number(p.power_millions)+'M'):number(p.power)],
        ['Kills',number(p.kills)],['Donations',number(p.donations)],['HQ',number(p.hq_level)],
        ['Birthday',p.birthday||'—'],['Last seen',p.last_seen_at?new Date(p.last_seen_at+'Z').toLocaleDateString():'—']
      ].map(([label,value])=>'<dt>'+label+'</dt><dd style="margin:0">'+esc(value)+'</dd>').join('')+'</dl>'+
      (edit?'<form><label>Name <input name="name" aria-label="Player name" minlength="2" maxlength="80" required value="'+esc(p.name)+'" style="width:100%;box-sizing:border-box;margin:8px 0 16px"></label><label>Rank <select name="rank" aria-label="Player rank">'+['R5','R4','R3','R2','R1'].map(r=>'<option '+(r===p.rank?'selected':'')+'>'+r+'</option>').join('')+'</select></label><p data-error role="status"></p><button type="submit" class="btn primary">Save changes</button> <button type="button" class="btn" data-cancel>Cancel</button></form>':'<button class="btn primary" type="button" data-edit>Edit player</button>');
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
    dialog.querySelector('[data-edit]')?.addEventListener('click',()=>{render(true);dialog.querySelector('[name="name"]').focus();});
    dialog.querySelector('[data-cancel]')?.addEventListener('click',()=>render());
    dialog.querySelector('form')?.addEventListener('submit',async event=>{
      event.preventDefault();const form=event.target,button=form.querySelector('[type="submit"]');button.disabled=true;
      const edited=player;
      try{
        const response=await fetch('/api/player-list/'+edited.id+'/profile',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:form.elements.name.value.trim(),rank:form.elements.rank.value,expected_name:edited.name,expected_rank:edited.rank})});
        const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Check the player name and rank.');
        Object.assign(edited,result);if(player===edited&&dialog.open)render();
        await window.playerListRefresh?.();window.vsPointsRefresh?.();
        window.dispatchEvent(new Event('player-profile-updated'));
      }catch(err){if(form.isConnected)form.querySelector('[data-error]').textContent=err.message;}
      finally{button.disabled=false;}
    });
  }
  dialog.addEventListener('click',event=>{const r=dialog.getBoundingClientRect();if(event.target===dialog&&(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom))dialog.close();});
  document.getElementById('plDirectoryBody').addEventListener('click',async event=>{
    if(event.target.closest('[data-player-birthday]'))return;
    const row=event.target.closest('[data-player-profile]');if(!row)return;
    const current=++request;dialog.innerHTML='<p role="status">Loading player…</p>';if(!dialog.open)dialog.showModal();
    try{
      const response=await fetch('/api/player-list',{cache:'no-store'});if(!response.ok)throw Error('Could not load player profile.');
      const data=await response.json();if(current!==request||!dialog.open)return;
      player=data.players.find(p=>p.id===Number(row.dataset.playerProfile));if(!player)throw Error('Player not found.');render();
    }catch(err){dialog.textContent=err.message;}
  });
})();

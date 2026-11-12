(() => {
  const view=document.getElementById('operationsView');
  if(!view)return;
  const slug=window.workspaceSlug;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const apiBase='/api/auth/alliances/'+encodeURIComponent(slug)+'/operations';
  let state={operations:[],leaders:[],timezone:'UTC'},openIds=new Set();
  async function api(path='',method='GET',body){
    const opts={method,cache:'no-store'};
    if(body!==undefined){opts.headers={'Content-Type':'application/json'};opts.body=JSON.stringify(body);}
    const res=await fetch(apiBase+path,opts),json=await res.json();
    if(!res.ok)throw Error(typeof json.detail==='string'?json.detail:'Please check the information and try again.');
    return json;
  }
  const styles=document.createElement('style');styles.textContent=`
    .ops-list{display:grid;gap:12px}.ops-card{border:1px solid var(--stroke);border-radius:12px;padding:14px;background:var(--panel2)}
    .ops-card>summary{cursor:pointer;font-weight:700;font-size:1.1rem}.ops-operation-title{display:flex;align-items:center;justify-content:space-between;gap:8px}.ops-edit-operation{border:0;background:transparent;color:inherit;cursor:pointer;padding:5px;line-height:1}.ops-edit-operation svg{width:19px;height:19px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}.ops-warning{color:#ed3038;font-weight:900;margin-left:3px}.ops-away{color:#ef5157;font-size:.88em;font-weight:700;margin-left:5px}.ops-coverage-row{border-top:1px solid var(--stroke);padding:10px 0}.ops-coverage-row p{margin:5px 0}.ops-coverage{margin:12px 0}.ops-text{white-space:pre-wrap;overflow-wrap:anywhere}
    .ops-form{display:grid;gap:10px;margin:12px 0}.ops-form label{display:grid;gap:4px}.ops-form :is(input,textarea,select){width:100%;min-width:0}
    .ops-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:10px}.ops-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
    .ops-assignees{border:1px solid var(--stroke);border-radius:8px;padding:10px}.ops-assignees label{display:flex;gap:8px;align-items:center}.ops-assignees input{width:auto}
    .ops-template{border-top:1px solid var(--stroke);padding:10px 0}.ops-muted{color:var(--muted)}.ops-status{min-height:1.5em}
  `;document.head.append(styles);
  view.innerHTML=`<section class="panel pl-section"><div class="workspace-heading"><div class="eyebrow">Alliance leadership</div><h2>Operations</h2><p>Organize recurring responsibilities and assign task templates to your leadership team.</p></div>
    <div class="ops-actions"><button class="btn" type="button" data-view-tasks>View assigned tasks</button></div><p class="ops-status" role="status" aria-live="polite"></p><details class="ops-card ops-coverage"><summary>Assignments &amp; coverage</summary><p class="ops-muted">See responsible leaders and who is currently away.</p><div data-coverage></div></details><details class="ops-card" data-create><summary>+ Create operation</summary><div data-form></div></details><h3>Alliance operations</h3><div class="ops-list" data-list></div></section>`;
  const status=view.querySelector('.ops-status'),list=view.querySelector('[data-list]');
  view.querySelector('[data-view-tasks]').onclick=()=>window.openLeadershipAccount?.({tasks:'1'});
  function today(){const parts=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:state.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(p=>[p.type,p.value]));return `${parts.year}-${parts.month}-${parts.day}`;}
  function warning(p){return p&&!p.has_account?' <span class="ops-warning" aria-label="No linked R4 or R5 account" title="No linked R4 or R5 account">!</span>':'';}
  function away(p){return p?.away_today?` <span class="ops-away" title="Away ${esc(p.away_start)} through ${esc(p.away_end)}">💤 Away through ${esc(p.away_end)}</span>`:'';}
  function operationForm(op={}){
    op=op||{};
    const selected=new Set(op.secondary_refs||[]);
    const choices=(role)=>state.leaders.map(p=>`<label><input type="${role==='primary'?'radio':'checkbox'}" name="${role==='primary'?'primary_ref':'secondary_refs'}" value="${esc(p.id)}" ${role==='primary'?(op.primary_ref===p.id?'checked':''):(selected.has(p.id)?'checked':'')} ${role==='primary'?'required':''}>${esc(p.display_name)} · ${esc(p.role)}${warning(p)}${away(p)}</label>`).join('');
    return `<form class="ops-form" data-operation-form><label>Operation name<input name="name" maxlength="160" required value="${esc(op.name||'')}" placeholder="Train management"></label>
      <label>Brief description<textarea name="description" maxlength="4000" rows="2">${esc(op.description||'')}</textarea></label>
      <label>Work instructions<textarea name="instructions" maxlength="20000" rows="5">${esc(op.instructions||'')}</textarea></label>
      <p class="ops-muted"><span class="ops-warning">!</span> No linked R4 or R5 account. Tasks will be assigned once their player profile is linked to one.</p>
      <fieldset class="ops-assignees"><legend>Primary person responsible</legend>${choices('primary')}</fieldset>
      <fieldset class="ops-assignees"><legend>Secondary people responsible · up to 5</legend>${choices('secondary')}</fieldset>
      <div class="ops-actions"><button type="submit" class="btn primary">${op.id?'Save changes':'Create operation'}</button></div><p data-form-status role="status"></p></form>`;
  }
  function bindOperationForm(container,op){
    container.innerHTML=operationForm(op);const form=container.querySelector('form');
    form.onsubmit=async event=>{event.preventDefault();const data=new FormData(form),secondary=data.getAll('secondary_refs'),primary=data.get('primary_ref'),notice=form.querySelector('[data-form-status]');
      if(secondary.length>5||secondary.includes(primary)){notice.textContent='Choose up to five different secondary leaders; the primary cannot also be secondary.';return;}
      const button=form.querySelector('button[type=submit]');button.disabled=true;
      try{await api(op?.id?'/'+op.id:'',op?.id?'PUT':'POST',{name:data.get('name'),description:data.get('description'),instructions:data.get('instructions'),primary_ref:primary,secondary_refs:secondary});
        if(op?.id)openIds.add(op.id);else view.querySelector('[data-create]').open=false;await load();status.textContent=op?.id?'Operation updated.':'Operation created.';
      }catch(e){notice.textContent=e.message;}finally{button.disabled=false;}
    };
  }
  function templateForm(oid,t={}){const schedule=t.schedule||{};return `<details><summary>${t.id?'Edit task template':'+ Add task template'}</summary><form class="ops-form" data-template-form="${oid}" data-template-id="${t.id||''}">
    <label>Task name<input name="title" required maxlength="160" value="${esc(t.title||'')}" placeholder="Select the drivers"></label><label>Task details<textarea name="description" rows="2" maxlength="1000">${esc(t.description||'')}</textarea></label>
    <div class="ops-grid"><label>Repeat<select name="kind">${[['daily','Daily'],['weekly','Weekly'],['monthly','Monthly'],['once','One time'],['interval','Custom interval']].map(([v,label])=>`<option value="${v}" ${schedule.kind===v?'selected':''}>${label}</option>`).join('')}</select></label>
    <label>Starts on<input type="date" name="start_date" value="${esc(schedule.start_date||today())}" required></label><label>Due time · ${esc(state.timezone)}<input type="time" name="due_time" value="${esc(schedule.due_time||'18:00')}" required></label>
    <label>End date · optional<input type="date" name="end_date" value="${esc(schedule.end_date||'')}"></label><label>Custom interval<input type="number" name="interval" min="1" max="365" value="${esc(schedule.interval||1)}"></label><label>Interval unit<select name="interval_unit">${[['day','Days'],['week','Weeks'],['month','Months']].map(([v,label])=>`<option value="${v}" ${schedule.interval_unit===v?'selected':''}>${label}</option>`).join('')}</select></label></div>
    <fieldset class="ops-assignees" data-weekdays ${schedule.kind==='weekly'?'':'hidden'}><legend>Days of the week</legend>${['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'].map((day,i)=>`<label><input type="checkbox" name="weekdays" value="${i}" ${(schedule.weekdays||[]).includes(i)?'checked':''}>${day}</label>`).join('')}</fieldset>
    <p class="ops-muted">Each responsible leader with a linked R4/R5 account gets this task. Weekly days can be selected above; monthly schedules follow the start date.</p><button class="btn primary">${t.id?'Save task template':'Add task template'}</button><p data-form-status role="status"></p></form></details>`;}
  function render(){
    const leaders=new Map(state.leaders.map(p=>[p.id,p]));
    const person=ref=>{const p=leaders.get(ref);return p?esc(p.display_name)+warning(p)+away(p):'Former leader';};
    const coverage=view.querySelector('[data-coverage]');
    coverage.innerHTML=state.operations.length?state.operations.map(op=>`<div class="ops-coverage-row"><strong>${esc(op.name)}</strong><p class="ops-text">${esc(op.description||'No description yet.')}</p><p><b>Primary:</b> ${person(op.primary_ref)}</p><p><b>Secondary:</b> ${op.secondary_refs.length?op.secondary_refs.map(ref=>person(ref)+` <button type="button" class="btn" data-promote-ref="${esc(ref)}" data-promote-operation="${op.id}">Make primary</button>`).join(' '):'None'}</p><button type="button" class="btn" data-change-people="${op.id}">Change people</button></div>`).join(''):'<p class="ops-muted">No assignments yet.</p>';
    bindOperationForm(view.querySelector('[data-create] [data-form]'),null);
    list.innerHTML=state.operations.length?state.operations.map(op=>`<details class="ops-card" data-operation="${op.id}" ${openIds.has(op.id)?'open':''}><summary class="ops-operation-title"><span>${esc(op.name)}</span><button type="button" class="ops-edit-operation" data-edit-operation aria-label="Edit ${esc(op.name)}" title="Edit operation"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v3M7 8h6M7 12h3"/><path d="m18 10 4 4-7 7h-4v-4Z"/></svg></button></summary>
      <p><strong>Primary:</strong> ${person(op.primary_ref)}</p><p><strong>Secondary:</strong> ${op.secondary_refs.length?op.secondary_refs.map(person).join(', '):'None'}</p>
      <h4>Description</h4><p class="ops-text">${esc(op.description||'No description yet.')}</p><h4>Work instructions</h4><p class="ops-text">${esc(op.instructions||'No instructions yet.')}</p>
      <details data-edit><summary>Edit operation and responsible leaders</summary><div data-edit-form></div></details><h4>Task templates</h4>
      ${op.templates.length?op.templates.map(t=>`<div class="ops-template"><strong>${esc(t.title)}</strong><p class="ops-text">${esc(t.description)}</p><p class="ops-muted">${esc(t.schedule.kind)} · from ${esc(t.schedule.start_date)} · ${esc(t.schedule.due_time)} ${esc(state.timezone)}</p>${templateForm(op.id,t)}<button type="button" class="btn" data-remove-template="${t.id}">Remove template</button></div>`).join(''):'<p class="ops-muted">No task templates yet.</p>'}
      ${templateForm(op.id)}</details>`).join(''):'<p class="ops-muted">No operations yet. Create one to get started.</p>';
    coverage.querySelectorAll('[data-promote-ref]').forEach(button=>button.onclick=async()=>{const op=state.operations.find(o=>o.id===Number(button.dataset.promoteOperation)),next=button.dataset.promoteRef;if(!op)return;button.disabled=true;try{await api('/'+op.id,'PUT',{name:op.name,description:op.description,instructions:op.instructions,primary_ref:next,secondary_refs:op.secondary_refs.map(ref=>ref===next?op.primary_ref:ref)});await load();status.textContent='Primary and secondary leaders swapped.';}catch(e){status.textContent=e.message;button.disabled=false;}});
    coverage.querySelectorAll('[data-change-people]').forEach(button=>button.onclick=()=>{const card=list.querySelector(`[data-operation="${button.dataset.changePeople}"]`);if(!card)return;card.open=true;card.querySelector('[data-edit]').open=true;card.scrollIntoView({behavior:'smooth',block:'start'});});
    for(const card of list.querySelectorAll('[data-operation]')){
      const id=Number(card.dataset.operation),op=state.operations.find(x=>x.id===id);
      card.ontoggle=()=>{if(card.open)openIds.add(id);else openIds.delete(id);};card.querySelector('[data-edit-operation]').onclick=event=>{event.preventDefault();event.stopPropagation();card.open=true;card.querySelector('[data-edit]').open=true;card.querySelector('[data-edit-form] input').focus();};bindOperationForm(card.querySelector('[data-edit-form]'),op);
      card.querySelectorAll('[data-template-form]').forEach(form=>{form.elements.kind.onchange=()=>{form.querySelector('[data-weekdays]').hidden=form.elements.kind.value!=='weekly';};form.onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,d=new FormData(form),date=d.get('start_date'),kind=d.get('kind'),dt=new Date(date+'T12:00:00'),day=(dt.getDay()+6)%7;
        const selectedDays=d.getAll('weekdays').map(Number);if(kind==='weekly'&&!selectedDays.length){form.querySelector('[data-form-status]').textContent='Choose at least one weekday.';return;}const schedule={kind,start_date:date,due_time:d.get('due_time'),end_date:d.get('end_date')||null,interval:Number(d.get('interval')),interval_unit:d.get('interval_unit'),weekdays:kind==='weekly'?selectedDays:[],month_day:dt.getDate()};
        const button=form.querySelector('button');button.disabled=true;try{const templateId=form.dataset.templateId;await api('/'+id+'/templates'+(templateId?'/'+templateId:''),templateId?'PUT':'POST',{title:d.get('title'),description:d.get('description'),schedule});openIds.add(id);await load();status.textContent=templateId?'Task template updated.':'Task template created and assigned.';}catch(e){form.querySelector('[data-form-status]').textContent=e.message;}finally{button.disabled=false;}};});
      card.querySelectorAll('[data-remove-template]').forEach(button=>button.onclick=async()=>{if(!confirm('Remove this template and its upcoming pending assignments? Completed task history will remain.'))return;button.disabled=true;try{await api('/'+id+'/templates/'+button.dataset.removeTemplate,'DELETE');openIds.add(id);await load();status.textContent='Task template removed.';}catch(e){status.textContent=e.message;button.disabled=false;}});
    }
  }
  async function load(){if(!slug){status.textContent='Open an alliance workspace to use Operations.';return;}try{state=await api();render();}catch(e){status.textContent='Could not load operations: '+e.message;}}
  new MutationObserver(()=>{if(!view.hidden)load();}).observe(view,{attributes:true,attributeFilter:['hidden']});
  if(!view.hidden)load();
})();

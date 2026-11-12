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
    .ops-card>summary{cursor:pointer;font-weight:700;font-size:1.1rem}.ops-text{white-space:pre-wrap;overflow-wrap:anywhere}
    .ops-form{display:grid;gap:10px;margin:12px 0}.ops-form label{display:grid;gap:4px}.ops-form :is(input,textarea,select){width:100%;min-width:0}
    .ops-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:10px}.ops-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
    .ops-assignees{border:1px solid var(--stroke);border-radius:8px;padding:10px}.ops-assignees label{display:flex;gap:8px;align-items:center}.ops-assignees input{width:auto}
    .ops-template{border-top:1px solid var(--stroke);padding:10px 0}.ops-muted{color:var(--muted)}.ops-status{min-height:1.5em}
  `;document.head.append(styles);
  view.innerHTML=`<section class="panel pl-section"><div class="workspace-heading"><div class="eyebrow">Alliance leadership</div><h2>Operations</h2><p>Organize recurring responsibilities and assign task templates to your leadership team.</p></div>
    <div class="ops-actions"><button class="btn" type="button" data-view-tasks>View assigned tasks</button></div><p class="ops-status" role="status" aria-live="polite"></p><details class="ops-card" data-create><summary>+ Create operation</summary><div data-form></div></details><h3>Alliance operations</h3><div class="ops-list" data-list></div></section>`;
  const status=view.querySelector('.ops-status'),list=view.querySelector('[data-list]');
  view.querySelector('[data-view-tasks]').onclick=()=>window.openLeadershipAccount?.({tasks:'1'});
  function today(){const parts=Object.fromEntries(new Intl.DateTimeFormat('en-US',{timeZone:state.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(p=>[p.type,p.value]));return `${parts.year}-${parts.month}-${parts.day}`;}
  function options(selected){return state.leaders.map(p=>`<option value="${p.id}" ${Number(selected)===p.id?'selected':''}>${esc(p.display_name)} · ${esc(p.role)}</option>`).join('');}
  function operationForm(op={}){
    op=op||{};
    const selected=new Set(op.secondary_ids||[]);
    return `<form class="ops-form" data-operation-form><label>Operation name<input name="name" maxlength="160" required value="${esc(op.name||'')}" placeholder="Train management"></label>
      <label>Brief description<textarea name="description" maxlength="4000" rows="2">${esc(op.description||'')}</textarea></label>
      <label>Work instructions<textarea name="instructions" maxlength="20000" rows="5">${esc(op.instructions||'')}</textarea></label>
      <label>Primary person responsible<select name="primary_id" required><option value="">Select an R4 or R5</option>${options(op.primary_id)}</select></label>
      <fieldset class="ops-assignees"><legend>Secondary people responsible · up to 5</legend>${state.leaders.map(p=>`<label><input type="checkbox" name="secondary_ids" value="${p.id}" ${selected.has(p.id)?'checked':''}>${esc(p.display_name)} · ${esc(p.role)}</label>`).join('')}</fieldset>
      <div class="ops-actions"><button type="submit" class="btn primary">${op.id?'Save changes':'Create operation'}</button></div><p data-form-status role="status"></p></form>`;
  }
  function bindOperationForm(container,op){
    container.innerHTML=operationForm(op);const form=container.querySelector('form');
    form.onsubmit=async event=>{event.preventDefault();const data=new FormData(form),secondary=data.getAll('secondary_ids').map(Number),primary=Number(data.get('primary_id')),notice=form.querySelector('[data-form-status]');
      if(secondary.length>5||secondary.includes(primary)){notice.textContent='Choose up to five different secondary leaders; the primary cannot also be secondary.';return;}
      const button=form.querySelector('button[type=submit]');button.disabled=true;
      try{await api(op?.id?'/'+op.id:'',op?.id?'PUT':'POST',{name:data.get('name'),description:data.get('description'),instructions:data.get('instructions'),primary_id:primary,secondary_ids:secondary});
        if(op?.id)openIds.add(op.id);else view.querySelector('[data-create]').open=false;await load();status.textContent=op?.id?'Operation updated.':'Operation created.';
      }catch(e){notice.textContent=e.message;}finally{button.disabled=false;}
    };
  }
  function templateForm(oid,t={}){const schedule=t.schedule||{};return `<details><summary>${t.id?'Edit task template':'+ Add task template'}</summary><form class="ops-form" data-template-form="${oid}" data-template-id="${t.id||''}">
    <label>Task name<input name="title" required maxlength="160" value="${esc(t.title||'')}" placeholder="Select the drivers"></label><label>Task details<textarea name="description" rows="2" maxlength="1000">${esc(t.description||'')}</textarea></label>
    <div class="ops-grid"><label>Repeat<select name="kind">${[['daily','Daily'],['weekly','Weekly'],['monthly','Monthly'],['once','One time'],['interval','Custom interval']].map(([v,label])=>`<option value="${v}" ${schedule.kind===v?'selected':''}>${label}</option>`).join('')}</select></label>
    <label>Starts on<input type="date" name="start_date" value="${esc(schedule.start_date||today())}" required></label><label>Due time · ${esc(state.timezone)}<input type="time" name="due_time" value="${esc(schedule.due_time||'18:00')}" required></label>
    <label>End date · optional<input type="date" name="end_date" value="${esc(schedule.end_date||'')}"></label><label>Custom interval<input type="number" name="interval" min="1" max="365" value="${esc(schedule.interval||1)}"></label><label>Interval unit<select name="interval_unit">${[['day','Days'],['week','Weeks'],['month','Months']].map(([v,label])=>`<option value="${v}" ${schedule.interval_unit===v?'selected':''}>${label}</option>`).join('')}</select></label></div>
    <p class="ops-muted">The task is assigned separately to the primary and every secondary leader. Weekly and monthly schedules follow the start date.</p><button class="btn primary">${t.id?'Save task template':'Add task template'}</button><p data-form-status role="status"></p></form></details>`;}
  function render(){
    const leaders=new Map(state.leaders.map(p=>[p.id,p.display_name]));
    bindOperationForm(view.querySelector('[data-create] [data-form]'),null);
    list.innerHTML=state.operations.length?state.operations.map(op=>`<details class="ops-card" data-operation="${op.id}" ${openIds.has(op.id)?'open':''}><summary>${esc(op.name)}</summary>
      <p><strong>Primary:</strong> ${esc(leaders.get(op.primary_id)||'Former leader')}</p><p><strong>Secondary:</strong> ${op.secondary_ids.length?op.secondary_ids.map(id=>esc(leaders.get(id)||'Former leader')).join(', '):'None'}</p>
      <h4>Description</h4><p class="ops-text">${esc(op.description||'No description yet.')}</p><h4>Work instructions</h4><p class="ops-text">${esc(op.instructions||'No instructions yet.')}</p>
      <details data-edit><summary>Edit operation and responsible leaders</summary><div data-edit-form></div></details><h4>Task templates</h4>
      ${op.templates.length?op.templates.map(t=>`<div class="ops-template"><strong>${esc(t.title)}</strong><p class="ops-text">${esc(t.description)}</p><p class="ops-muted">${esc(t.schedule.kind)} · from ${esc(t.schedule.start_date)} · ${esc(t.schedule.due_time)} ${esc(state.timezone)}</p>${templateForm(op.id,t)}<button type="button" class="btn" data-remove-template="${t.id}">Remove template</button></div>`).join(''):'<p class="ops-muted">No task templates yet.</p>'}
      ${templateForm(op.id)}</details>`).join(''):'<p class="ops-muted">No operations yet. Create one to get started.</p>';
    for(const card of list.querySelectorAll('[data-operation]')){
      const id=Number(card.dataset.operation),op=state.operations.find(x=>x.id===id);
      card.ontoggle=()=>{if(card.open)openIds.add(id);else openIds.delete(id);};bindOperationForm(card.querySelector('[data-edit-form]'),op);
      card.querySelectorAll('[data-template-form]').forEach(form=>form.onsubmit=async event=>{event.preventDefault();const form=event.currentTarget,d=new FormData(form),date=d.get('start_date'),kind=d.get('kind'),dt=new Date(date+'T12:00:00'),day=(dt.getDay()+6)%7;
        const schedule={kind,start_date:date,due_time:d.get('due_time'),end_date:d.get('end_date')||null,interval:Number(d.get('interval')),interval_unit:d.get('interval_unit'),weekdays:kind==='weekly'?[day]:[],month_day:dt.getDate()};
        const button=form.querySelector('button');button.disabled=true;try{const templateId=form.dataset.templateId;await api('/'+id+'/templates'+(templateId?'/'+templateId:''),templateId?'PUT':'POST',{title:d.get('title'),description:d.get('description'),schedule});openIds.add(id);await load();status.textContent=templateId?'Task template updated.':'Task template created and assigned.';}catch(e){form.querySelector('[data-form-status]').textContent=e.message;}finally{button.disabled=false;}});
      card.querySelectorAll('[data-remove-template]').forEach(button=>button.onclick=async()=>{if(!confirm('Remove this template and its upcoming pending assignments? Completed task history will remain.'))return;button.disabled=true;try{await api('/'+id+'/templates/'+button.dataset.removeTemplate,'DELETE');openIds.add(id);await load();status.textContent='Task template removed.';}catch(e){status.textContent=e.message;button.disabled=false;}});
    }
  }
  async function load(){if(!slug){status.textContent='Open an alliance workspace to use Operations.';return;}try{state=await api();render();}catch(e){status.textContent='Could not load operations: '+e.message;}}
  new MutationObserver(()=>{if(!view.hidden)load();}).observe(view,{attributes:true,attributeFilter:['hidden']});
  if(!view.hidden)load();
})();

(() => {
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const localDate=d=>[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');
  const num=value=>BigInt(value).toLocaleString();
  const zones=['CET','CEST','UTC','GMT','BST','EST','EDT','CST','CDT','MST','MDT','PST','PDT','IST','JST','AEST','AEDT'];
  const defaultTarget=7200000;
  const view=$('trainView');
  const styles=document.createElement('style');
  styles.textContent=`
    .train-controls{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin:10px 0}
    .train-controls label,.train-field{display:grid;grid-template-columns:minmax(0,1fr);gap:5px;min-width:0;max-width:100%}
    .train-field select{width:100%;min-width:0}
    #trainView input,#trainView select,#trainView textarea,#trainAdmin input,#trainAdmin select,#trainHistoryDialog input,#trainHistoryDialog select{color:var(--text);background:var(--panel2);border:1px solid var(--stroke);border-radius:7px;padding:8px;font:inherit;max-width:100%}
    .train-layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr);align-items:start;gap:20px}
    .train-card{min-width:0;border:1px solid var(--stroke);border-radius:12px;padding:14px}
    .train-card h3,.train-card h4{margin:0 0 6px}
    .train-picks{position:sticky;top:12px}
    .train-picks label,.train-history-form label{margin-bottom:10px}
    #trainView .pl-table{min-width:0}
    #trainView .pl-table :is(td,th){padding:7px;white-space:normal;overflow-wrap:anywhere}
    .train-badge{display:block;margin-top:4px;font-size:.85em;color:var(--muted)}
    .train-nominee-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:end;margin-top:10px}
    .train-nominee-actions label{min-width:0}
    .train-step{display:inline-grid;place-items:center;width:1.65em;height:1.65em;margin-right:.35em;border:1px solid var(--stroke);border-radius:50%;font-size:.8em}
    .train-summary,.admin-tool-summary{cursor:pointer;font-weight:700}
    .admin-tool-summary+form,.admin-tool-summary+.train-controls{margin-top:12px}
    .train-hint:empty,#trainStatus:empty,#trainSaveStatus:empty{display:none}
    #trainHistoryDialog{width:min(720px,96vw);max-height:90vh;overflow:auto}
    #trainHistoryDialog .pl-table{min-width:0}
    #trainHistoryDialog .pl-table :is(td,th){padding:8px;white-space:normal;overflow-wrap:anywhere}
    #trainHistoryDialog [hidden]{display:none!important}
    .train-history-form{margin-top:12px}
    @media(max-width:900px){
      .train-layout{grid-template-columns:1fr}
      .train-picks{position:static}
    }
    @media(max-width:560px){
      .train-card{padding:10px}
      #trainView .pl-table :is(td,th){padding:6px}
      #trainView .pl-table button{white-space:normal;font-size:.8em;padding:6px}
    }
    #trainView textarea{width:100%;min-height:8em}
    #trainTemplateAdmin :is(input,select,textarea){width:100%;color:var(--text);background:var(--panel2);border:1px solid var(--stroke);border-radius:7px;padding:8px;font:inherit}
    #trainTemplateAdmin textarea{min-height:16em;resize:vertical}
    #trainTemplateAdmin form .train-field{margin:10px 0}
    .train-wheel-wrap{width:min(100%,330px);position:relative;margin:15px auto;text-align:center}
    #trainWheel{display:block;width:100%;height:auto;border-radius:50%;border:4px solid var(--stroke);background:var(--panel2)}
    .train-pointer{position:absolute;z-index:1;left:50%;top:-12px;transform:translateX(-50%);color:var(--amber);font-size:25px}
    .train-import-row{border:1px solid var(--stroke);border-radius:9px;padding:10px;margin:8px 0}
    .train-import-row select{display:block}
    .train-import-list{max-height:560px;overflow:auto}
    .train-hint{color:var(--muted)}
    .train-controls [hidden]{display:none}
    #trainView [hidden],#trainAdmin [hidden]{display:none!important}
  `;
  document.head.append(styles);
  view.innerHTML=`
    <section class="panel pl-section">
      <div class="pl-title-row"><div><div class="eyebrow">Alliance awards</div><h2>Train Manager</h2></div><button class="btn" type="button" id="trainEditHistory" aria-haspopup="dialog">Edit train history</button></div>
      <div class="train-controls">
        <label>Assignment date<input id="trainDay" type="date" required></label>
        <fieldset class="train-controls" id="trainGoalControls"><legend>Weekly goal</legend><label><span><input name="trainGoal" type="radio" value="Push" checked> Push</span></label><label><span><input name="trainGoal" type="radio" value="Save"> Save</span></label></fieldset>
        <label id="trainTargetLabel" hidden>Save goal<input id="trainTarget" type="number" min="1" max="9007199254740991" value="7200000"></label>
      </div>
      <p id="trainStatus" role="status" aria-live="polite"></p>
      <p class="train-hint" id="trainContext"></p>
      <div id="trainBoard" hidden>
        <div id="trainWorkflow" class="train-layout">
          <section class="train-card" aria-labelledby="trainNomineeTitle">
            <h3 id="trainNomineeTitle"><span class="train-step" aria-hidden="true">1</span> Choose a Conductor</h3>
            <p class="train-hint">Pick a nominee or use the Conductor menu. Checked nominees go on the VIP wheel once you pick a Conductor.</p>
            <div class="pl-table-wrap"><table class="pl-table"><thead><tr><th scope="col">Include</th><th scope="col">Player</th><th scope="col">VS points</th><th scope="col">Assign</th></tr></thead><tbody id="trainRows"></tbody></table></div>
            <div class="train-nominee-actions"><button id="trainMore" type="button" class="btn smallbtn" hidden>Show all nominees</button><label class="train-field">Add a player<select id="trainManual"></select></label><button class="btn" type="button" id="trainAppend">Add nominee</button></div>
          </section>
          <section class="train-card train-picks" aria-labelledby="trainPickTitle">
            <h3 id="trainPickTitle"><span class="train-step" aria-hidden="true">2</span> Pick a VIP</h3>
            <label class="train-field">Conductor<select id="trainConductor"></select></label>
            <p class="train-hint" id="trainWheelStatus" role="status" aria-live="polite"></p>
            <div class="train-wheel-wrap" id="trainWheelWrap" hidden><span class="train-pointer" aria-hidden="true">▼</span><canvas id="trainWheel" width="480" height="480" aria-label="VIP randomizer wheel"></canvas></div>
            <div class="train-controls"><button class="btn" type="button" id="trainSpin" disabled>Spin for VIP</button></div>
            <label class="train-field">VIP<select id="trainVip"></select></label>
            <button class="btn primary" type="button" id="trainSave">Save train assignment</button>
            <p id="trainSaveStatus" role="status" aria-live="polite"></p>
          </section>
        </div>
        <section class="train-card" id="trainSavedAssignment" hidden>
          <h3>Saved train assignment</h3>
          <p id="trainSavedNames"></p>
          <div class="train-controls">
            <button class="btn primary" type="button" id="trainOpenEmail">Generate Alliance Email</button>
            <button class="btn" type="button" id="trainRedo" hidden>Clear today’s picks and redo</button>
          </div>
          <p id="trainRedoStatus" role="status" aria-live="polite" hidden></p>
        </section>
      </div>
    </section>
    <section class="panel pl-section" id="trainEmailEditor" hidden>
      <h3>Generate Alliance Email</h3>
      <label class="train-field">Email template<select id="trainEmailTemplate" disabled><option value="">Random template</option></select></label>
      <form id="trainEventForm" class="train-controls">
        <label>Event name<input id="trainEventName" list="trainEventNames" autocomplete="off" maxlength="80" required><datalist id="trainEventNames"></datalist></label>
        <label>Event time<input id="trainEventTime" type="time" required></label>
        <label>Timezone<select id="trainZone">${zones.map(z=>'<option>'+z+'</option>').join('')}</select></label>
        <button class="btn" type="submit">Add event</button>
      </form>
      <ul id="trainEventList"></ul>
      <label class="train-field">Admin notes<textarea id="trainNotes" maxlength="4000"></textarea></label>
      <div class="train-controls"><label>Author<select id="trainAuthor"></select></label><button class="btn primary" type="button" id="trainGenerate" disabled>Generate Email</button></div>
      <label id="trainDraftField" class="train-field" hidden>Email draft<textarea id="trainDraft" maxlength="12000"></textarea></label>
      <div id="trainDraftActions" class="train-controls" hidden><button class="btn" type="button" id="trainCopy">Copy email</button><button class="btn primary" type="button" id="trainSaveEmail">Save email</button></div>
      <p id="trainEmailStatus" role="status" aria-live="polite"></p>
    </section>
    <details class="panel pl-section"><summary class="train-summary">Saved alliance emails</summary>
      <div class="train-controls"><button class="btn" type="button" id="trainOlderEmail">‹ Older</button><strong id="trainEmailDate"></strong><button class="btn" type="button" id="trainNewerEmail">Newer ›</button></div>
      <label id="trainSavedEmailField" class="train-field" hidden>Saved email<textarea id="trainSavedEmail" readonly></textarea></label>
      <button id="trainCopySaved" class="btn" type="button" hidden>Copy saved email</button>
      <p id="trainArchiveStatus" role="status" aria-live="polite"></p>
    </details>`;
  const historyDialog=document.createElement('dialog');
  historyDialog.id='trainHistoryDialog';
  historyDialog.setAttribute('aria-labelledby','trainHistoryTitle');
  historyDialog.innerHTML=`
    <div class="pl-title-row"><h3 id="trainHistoryTitle">Edit train history</h3><form method="dialog"><button class="btn" type="submit">Close</button></form></div>
    <p id="trainHistoryStatus" role="status" aria-live="polite"></p>
    <div id="trainHistoryList">
      <div class="pl-table-wrap"><table class="pl-table"><thead><tr><th scope="col">Date</th><th scope="col">Conductor</th><th scope="col">VIP</th><th scope="col">Edit</th></tr></thead><tbody id="trainHistory"></tbody></table></div>
      <button id="trainOlderHistory" class="btn" type="button" hidden>Load older assignments</button>
    </div>
    <form class="train-history-form" id="trainHistoryForm" hidden>
      <h4 id="trainHistoryEditTitle"></h4>
      <label class="train-field">Assignment date<input name="day" type="date" required></label>
      <label class="train-field">Conductor<select name="conductor_id" required></select></label>
      <label class="train-field">VIP<select name="vip_id" required></select></label>
      <p class="train-hint">Changes update the assignment record. Review any saved alliance email separately.</p>
      <button class="btn primary" type="submit">Save changes</button>
      <button class="btn" type="button" id="trainHistoryCancel">Back to history</button>
    </form>`;
  document.body.append(historyDialog);
  $('trainTemplateAdmin').insertAdjacentHTML('beforeend',`
    <div class="train-controls">
      <label>Saved templates<select id="trainTemplateList" disabled></select></label>
      <button class="btn" type="button" id="trainTemplateNew" disabled>Add template</button>
    </div>
    <form id="trainTemplateForm" hidden>
      <label class="train-field">Template name<input name="name" maxlength="80" required></label>
      <label class="train-field">Email body<textarea name="body" maxlength="6000" required aria-describedby="trainTemplateFields"></textarea></label>
      <p id="trainTemplateFields" class="train-hint">Keep these fields in the email body so the app can fill in each message: <code>{alliance}</code>, <code>{conductor}</code>, <code>{vip}</code>, <code>{reasons}</code>, <code>{events}</code>, <code>{notes}</code>, and <code>{author}</code>. Empty sections are skipped.</p>
      <div class="train-controls">
        <button class="btn primary" id="trainTemplateSave" type="submit">Save template</button>
        <button class="btn danger" id="trainTemplateDelete" type="button">Delete template</button>
      </div>
    </form>
    <p id="trainTemplateStatus" role="status" aria-live="polite"></p>`);
  $('trainAdmin').innerHTML=`
    <details class="panel pl-section" id="trainSettingsSection">
    <summary class="admin-tool-summary">Train Manager settings</summary>
    <form id="trainSettingsForm" class="train-controls">
      <label>Alliance name<input name="alliance_name" maxlength="80" required></label>
      <label>Cooldown (weeks)<input name="cooldown_weeks" type="number" min="0" max="52" required></label>
      <label>Wheel spin (seconds)<input name="spin_seconds" type="number" min="1" max="30" required></label>
      <button type="submit" class="btn primary">Save train settings</button>
    </form>
    <p id="trainSettingsStatus" role="status"></p>
    </details>
    <details class="panel pl-section">
    <summary class="admin-tool-summary">Train assignment history</summary>
    <div class="train-controls"><button class="btn" type="button" id="trainAdminHistory" aria-haspopup="dialog">Edit train history</button></div>
    </details>
    <details class="panel pl-section">
    <summary class="admin-tool-summary">Import historical train assignments</summary>
    <p>Use a CSV with Date, Conductor Player, and VIP Player columns. Review the name matches and save each row individually.</p>
    <form id="trainImportForm" class="train-controls"><label>Assignment CSV<input name="file" type="file" accept=".csv,text/csv" required></label><button class="btn" type="submit">Review CSV</button></form>
    <p id="trainImportStatus" role="status"></p><div id="trainImportRows" class="train-import-list"></div>
    <div class="train-controls" id="trainImportPages" hidden><button type="button" class="btn" id="trainImportPrevious">‹ Previous rows</button>
    <span id="trainImportPage"></span><button type="button" class="btn" id="trainImportNext">Next rows ›</button></div>
    </details>`;

  async function api(path,options){
    const response=await fetch('/api/train'+path,{cache:'no-store',...options});
    const result=await response.json();
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Check the form and try again.');
    return result;
  }
  const json=(method,data)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const message=(id,text)=>{$(id).textContent=text;};
  const templateUrl='/email-templates';
  const newTemplateBody='Good morning, {alliance}! 🚂\n\n{conductor} is today’s Conductor and {vip} is our VIP.\n\n{reasons}\n\n{events}\n\n{notes}\n\nBest regards,\n{author}';
  const templateForm=$('trainTemplateForm'),templateList=$('trainTemplateList');
  let adminTemplates=[],emailTemplates=[],templateBusy=false,templateRequest=0;
  function showTemplate(){
    const template=adminTemplates.find(entry=>entry.id===templateList.value);
    templateForm.hidden=false;
    templateForm.elements.name.value=template?.name||'';
    templateForm.elements.body.value=template?.body||newTemplateBody;
    $('trainTemplateDelete').hidden=!template;
  }
  function setTemplateBusy(busy){
    templateBusy=busy;
    for(const id of ['trainTemplateList','trainTemplateNew','trainTemplateSave','trainTemplateDelete'])$(id).disabled=busy;
    templateForm.elements.name.disabled=busy;
    templateForm.elements.body.disabled=busy;
  }
  async function refreshTemplateAdmin(selected){
    const {templates}=await api(templateUrl);
    adminTemplates=templates;
    templateList.innerHTML='<option value="">New template</option>'+
      templates.map(entry=>'<option value="'+esc(entry.id)+'">'+esc(entry.name)+'</option>').join('');
    templateList.value=selected??templates[0]?.id??'';
    showTemplate();
  }
  async function templateAction(action,success){
    if(templateBusy)return;
    setTemplateBusy(true);
    message('trainTemplateStatus','');
    try{
      await action();
      if(success)message('trainTemplateStatus',success);
    }catch(error){message('trainTemplateStatus',error.message);}
    finally{setTemplateBusy(false);}
  }
  $('trainTemplateAdmin').addEventListener('toggle',()=>{
    if($('trainTemplateAdmin').open)templateAction(()=>refreshTemplateAdmin(templateForm.hidden?undefined:templateList.value));
  });
  templateList.onchange=()=>{showTemplate();message('trainTemplateStatus','');};
  $('trainTemplateNew').onclick=()=>{
    templateList.value='';
    showTemplate();
    message('trainTemplateStatus','');
    templateForm.elements.name.focus();
  };
  templateForm.onsubmit=event=>{
    event.preventDefault();
    const key=templateList.value,body={name:templateForm.elements.name.value,body:templateForm.elements.body.value};
    templateAction(async()=>{
      const {id}=await api(key?templateUrl+'/'+encodeURIComponent(key):templateUrl,json(key?'PUT':'POST',body));
      await refreshTemplateAdmin(id);
    },'Template saved.');
  };
  $('trainTemplateDelete').onclick=()=>{
    const template=adminTemplates.find(entry=>entry.id===templateList.value);
    if(!template||!window.confirm('Delete the “'+template.name+'” email template?'))return;
    templateAction(async()=>{
      await api(templateUrl+'/'+encodeURIComponent(template.id),{method:'DELETE'});
      await refreshTemplateAdmin();
    },'Template deleted.');
  };
  async function loadEmailTemplates(){
    const request=++templateRequest,select=$('trainEmailTemplate'),selected=select.value;
    select.disabled=true;$('trainGenerate').disabled=true;
    try{
      const {templates}=await api(templateUrl);
      if(request!==templateRequest)return;
      emailTemplates=templates;
      select.innerHTML='<option value="">Random template</option>'+
        templates.map(entry=>'<option value="'+esc(entry.id)+'">'+esc(entry.name)+'</option>').join('');
      select.value=templates.some(entry=>entry.id===selected)?selected:'';
      if(!templates.length)message('trainEmailStatus','Add an alliance email template in Admin to generate an email.');
      select.disabled=!templates.length;$('trainGenerate').disabled=!templates.length;
    }catch(error){if(request===templateRequest)message('trainEmailStatus',error.message);}
  }
  const goal=()=>view.querySelector('[name="trainGoal"]:checked').value;
  const day=()=>$('trainDay').value;
  const target=()=>goal()==='Save'?Number($('trainTarget').value):defaultTarget;
  const option=(p,selected,disabled=false)=>'<option value="'+p.id+'" '+(p.id===selected?'selected ':'')+(disabled?'disabled':'')+'>'+esc(p.name+(p.blocked?' · '+p.blocked:''))+'</option>';
  let board,request=0,loading=false,saving=false,manual=[],excluded=new Set(),showAll=false,spin=false,rotation=0,wheel=[],wheelWinner=0;
  let events=[],emailRows=[],emailIndex=0,historyRows=[],historyPlayers=[],editingHistory,historyRequest=0;
  const getPlayer=id=>board?.players.find(p=>p.id===id);
  const conductor=()=>Number($('trainConductor').value);
  const vip=()=>Number($('trainVip').value);
  $('trainDay').value=localDate(new Date());
  const weekKey=()=>{
    const start=new Date(day()+'T12:00:00');start.setDate(start.getDate()-(start.getDay()+6)%7);
    return 'lastwar.trainGoal.'+localDate(start);
  };
  function loadGoal(){
    let saved;
    try{saved=JSON.parse(localStorage.getItem(weekKey()));}catch{}
    const mode=saved?.goal==='Save'?'Save':'Push';
    view.querySelector('[name="trainGoal"][value="'+mode+'"]').checked=true;
    $('trainTarget').value=saved?.target||defaultTarget;
    $('trainTargetLabel').hidden=mode!=='Save';
  }
  function saveGoal(){
    if(!day()||!Number.isSafeInteger(target())||target()<1)return;
    try{localStorage.setItem(weekKey(),JSON.stringify({goal:goal(),target:Number($('trainTarget').value)||defaultTarget}));}catch{}
  }
  loadGoal();

  function nomineeIds(){return [...new Set([...(showAll?board.nominees:board.nominees.slice(0,20)),...manual])];}
  function syncWheel(ids){
    const next=ids.filter(id=>!excluded.has(id));
    if(next.length===wheel.length&&next.every((id,i)=>id===wheel[i]))return;
    wheel=next;
    rotation=0;
    const canvas=$('trainWheel');
    canvas.style.transition='none';
    canvas.style.transform='rotate(0deg)';
    drawWheel();
  }
  function render(){
    const assigned=board.assignment;
    const oldConductor=conductor(),oldVip=vip();
    const ids=nomineeIds();
    const choices=board.players.filter(p=>!p.blocked&&!excluded.has(p.id));
    const selectedConductor=assigned?.conductor_id||choices.find(p=>p.id===oldConductor)?.id||0;
    const remaining=ids.filter(id=>id!==selectedConductor);
    $('trainRows').innerHTML=remaining.map(id=>{
      const p=getPlayer(id);
      const birthday=p.birthday_offset>=7?' <span class="train-badge">Upcoming birthday '+esc(p.birthday)+'</span>':'';
      return '<tr><td><input type="checkbox" data-train-include="'+id+'" aria-label="Include '+esc(p.name)+'" '+(excluded.has(id)?'':'checked ')+(assigned||spin?'disabled':'')+'></td>'+
        '<th scope="row">'+esc(p.name)+'<span class="train-badge">'+esc(p.reason)+'</span>'+birthday+'</th><td>'+(p.points==null?'—':num(p.points))+'</td>'+
        '<td><button class="btn smallbtn" type="button" data-train-conductor="'+id+'" '+(assigned||excluded.has(id)||spin?'disabled':'')+'>Assign as Conductor</button></td></tr>';
    }).join('')||'<tr><td colspan="4">No remaining nominees. You can still choose an eligible player from the Conductor menu.</td></tr>';
    $('trainMore').hidden=board.nominees.length<=20;
    $('trainMore').textContent=showAll?'Show top 20':'Show all '+board.nominees.length+' nominees';
    $('trainManual').innerHTML='<option value="">Choose player…</option>'+board.players.map(p=>option(p,0,!!p.blocked||ids.includes(p.id))).join('');
    for(const [id,chosen,other] of [['trainConductor',selectedConductor,0],['trainVip',oldVip,selectedConductor]]){
      const select=$(id);
      select.innerHTML='<option value="">Choose player…</option>'+
        (assigned?'':choices.filter(p=>p.id!==other).map(p=>option(p,chosen)).join(''));
      select.disabled=!!assigned||spin||(id==='trainVip'&&!selectedConductor);
    }
    if(vip()!==wheelWinner)wheelWinner=0;
    syncWheel(assigned?[]:remaining);
    $('trainWheelWrap').hidden=!selectedConductor||!wheel.length;
    if(!spin){
      message('trainWheelStatus',!selectedConductor?'Choose a Conductor to enable the VIP wheel.':wheelWinner?
        getPlayer(wheelWinner).name+' selected as VIP.':wheel.length?
        wheel.length+' on the VIP wheel. You can also choose a VIP below.':'No checked nominees on the wheel. Choose a VIP below or check a nominee.');
      $('trainSpin').textContent=wheelWinner?'Respin for VIP':'Spin for VIP';
    }
    $('trainWorkflow').hidden=!!assigned;
    $('trainSavedAssignment').hidden=!assigned;
    $('trainRedo').hidden=assigned?.assignment_date!==localDate(new Date());
    $('trainGoalControls').disabled=!!assigned;
    $('trainTarget').disabled=!!assigned||spin;
    if(assigned)message('trainSavedNames','Conductor · '+assigned.conductor_name+'   |   VIP · '+assigned.vip_name);
    $('trainManual').disabled=!!assigned||spin;
    $('trainAppend').disabled=true;
    $('trainSpin').disabled=!!assigned||spin||!conductor()||!wheel.length;
    $('trainSave').disabled=!!assigned||spin||saving||!conductor()||!vip();
  }

  async function loadBoard(reset=false){
    const current=++request;
    if(!day()||!Number.isSafeInteger(target())||target()<1){
      loading=false;$('trainBoard').hidden=true;
      message('trainStatus','Choose a date and a valid save goal.');return;
    }
    loading=true;message('trainStatus','Loading nominees…');$('trainBoard').hidden=true;
    try{
      const result=await api('/board?'+new URLSearchParams({day:day(),goal:goal(),target:target()}));
      if(current!==request)return;
      board=result;
      if(reset){manual=[];excluded.clear();showAll=false;$('trainConductor').value='';$('trainVip').value='';}
      else manual=manual.filter(id=>getPlayer(id)&&!getPlayer(id).blocked);
      const goalHint=goal()==='Push'?'Push ranks the highest scores.':'Save prioritizes exact matches, then scores closest above the goal.';
      message('trainContext',board.assignment?'':goalHint+' VS scores from '+board.score_date+'. '+board.settings.cooldown_weeks+'-week cooldown.');
      message('trainStatus','');
      $('trainBoard').hidden=false;render();
    }catch(error){if(current===request)message('trainStatus',error.message);}
    finally{if(current===request)loading=false;}
  }

  function selectConductor(id){
    if(id&&(!getPlayer(id)||getPlayer(id).blocked||excluded.has(id)))return;
    if(id&&!board.nominees.slice(0,20).includes(id)&&!manual.includes(id))manual.push(id);
    $('trainConductor').value=id||'';
    render();
  }
  $('trainRows').onclick=event=>{
    const button=event.target.closest('[data-train-conductor]');
    if(button){
      selectConductor(Number(button.dataset.trainConductor));
      $('trainConductor').focus();
    }
  };
  $('trainRows').onchange=event=>{
    const id=Number(event.target.dataset.trainInclude);
    if(!id||spin)return;
    if(event.target.checked)excluded.delete(id);
    else excluded.add(id);
    render();
    $('trainRows').querySelector('[data-train-include="'+id+'"]').focus();
  };
  $('trainMore').onclick=()=>{showAll=!showAll;render();};
  $('trainAppend').onclick=()=>{
    const id=Number($('trainManual').value);
    if(id&&!getPlayer(id).blocked){manual.push(id);render();}
  };
  $('trainManual').onchange=()=>{$('trainAppend').disabled=!$('trainManual').value;};
  $('trainConductor').onchange=()=>selectConductor(conductor());
  $('trainVip').onchange=()=>{wheelWinner=0;render();};
  function resetEmailEditor(){
    $('trainEmailEditor').hidden=true;events=[];$('trainNotes').value='';$('trainDraft').value='';
    $('trainDraftField').hidden=true;$('trainDraftActions').hidden=true;
    message('trainSaveStatus','');
    $('trainRedoStatus').hidden=true;
  }
  $('trainDay').onchange=()=>{
    resetEmailEditor();
    if(day())loadGoal();
    loadBoard(true);
  };
  view.querySelectorAll('[name="trainGoal"]').forEach(input=>input.onchange=()=>{
    $('trainTargetLabel').hidden=goal()!=='Save';saveGoal();loadBoard(true);
  });
  $('trainTarget').onchange=()=>{saveGoal();loadBoard(true);};

  function drawWheel(){
    const canvas=$('trainWheel'),context=canvas.getContext('2d');
    if(!context)return;
    const center=canvas.width/2;
    context.clearRect(0,0,canvas.width,canvas.height);
    if(!wheel.length)return;
    const slice=2*Math.PI/wheel.length;
    for(const [i,id] of wheel.entries()){
      context.beginPath();context.moveTo(center,center);
      context.arc(center,center,center-2,-Math.PI/2+i*slice,-Math.PI/2+(i+1)*slice);
      context.fillStyle=['#6550a6','#246f79','#824262','#34568a','#72563a'][i%5];
      context.fill();
      context.save();context.translate(center,center);
      context.rotate(-Math.PI/2+(i+.5)*slice);
      context.fillStyle='#fff';context.font=(wheel.length>25?'12':'16')+'px system-ui';
      context.textAlign='right';context.fillText(getPlayer(id).name.slice(0,20),center-15,5);context.restore();
    }
  }
  $('trainSpin').onclick=()=>{
    if(spin||!conductor()||!wheel.length)return;
    const previous=vip();
    const pool=wheel.length>1?wheel.filter(id=>id!==previous):wheel;
    const sample=new Uint32Array(1),limit=2**32-(2**32%pool.length);
    do{crypto.getRandomValues(sample);}while(sample[0]>=limit);
    const winner=pool[sample[0]%pool.length];
    const angle=(360-(wheel.indexOf(winner)+.5)*360/wheel.length)%360;
    rotation+=1800+(angle-rotation%360+360)%360;
    const duration=board.settings.spin_seconds;
    spin=true;render();message('trainWheelStatus','Spinning…');
    for(const input of view.querySelectorAll('[name="trainGoal"],#trainDay,#trainTarget,[data-train-include],#trainMore'))input.disabled=true;
    const canvas=$('trainWheel');
    canvas.style.transition='transform '+duration+'s cubic-bezier(.15,.72,.17,1)';
    canvas.style.transform='rotate('+rotation+'deg)';
    setTimeout(()=>{
      spin=false;$('trainVip').value=winner;wheelWinner=winner;render();
      for(const input of view.querySelectorAll('[name="trainGoal"],#trainDay,#trainTarget,[data-train-include],#trainMore'))input.disabled=false;
    },duration*1000);
  };
  $('trainSave').onclick=async()=>{
    if(saving)return;
    saving=true;$('trainSave').disabled=true;
    const date=day();
    message('trainSaveStatus','Saving assignment…');
    try{
      await api('/assignments',json('POST',{day:date,conductor_id:conductor(),vip_id:vip(),goal:goal(),target:target(),wheel_vip:wheelWinner===vip()}));
      if(day()===date){
        await loadBoard();
        if(day()===date&&board?.assignment)$('trainOpenEmail').focus();
      }
    }catch(error){if(day()===date)message('trainSaveStatus',error.message);}
    finally{saving=false;if(board&&!loading)render();}
  };

  $('trainRedo').onclick=async()=>{
    const button=$('trainRedo'), assignment=board?.assignment;
    if(!assignment||assignment.assignment_date!==localDate(new Date()))return;
    if(!window.confirm('Clear today’s saved Conductor and VIP and choose again? Any saved alliance email will stay in the archive until you update it.'))return;
    const date=assignment.assignment_date;
    button.disabled=true;
    $('trainRedoStatus').hidden=true;
    try{
      const query=new URLSearchParams({timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC',
        conductor_id:assignment.conductor_id,vip_id:assignment.vip_id});
      await api('/assignments/'+date+'?'+query,{method:'DELETE'});
      if(day()===date){
        resetEmailEditor();
        await loadBoard(true);
        if(day()===date&&!board?.assignment){
          message('trainStatus','Today’s picks cleared. Choose a new Conductor and VIP.');
          $('trainConductor').focus();
        }
      }
    }catch(error){
      if(day()===date){
        message('trainRedoStatus',error.message);
        $('trainRedoStatus').hidden=false;
      }
    }finally{button.disabled=false;}
  };

  async function loadHistory(older=false){
    const request=++historyRequest;
    const button=$('trainOlderHistory');button.disabled=true;
    message('trainHistoryStatus','Loading assignments…');
    try{
      const before=older&&historyRows.length?'?before='+encodeURIComponent(historyRows.at(-1).assignment_date):'';
      const {rows,more,players}=await api('/history'+before);
      if(request!==historyRequest)return;
      if(players)historyPlayers=players;
      historyRows=older?[...historyRows,...rows]:rows;
      $('trainHistory').innerHTML=historyRows.map(row=>'<tr><td>'+esc(row.assignment_date)+'</td><td>'+esc(row.conductor_name)+'</td><td>'+esc(row.vip_name)+'</td>'+
        '<td><button class="btn smallbtn" type="button" data-history-date="'+esc(row.assignment_date)+'" aria-label="Edit assignment for '+esc(row.assignment_date)+'">Edit</button></td></tr>').join('')||
        '<tr><td colspan="4">No assignments saved yet.</td></tr>';
      button.hidden=!more;
      message('trainHistoryStatus','');
    }catch(error){if(request===historyRequest)message('trainHistoryStatus',error.message);}
    finally{if(request===historyRequest)button.disabled=false;}
  }
  $('trainOlderHistory').onclick=()=>loadHistory(true);
  const historyForm=$('trainHistoryForm');
  function showHistoryList(){
    historyForm.hidden=true;
    $('trainHistoryList').hidden=false;
  }
  function openHistory(){
    showHistoryList();
    $('trainHistory').replaceChildren();
    $('trainOlderHistory').hidden=true;
    historyDialog.showModal();
    loadHistory();
  }
  $('trainEditHistory').onclick=openHistory;
  $('trainAdminHistory').onclick=openHistory;
  historyDialog.addEventListener('click',event=>{
    if(event.target!==historyDialog)return;
    const bounds=historyDialog.getBoundingClientRect();
    if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)historyDialog.close();
  });
  $('trainHistory').onclick=event=>{
    const button=event.target.closest('[data-history-date]');
    if(!button)return;
    editingHistory=historyRows.find(row=>row.assignment_date===button.dataset.historyDate);
    historyForm.elements.day.value=editingHistory.assignment_date;
    for(const role of ['conductor','vip']){
      const id=editingHistory[role+'_id'];
      const players=historyPlayers.some(p=>p.id===id)?historyPlayers:
        [{id,name:editingHistory[role+'_name']+' (saved player)'},...historyPlayers];
      historyForm.elements[role+'_id'].innerHTML=players.map(p=>option(p,id)).join('');
    }
    message('trainHistoryEditTitle','Edit assignment for '+editingHistory.assignment_date);
    message('trainHistoryStatus','');
    $('trainHistoryList').hidden=true;
    historyForm.hidden=false;
    historyForm.elements.day.focus();
  };
  $('trainHistoryCancel').onclick=()=>{
    showHistoryList();
    message('trainHistoryStatus','');
    $('trainHistory').querySelector('[data-history-date="'+editingHistory.assignment_date+'"]')?.focus();
  };
  historyForm.onsubmit=async event=>{
    event.preventDefault();
    const original=editingHistory,form=historyForm.elements;
    const body={day:form.day.value,conductor_id:Number(form.conductor_id.value),vip_id:Number(form.vip_id.value),
      original_conductor_id:original.conductor_id,original_vip_id:original.vip_id};
    if(body.conductor_id===body.vip_id){message('trainHistoryStatus','Choose different players for Conductor and VIP.');return;}
    const submit=historyForm.querySelector('[type="submit"]');submit.disabled=true;
    try{
      await api('/history/'+original.assignment_date,json('PUT',body));
      showHistoryList();
      await loadHistory();
      if(!$('trainHistoryStatus').textContent)message('trainHistoryStatus','Assignment updated.');
      if(!view.hidden){
        if([original.assignment_date,body.day].includes(day())){resetEmailEditor();await loadBoard();}
        await loadEmails();
      }
    }catch(error){message('trainHistoryStatus',error.message);}
    finally{submit.disabled=false;}
  };

  function renderEvents(){
    $('trainEventList').innerHTML=events.map((event,index)=>'<li>'+esc(event.name+' · '+event.time+' '+event.zone)+
      ' <button class="btn smallbtn" type="button" data-event-remove="'+index+'">Remove</button></li>').join('');
  }
  function eventOptions(){
    $('trainEventNames').innerHTML=[...board.event_names,'Other'].map(name=>'<option value="'+esc(name)+'"></option>').join('');
  }
  $('trainOpenEmail').onclick=()=>{
    const panel=$('trainEmailEditor');panel.hidden=false;
    eventOptions();renderEvents();
    message('trainEmailStatus','');
    loadEmailTemplates();
    const author=Number($('trainAuthor').value);
    $('trainAuthor').innerHTML='<option value="">Choose an R4 or R5 player…</option>'+
      board.players.filter(p=>p.rank==='R4'||p.rank==='R5').map(p=>option({...p,blocked:''},author)).join('');
    panel.scrollIntoView({behavior:'smooth',block:'start'});
  };
  const clearOther=event=>{
    if(event.target.value.trim().toLowerCase()==='other'){event.target.value='';event.target.focus();}
  };
  $('trainEventName').oninput=clearOther;
  $('trainEventName').onchange=clearOther;
  $('trainEventForm').onsubmit=async event=>{
    event.preventDefault();
    const name=$('trainEventName').value.trim(),time=$('trainEventTime').value,zone=$('trainZone').value;
    if(!name||name.toLowerCase()==='other'){message('trainEmailStatus','Enter an event name.');$('trainEventName').focus();return;}
    try{
      if(!board.event_names.some(existing=>existing.toLowerCase()===name.toLowerCase())){
        await api('/event-names',json('POST',{name}));board.event_names.push(name);eventOptions();
      }
      events.push({name,time,zone});renderEvents();$('trainEventName').value='';$('trainEventTime').value='';$('trainEventName').focus();
      message('trainEmailStatus','Event added.');
    }catch(error){message('trainEmailStatus',error.message);}
  };
  $('trainEventList').onclick=event=>{
    const button=event.target.closest('[data-event-remove]');
    if(button){events.splice(Number(button.dataset.eventRemove),1);renderEvents();}
  };
  function wallTime(time){
    const [hour,minute]=time.split(':').map(Number);
    const total=(hour*60+minute+1410)%1440;
    return String(Math.floor(total/60)).padStart(2,'0')+':'+String(total%60).padStart(2,'0');
  }
  function selectionReason(reason,assignment){
    if(reason.startsWith('Hit the save goal exactly'))return 'hit the save goal of '+num(assignment.target)+' VS points'+
      (reason.includes('Wheel of Fortune')?' and was selected by the Wheel of Fortune':'');
    if(reason==='Birthday this week')return 'has a birthday this week';
    if(reason==='Wheel of Fortune')return 'was selected by the Wheel of Fortune';
    if(reason.startsWith('Highest VS score'))return 'had the highest VS score on the previous day'+
      (reason.includes('Wheel of Fortune')?' and was selected by the Wheel of Fortune':'');
    if(reason.startsWith('Top 10'))return 'placed in the previous day’s top 10 for VS points'+(reason.includes('Wheel of Fortune')?' and was selected by the Wheel of Fortune':'');
    if(reason.includes('above the save goal'))return 'was '+reason.replace('the save goal','the '+num(assignment.target)+' VS point save goal');
    if(reason==='Birthday this week and selected by the Wheel of Fortune')return 'has a birthday this week and was selected by the Wheel of Fortune';
    if(reason==='Manual selection')return 'was selected by alliance leadership';
    return '';
  }
  $('trainGenerate').onclick=()=>{
    const assignment=board.assignment,author=getPlayer(Number($('trainAuthor').value));
    if(!assignment||!author||!['R4','R5'].includes(author.rank)){message('trainEmailStatus','Choose an R4 or R5 author.');return;}
    const selected=$('trainEmailTemplate').value;
    const template=selected?emailTemplates.find(entry=>entry.id===selected):
      emailTemplates[Math.floor(Math.random()*emailTemplates.length)];
    if(!template){message('trainEmailStatus','Choose an available email template.');return;}
    const reasons=[];
    const c=selectionReason(assignment.conductor_reason,assignment),v=selectionReason(assignment.vip_reason,assignment);
    if(assignment.goal==='Save'&&assignment.conductor_points===assignment.target&&assignment.vip_points===assignment.target){
      reasons.push('Both hit the save goal of '+num(assignment.target)+' VS points.');
      if(c.includes('birthday'))reasons.push(assignment.conductor_name+' has a birthday this week.');
      if(v.includes('birthday'))reasons.push(assignment.vip_name+' has a birthday this week.');
      if(v.includes('Wheel of Fortune'))reasons.push('The Wheel of Fortune selected '+assignment.vip_name+' as VIP.');
    }else{
      if(c)reasons.push(assignment.conductor_name+' '+c+'.');
      if(v)reasons.push(assignment.vip_name+' '+v+'.');
    }
    const eventLines=events.map(event=>{
      let line=event.name+' is at '+event.time+' '+event.zone+'.';
      if(event.name.trim().toLowerCase()==='zombie siege')line+=' Please have troops on the wall by '+wallTime(event.time)+' '+event.zone+(event.time<'00:30'?' the previous day':'')+'.';
      return line;
    });
    const fields={alliance:board.settings.alliance_name,conductor:assignment.conductor_name,vip:assignment.vip_name,
      reasons:reasons.join(' '),events:eventLines.join('\n\n'),notes:$('trainNotes').value.trim(),author:author.name};
    const draft=template.body.split(/\n{2,}/)
      .map(paragraph=>paragraph.replace(/\{([a-zA-Z_]+)\}/g,(_,field)=>fields[field]))
      .filter(paragraph=>paragraph.trim()).join('\n\n');
    if(draft.length>12000){message('trainEmailStatus','This draft exceeds 12,000 characters. Shorten the notes or choose a shorter template.');return;}
    $('trainDraft').value=draft;$('trainDraftField').hidden=false;$('trainDraftActions').hidden=false;
    message('trainEmailStatus','Draft generated using “'+template.name+'”. You can edit, copy, or save it.');
  };
  async function copy(field,status){
    try{
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText($(field).value);
      else{$(field).focus();$(field).select();if(!document.execCommand('copy'))throw Error();}
      message(status,'Copied to clipboard.');
    }catch{message(status,'Could not copy automatically. Select and copy the text.');}
  }
  $('trainCopy').onclick=()=>copy('trainDraft','trainEmailStatus');
  $('trainSaveEmail').onclick=async()=>{
    const button=$('trainSaveEmail'), date=board.assignment.assignment_date;button.disabled=true;
    try{await api('/emails/'+date,json('PUT',{body:$('trainDraft').value}));message('trainEmailStatus','Email saved.');await loadEmails(date);}
    catch(error){message('trainEmailStatus',error.message);}
    finally{button.disabled=false;}
  };
  function renderSavedEmail(){
    const row=emailRows[emailIndex];
    $('trainEmailDate').textContent=row?row.assignment_date:'No saved emails';
    $('trainOlderEmail').disabled=!row||emailIndex>=emailRows.length-1;
    $('trainNewerEmail').disabled=!row||emailIndex<=0;
    $('trainSavedEmailField').hidden=!row;$('trainCopySaved').hidden=!row;
    $('trainSavedEmail').value=row?.body||'';
    message('trainArchiveStatus','');
  }
  async function loadEmails(preferred){
    try{
      const {rows}=await api('/emails');emailRows=rows;
      emailIndex=Math.max(0,rows.findIndex(row=>row.assignment_date===preferred));renderSavedEmail();
    }catch(error){message('trainArchiveStatus',error.message);}
  }
  $('trainOlderEmail').onclick=()=>{if(emailIndex<emailRows.length-1)emailIndex++;renderSavedEmail();};
  $('trainNewerEmail').onclick=()=>{if(emailIndex)emailIndex--;renderSavedEmail();};
  $('trainCopySaved').onclick=()=>copy('trainSavedEmail','trainArchiveStatus');

  const settingsForm=$('trainSettingsForm');
  async function loadSettings(){
    try{
      const settings=await api('/settings');
      for(const [key,value] of Object.entries(settings))settingsForm.elements[key].value=value;
    }catch(error){message('trainSettingsStatus',error.message);}
  }
  settingsForm.onsubmit=async event=>{
    event.preventDefault();
    try{
      await api('/settings',json('PUT',{alliance_name:settingsForm.elements.alliance_name.value,
        cooldown_weeks:Number(settingsForm.elements.cooldown_weeks.value),spin_seconds:Number(settingsForm.elements.spin_seconds.value)}));
      message('trainSettingsStatus','Train settings saved.');
    }catch(error){message('trainSettingsStatus',error.message);}
  };
  const importPageSize=15;
  let importRows=[],importPlayers=[],importPage=0;
  function renderImportPage(){
    const list=$('trainImportRows');list.replaceChildren();
    const start=importPage*importPageSize,end=Math.min(start+importPageSize,importRows.length);
    $('trainImportPages').hidden=importRows.length<=importPageSize;
    $('trainImportPrevious').disabled=!importPage;
    $('trainImportNext').disabled=end>=importRows.length;
    message('trainImportPage',(start+1)+'–'+end+' of '+importRows.length);
    for(const row of importRows.slice(start,end)){
      const form=document.createElement('form');form.className='train-import-row';
      if(row.error){form.textContent='CSV row '+row.line+' · '+row.error;list.append(form);continue;}
      form.innerHTML='<strong>'+esc(row.day)+'</strong><div class="train-controls">'+
        ['conductor','vip'].map(role=>'<label>'+esc((role==='conductor'?'Conductor':'VIP')+' in CSV · '+row[role])+
        '<select name="'+role+'" required '+(row.saved||row.saving?'disabled':'')+'><option value="">Choose player…</option>'+
        importPlayers.map(p=>option(p,row[role+'_id'])).join('')+'</select></label>').join('')+
        '<button class="btn primary" type="submit" '+(row.saved||row.saving?'disabled':'')+'>'+
        (row.saved?'Saved':row.saving?'Saving…':'Save row')+'</button></div><small role="status">'+esc(row.status||'')+'</small>';
      form.onchange=event=>{row[event.target.name+'_id']=Number(event.target.value);};
      form.onsubmit=async event=>{
        event.preventDefault();
        if(row.saved||row.saving)return;
        row.saving=true;renderImportPage();
        try{
          await api('/history',json('POST',{day:row.day,conductor_id:row.conductor_id,vip_id:row.vip_id}));
          row.saved=true;row.status='Assignment added to history.';
        }catch(error){row.status=error.message;}
        finally{row.saving=false;renderImportPage();}
      };
      list.append(form);
    }
  }
  $('trainImportPrevious').onclick=()=>{if(importPage){importPage--;renderImportPage();}};
  $('trainImportNext').onclick=()=>{if((importPage+1)*importPageSize<importRows.length){importPage++;renderImportPage();}};
  $('trainImportForm').onsubmit=async event=>{
    event.preventDefault();
    const button=event.target.querySelector('button');button.disabled=true;
    try{
      const {rows,players}=await api('/import-preview',{method:'POST',body:new FormData(event.target)});
      importRows=rows;importPlayers=players;importPage=0;
      renderImportPage();
      message('trainImportStatus',rows.length+' CSV rows ready to review.');
    }catch(error){message('trainImportStatus',error.message);}
    finally{button.disabled=false;}
  };
  function openTrain(){if(!view.hidden&&!loading&&!spin){loadBoard();loadEmails();}}
  new MutationObserver(openTrain).observe(view,{attributes:true,attributeFilter:['hidden']});
  const settingsSection=$('trainSettingsSection');
  settingsSection.addEventListener('toggle',()=>{if(settingsSection.open)loadSettings();});
  window.addEventListener('train-restriction-changed',()=>{if(!view.hidden)loadBoard(true);});
  openTrain();
})();

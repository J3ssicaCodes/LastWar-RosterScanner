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
    .train-controls label,.train-field{display:grid;gap:5px}
    #trainView input,#trainView select,#trainView textarea,#trainAdmin input,#trainAdmin select{color:var(--text);background:var(--panel2);border:1px solid var(--stroke);border-radius:7px;padding:8px;font:inherit;max-width:100%}
    #trainView .pl-table{min-width:640px}
    #trainView textarea{width:100%;min-height:8em}
    .train-wheel-wrap{width:min(78vw,330px);position:relative;margin:15px auto;text-align:center}
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
      <div class="pl-title-row"><div><div class="eyebrow">Alliance awards</div><h2>Train Manager</h2></div></div>
      <div class="train-controls">
        <label>Assignment date<input id="trainDay" type="date" required></label>
        <fieldset class="train-controls"><legend>Weekly goal</legend><label><span><input name="trainGoal" type="radio" value="Push" checked> Push</span></label><label><span><input name="trainGoal" type="radio" value="Save"> Save</span></label></fieldset>
        <label id="trainTargetLabel" hidden>Save goal<input id="trainTarget" type="number" min="1" max="9007199254740991" value="7200000"></label>
      </div>
      <p id="trainStatus" role="status" aria-live="polite"></p>
      <p class="train-hint" id="trainContext"></p>
      <div id="trainBoard" hidden>
        <h3>Eligible nominees</h3>
        <p class="train-hint">Uncheck a player to leave them out of Conductor and VIP selection.</p>
        <div class="pl-table-wrap"><table class="pl-table"><thead><tr><th>Include</th><th>Player</th><th>VS points</th><th>Nomination</th><th>Conductor</th></tr></thead><tbody id="trainRows"></tbody></table></div>
        <button id="trainMore" type="button" class="btn smallbtn" hidden>Show all nominees</button>
        <div class="train-controls"><label>Add a player<select id="trainManual"></select></label><button class="btn" type="button" id="trainAppend">Add to table</button></div>
        <h3>Train assignment</h3>
        <label class="train-field">Conductor<select id="trainConductor"></select></label>
        <div class="train-controls"><button class="btn" type="button" id="trainAddWheel">Add checked players to wheel</button>
          <button class="btn primary" type="button" id="trainSpin" disabled>Spin the wheel</button></div>
        <p id="trainWheelStatus" role="status" aria-live="polite"></p>
        <div class="train-wheel-wrap"><span class="train-pointer" aria-hidden="true">▼</span><canvas id="trainWheel" width="480" height="480" aria-label="VIP randomizer wheel"></canvas></div>
        <label class="train-field">VIP<select id="trainVip"></select></label>
        <div class="train-controls"><button class="btn primary" type="button" id="trainSave">Save train assignment</button><button class="btn" type="button" id="trainOpenEmail" hidden>Generate Alliance Email</button></div>
      </div>
    </section>
    <section class="panel pl-section" id="trainEmailEditor" hidden>
      <h3>Generate Alliance Email</h3>
      <form id="trainEventForm" class="train-controls">
        <label>Event name<input id="trainEventName" list="trainEventNames" autocomplete="off" maxlength="80" required><datalist id="trainEventNames"></datalist></label>
        <label>Event time<input id="trainEventTime" type="time" required></label>
        <label>Timezone<select id="trainZone">${zones.map(z=>'<option>'+z+'</option>').join('')}</select></label>
        <button class="btn" type="submit">Add event</button>
      </form>
      <ul id="trainEventList"></ul>
      <label class="train-field">Admin notes<textarea id="trainNotes" maxlength="4000"></textarea></label>
      <div class="train-controls"><label>Author<select id="trainAuthor"></select></label><button class="btn primary" type="button" id="trainGenerate">Generate Email</button></div>
      <label id="trainDraftField" class="train-field" hidden>Email draft<textarea id="trainDraft" maxlength="12000"></textarea></label>
      <div id="trainDraftActions" class="train-controls" hidden><button class="btn" type="button" id="trainCopy">Copy email</button><button class="btn primary" type="button" id="trainSaveEmail">Save email</button></div>
      <p id="trainEmailStatus" role="status" aria-live="polite"></p>
    </section>
    <section class="panel pl-section"><h3>Saved alliance emails</h3>
      <div class="train-controls"><button class="btn" type="button" id="trainOlderEmail">‹ Older</button><strong id="trainEmailDate"></strong><button class="btn" type="button" id="trainNewerEmail">Newer ›</button></div>
      <label id="trainSavedEmailField" class="train-field" hidden>Saved email<textarea id="trainSavedEmail" readonly></textarea></label>
      <button id="trainCopySaved" class="btn" type="button" hidden>Copy saved email</button>
      <p id="trainArchiveStatus" role="status" aria-live="polite"></p>
    </section>
    <section class="panel pl-section"><h3>Train assignment history</h3>
      <div class="pl-table-wrap"><table class="pl-table"><thead><tr><th>Date</th><th>Conductor</th><th>VIP</th></tr></thead><tbody id="trainHistory"></tbody></table></div>
      <button id="trainOlderHistory" class="btn" type="button" hidden>Load older assignments</button>
    </section>`;
  $('trainAdmin').innerHTML=`
    <h3>Train Manager settings</h3>
    <form id="trainSettingsForm" class="train-controls">
      <label>Alliance name<input name="alliance_name" maxlength="80" required></label>
      <label>Cooldown (weeks)<input name="cooldown_weeks" type="number" min="0" max="52" required></label>
      <label>Wheel spin (seconds)<input name="spin_seconds" type="number" min="1" max="30" required></label>
      <button type="submit" class="btn primary">Save train settings</button>
    </form>
    <p id="trainSettingsStatus" role="status"></p>
    <h3>Import historical train assignments</h3>
    <p>Use a CSV with Date, Conductor Player, and VIP Player columns. Review the name matches and save each row individually.</p>
    <form id="trainImportForm" class="train-controls"><label>Assignment CSV<input name="file" type="file" accept=".csv,text/csv" required></label><button class="btn" type="submit">Review CSV</button></form>
    <p id="trainImportStatus" role="status"></p><div id="trainImportRows" class="train-import-list"></div>
    <div class="train-controls" id="trainImportPages" hidden><button type="button" class="btn" id="trainImportPrevious">‹ Previous rows</button>
    <span id="trainImportPage"></span><button type="button" class="btn" id="trainImportNext">Next rows ›</button></div>`;

  async function api(path,options){
    const response=await fetch('/api/train'+path,{cache:'no-store',...options});
    const result=await response.json();
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Check the form and try again.');
    return result;
  }
  const json=(method,data)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const message=(id,text)=>{$(id).textContent=text;};
  const goal=()=>view.querySelector('[name="trainGoal"]:checked').value;
  const day=()=>$('trainDay').value;
  const target=()=>goal()==='Save'?Number($('trainTarget').value):defaultTarget;
  const option=(p,selected,disabled=false)=>'<option value="'+p.id+'" '+(p.id===selected?'selected ':'')+(disabled?'disabled':'')+'>'+esc(p.name+(p.blocked?' · '+p.blocked:''))+'</option>';
  let board,request=0,loading=false,manual=[],excluded=new Set(),showAll=false,spin=false,rotation=0,wheel=[],wheelWinner=0;
  let events=[],emailRows=[],emailIndex=0,historyRows=[];
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
  function invalidateWheel(){
    wheel=[];wheelWinner=0;drawWheel();message('trainWheelStatus','');
  }
  function render(){
    const assigned=board.assignment;
    const oldConductor=conductor(),oldVip=vip();
    const ids=nomineeIds();
    $('trainRows').innerHTML=ids.map(id=>{
      const p=getPlayer(id);
      const birthday=p.birthday_offset>=7?' · Birthday '+p.birthday+' in '+Math.floor(p.birthday_offset/7)+' week(s)':'';
      return '<tr><td><input type="checkbox" data-train-include="'+id+'" aria-label="Include '+esc(p.name)+'" '+(excluded.has(id)?'':'checked')+'></td>'+
        '<th scope="row">'+esc(p.name)+'</th><td>'+(p.points==null?'—':num(p.points))+'</td><td>'+esc(p.reason+birthday)+'</td>'+
        '<td><button class="btn smallbtn" type="button" data-train-conductor="'+id+'" '+(assigned||excluded.has(id)||spin?'disabled':'')+'>Assign as Conductor</button></td></tr>';
    }).join('')||'<tr><td colspan="5">No automatic nominees. Add an eligible player below.</td></tr>';
    $('trainMore').hidden=board.nominees.length<=20;
    $('trainMore').textContent=showAll?'Show top 20':'Show all '+board.nominees.length+' nominees';
    $('trainManual').innerHTML='<option value="">Choose player…</option>'+board.players.map(p=>option(p,0,!!p.blocked||ids.includes(p.id))).join('');
    const choices=board.players.filter(p=>!p.blocked&&!excluded.has(p.id));
    for(const [id,chosen,other] of [['trainConductor',oldConductor,0],['trainVip',oldVip,oldConductor]]){
      const select=$(id);
      if(assigned){
        select.innerHTML='<option>'+esc(assigned[id==='trainConductor'?'conductor_name':'vip_name'])+'</option>';
      }else{
        select.innerHTML='<option value="">Choose player…</option>'+choices.filter(p=>p.id!==other).map(p=>option(p,chosen)).join('');
      }
      select.disabled=!!assigned||spin;
    }
    if(!assigned&&vip()===conductor())$('trainVip').value='';
    $('trainOpenEmail').hidden=!assigned;
    $('trainAppend').disabled=!!assigned||spin;
    $('trainAddWheel').disabled=!!assigned||spin||!conductor();
    $('trainSpin').disabled=!!assigned||spin||!conductor()||!wheel.length;
    $('trainSave').disabled=!!assigned||spin||!conductor()||!vip();
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
      if(reset){manual=[];excluded.clear();showAll=false;$('trainConductor').value='';$('trainVip').value='';invalidateWheel();}
      else{
        manual=manual.filter(id=>getPlayer(id)&&!getPlayer(id).blocked);
        if(wheel.some(id=>!getPlayer(id)||getPlayer(id).blocked))invalidateWheel();
      }
      message('trainContext','VS score date '+board.score_date+' · '+board.settings.cooldown_weeks+'-week cooldown');
      message('trainStatus',board.assignment?'Train assignment saved for '+day()+'.':'');
      $('trainBoard').hidden=false;render();
    }catch(error){if(current===request)message('trainStatus',error.message);}
    finally{if(current===request)loading=false;}
  }

  function selectConductor(id){
    if(!getPlayer(id)||getPlayer(id).blocked||excluded.has(id))return;
    $('trainConductor').value=id;
    if(vip()===id)$('trainVip').value='';
    invalidateWheel();render();
  }
  $('trainRows').onclick=event=>{
    const button=event.target.closest('[data-train-conductor]');
    if(button)selectConductor(Number(button.dataset.trainConductor));
  };
  $('trainRows').onchange=event=>{
    const id=Number(event.target.dataset.trainInclude);
    if(!id||spin)return;
    if(event.target.checked)excluded.delete(id);
    else{excluded.add(id);if(conductor()===id)$('trainConductor').value='';if(vip()===id)$('trainVip').value='';}
    invalidateWheel();render();
  };
  $('trainMore').onclick=()=>{showAll=!showAll;render();};
  $('trainAppend').onclick=()=>{
    const id=Number($('trainManual').value);
    if(id&&!getPlayer(id).blocked){manual.push(id);render();}
  };
  $('trainConductor').onchange=()=>{
    if(conductor())selectConductor(conductor());
    else{invalidateWheel();render();}
  };
  $('trainVip').onchange=()=>{wheelWinner=0;render();};
  $('trainDay').onchange=()=>{
    $('trainEmailEditor').hidden=true;events=[];$('trainNotes').value='';$('trainDraft').value='';
    $('trainDraftField').hidden=true;$('trainDraftActions').hidden=true;
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
  $('trainAddWheel').onclick=()=>{
    wheel=nomineeIds().filter(id=>id!==conductor()&&!excluded.has(id)&&!getPlayer(id).blocked);
    drawWheel();render();
    message('trainWheelStatus',wheel.length?wheel.length+' eligible VIP candidates on the wheel.':'Select at least one eligible VIP candidate.');
  };
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
      spin=false;render();$('trainVip').value=winner;wheelWinner=winner;render();
      for(const input of view.querySelectorAll('[name="trainGoal"],#trainDay,#trainTarget,[data-train-include],#trainMore'))input.disabled=false;
      message('trainWheelStatus',getPlayer(winner).name+' selected as VIP.');
    },duration*1000);
  };
  $('trainSave').onclick=async()=>{
    const button=$('trainSave');button.disabled=true;
    const date=day();
    try{
      await api('/assignments',json('POST',{day:date,conductor_id:conductor(),vip_id:vip(),goal:goal(),target:target(),wheel_vip:wheelWinner===vip()}));
      await Promise.all([loadBoard(),loadHistory()]);
    }catch(error){message('trainStatus',error.message);if(day()===date)render();}
  };

  async function loadHistory(older=false){
    try{
      const before=older&&historyRows.length?'?before='+encodeURIComponent(historyRows.at(-1).assignment_date):'';
      const {rows,more}=await api('/history'+before);
      historyRows=older?[...historyRows,...rows]:rows;
      $('trainHistory').innerHTML=historyRows.map(row=>'<tr><td>'+esc(row.assignment_date)+'</td><td>'+esc(row.conductor_name)+'</td><td>'+esc(row.vip_name)+'</td></tr>').join('')||
        '<tr><td colspan="3">No assignments saved yet.</td></tr>';
      $('trainOlderHistory').hidden=!more;
    }catch(error){message('trainStatus',error.message);}
  }
  $('trainOlderHistory').onclick=()=>loadHistory(true);

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
    const lines=['Good Morning '+board.settings.alliance_name+',',
      'Today’s train driver is '+assignment.conductor_name+' and VIP is '+assignment.vip_name+'.'];
    const c=selectionReason(assignment.conductor_reason,assignment),v=selectionReason(assignment.vip_reason,assignment);
    if(assignment.goal==='Save'&&assignment.conductor_points===assignment.target&&assignment.vip_points===assignment.target){
      lines[1]+=' Both hit the save goal of '+num(assignment.target)+' VS points.';
      if(c.includes('birthday'))lines[1]+=' '+assignment.conductor_name+' has a birthday this week.';
      if(v.includes('birthday'))lines[1]+=' '+assignment.vip_name+' has a birthday this week.';
      if(v.includes('Wheel of Fortune'))lines[1]+=' The Wheel of Fortune selected '+assignment.vip_name+' as VIP.';
    }else{
      if(c)lines[1]+=' '+assignment.conductor_name+' '+c+'.';
      if(v)lines[1]+=' '+assignment.vip_name+' '+v+'.';
    }
    for(const event of events){
      let line=event.name+' is at '+event.time+' '+event.zone+'.';
      if(event.name.trim().toLowerCase()==='zombie siege')line+=' Please have troops on the wall by '+wallTime(event.time)+' '+event.zone+(event.time<'00:30'?' the previous day':'')+'.';
      lines.push(line);
    }
    if($('trainNotes').value.trim())lines.push($('trainNotes').value.trim());
    lines.push('Best Regards,\n'+author.name);
    $('trainDraft').value=lines.join('\n\n');$('trainDraftField').hidden=false;$('trainDraftActions').hidden=false;
    message('trainEmailStatus','Draft ready to edit, copy, or save.');
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
  function openTrain(){if(!view.hidden&&!loading&&!spin){loadBoard();loadEmails();loadHistory();}}
  new MutationObserver(openTrain).observe(view,{attributes:true,attributeFilter:['hidden']});
  new MutationObserver(()=>{if(!$('adminView').hidden)loadSettings();}).observe($('adminView'),{attributes:true,attributeFilter:['hidden']});
  if(!$('adminView').hidden)loadSettings();
  window.addEventListener('train-restriction-changed',()=>{if(!view.hidden)loadBoard(true);});
  openTrain();
})();

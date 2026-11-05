(() => {
  const confirmDialog=document.getElementById('adminClearConfirm');
  const message=document.getElementById('adminClearMessage');
  const clearButton=document.getElementById('adminClearButton');
  const tableList=document.getElementById('adminClearTables');
  const confirmationList=document.getElementById('adminConfirmTables');
  let selectedTables=[];
  let clearing=false;
  const checkedTables=()=>[...tableList.querySelectorAll('input:checked')];
  const syncButton=()=>{clearButton.disabled=clearing||!checkedTables().length;};
  tableList.addEventListener('change',syncButton);
  const resetKey='lastwar.adminReset.v1';
  const dictionaryKey='lastwar.ocrNameDictionary.v1';
  const completedKey='lastwar.adminResetCompleted';

  function applyReset(resetId){
    if(!resetId||localStorage.getItem(resetKey)===resetId)return false;
    for(const store of [localStorage,sessionStorage]){
      for(const key of Object.keys(store)){
        if(key.startsWith('lastwar.')&&key!==dictionaryKey&&key!==resetKey){
          store.removeItem(key);
        }
      }
    }
    localStorage.setItem(resetKey,resetId);
    return true;
  }

  async function refresh(){
    if(clearing)return;
    try{
      const response=await fetch('/api/admin/status',{cache:'no-store'});
      if(!response.ok)throw Error('Could not check whether Clear Data is available.');
      const status=await response.json();
      if(clearing)return;
      if(applyReset(status.reset_id))location.reload();
    }catch(error){
      message.textContent=error.message;
    }
  }

  async function loadTables(){
    try{
      const response=await fetch('/api/admin/tables',{cache:'no-store'});
      if(!response.ok)throw Error('Could not load tables to clear.');
      const {tables}=await response.json();
      const previous=new Set(checkedTables().map(input=>input.value));
      tableList.replaceChildren();
      for(const table of tables){
        const label=document.createElement('label');
        const input=document.createElement('input');
        input.type='checkbox';
        input.value=table.id;
        input.checked=previous.has(table.id);
        label.append(input,' '+table.label);
        tableList.append(label);
      }
      if(!tables.length)tableList.textContent='No tables available to clear.';
      syncButton();
    }catch(error){message.textContent=error.message;}
  }

  clearButton.addEventListener('click',()=>{
    const checked=checkedTables();
    if(clearing||!checked.length)return;
    selectedTables=checked.map(input=>input.value);
    confirmationList.replaceChildren(...checked.map(input=>{
      const item=document.createElement('li');
      item.textContent=input.parentElement.textContent.trim();
      return item;
    }));
    confirmDialog.returnValue='';
    confirmDialog.showModal();
  });

  confirmDialog.addEventListener('close',async()=>{
    if(clearing||confirmDialog.returnValue!=='clear')return;
    clearing=true;
    clearButton.disabled=true;
    message.textContent='Clearing saved data…';
    try{
      const response=await fetch('/api/admin/clear-data',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({confirmation:'CLEAR DATA',tables:selectedTables}),
      });
      const result=await response.json();
      if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not clear saved data.');
      const summary='Cleared '+result.tables_cleared+' selected '+(result.tables_cleared===1?'table.':'tables.');
      try{sessionStorage.setItem(completedKey,summary);}catch{}
      location.reload();
    }catch(error){
      message.textContent=error.message;
    }finally{
      clearing=false;
      syncButton();
    }
  });

  try{
    const completed=sessionStorage.getItem(completedKey);
    if(completed){
      sessionStorage.removeItem(completedKey);
      document.getElementById('adminTab').click();
      message.textContent=completed;
    }
  }catch{}
  window.addEventListener('storage',event=>{
    if(event.key===resetKey&&event.newValue)location.reload();
  });
  window.addEventListener('focus',refresh);
  document.getElementById('adminTab').addEventListener('click',loadTables);
  if(document.getElementById('adminTab').getAttribute('aria-selected')==='true')loadTables();
  refresh();

  const dictionaryForm=document.getElementById('adminDictionaryForm');
  const dictionaryRows=document.getElementById('adminDictionaryRows');
  const dictionaryMessage=document.getElementById('adminDictionaryMessage');
  const dictionaryPath='/api/admin/name-dictionary';
  const originalInput=dictionaryForm.elements.original;
  const correctedInput=dictionaryForm.elements.corrected;
  const cancelEdit=dictionaryForm.querySelector('[type="reset"]');
  const saveCorrection=dictionaryForm.querySelector('[type="submit"]');
  let dictionaryBusy=false;
  async function dictionaryRequest(url,options){
    const response=await fetch(url,{cache:'no-store',...options});
    const result=await response.json();
    if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not update the dictionary.');
    return result;
  }
  async function loadDictionary(){
    try{
      const {entries}=await dictionaryRequest(dictionaryPath);
      dictionaryRows.replaceChildren();
      if(!entries.length){
        const cell=dictionaryRows.insertRow().insertCell();
        cell.colSpan=3;
        cell.textContent='No saved corrections yet.';
      }
      for(const entry of entries){
        const row=dictionaryRows.insertRow();
        row.insertCell().textContent=entry.original;
        row.insertCell().textContent=entry.corrected;
        const actions=row.insertCell();
        for(const label of ['Edit','Remove']){
          const button=document.createElement('button');
          button.type='button';
          button.className='btn';
          button.textContent=label;
          button.setAttribute('aria-label',label+' correction for '+entry.original);
          actions.append(button,' ');
          button.addEventListener('click',async()=>{
            if(dictionaryBusy)return;
            if(label==='Edit'){
              originalInput.value=entry.original;
              originalInput.readOnly=true;
              correctedInput.value=entry.corrected;
              cancelEdit.hidden=false;
              correctedInput.focus();
              return;
            }
            if(!confirm('Remove the correction for '+entry.original+'?'))return;
            dictionaryBusy=true;
            saveCorrection.disabled=true;
            try{
              await dictionaryRequest(dictionaryPath+'?'+new URLSearchParams({original:entry.original}),{method:'DELETE'});
              if(originalInput.value===entry.original)dictionaryForm.reset();
              await loadDictionary();
              dictionaryMessage.textContent='Correction removed.';
            }catch(error){dictionaryMessage.textContent=error.message;}
            finally{dictionaryBusy=false;saveCorrection.disabled=false;}
          });
        }
      }
    }catch(error){dictionaryMessage.textContent=error.message;}
  }
  dictionaryForm.addEventListener('reset',()=>{
    originalInput.readOnly=false;
    cancelEdit.hidden=true;
  });
  dictionaryForm.addEventListener('submit',async event=>{
    event.preventDefault();
    if(dictionaryBusy)return;
    dictionaryBusy=true;
    saveCorrection.disabled=true;
    try{
      await dictionaryRequest(dictionaryPath,{
        method:'PUT',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({original:originalInput.value,corrected:correctedInput.value}),
      });
      dictionaryForm.reset();
      await loadDictionary();
      dictionaryMessage.textContent='Correction saved.';
    }catch(error){dictionaryMessage.textContent=error.message;}
    finally{dictionaryBusy=false;saveCorrection.disabled=false;}
  });
  document.getElementById('adminTab').addEventListener('click',loadDictionary);
  if(document.getElementById('adminTab').getAttribute('aria-selected')==='true')loadDictionary();
})();

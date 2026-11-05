(() => {
  const confirmDialog=document.getElementById('adminClearConfirm');
  const message=document.getElementById('adminClearMessage');
  const clearButton=document.getElementById('adminClearButton');
  const resetKey='lastwar.adminReset.v1';
  const dictionaryKey='lastwar.ocrNameDictionary.v1';
  const completedKey='lastwar.adminResetCompleted';
  let clearing=false;

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
      if(applyReset(status.reset_id)){
        location.reload();
        return;
      }
      clearButton.disabled=false;
    }catch(error){
      clearButton.disabled=true;
      message.textContent=error.message;
    }
  }

  clearButton.addEventListener('click',()=>{
    if(clearing)return;
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
        body:JSON.stringify({confirmation:'CLEAR DATA'}),
      });
      const result=await response.json();
      if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not clear saved data.');
      try{
        applyReset(result.reset_id);
        sessionStorage.setItem(completedKey,'Saved data cleared. The OCR dictionary was kept.');
      }catch{
        message.textContent='Shared data cleared. This browser blocked clearing its saved settings.';
        return;
      }
      location.reload();
    }catch(error){
      message.textContent=error.message;
    }finally{
      clearing=false;
      clearButton.disabled=false;
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
  document.getElementById('adminTab').addEventListener('click',refresh);
  refresh();
})();

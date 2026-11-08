(() => {
  const match=location.pathname.match(/^\/a\/([0-9]+-[a-z0-9]+)\/manage\/?$/);
  const slug=match?.[1];
  window.workspaceSlug=slug||null;
  window.workspaceKey=key=>slug&&key.startsWith('lastwar.')
    ? 'lastwar.workspace.'+slug+'.'+key.slice(8) : key;
  window.workspaceApi=url=>slug&&typeof url==='string'&&url.startsWith('/api/')
    && !url.startsWith('/api/auth/') && !url.startsWith('/api/public/')
      ? '/a/'+slug+url : url;
  if(!slug)return;

  if(slug==='1439-rvby'){
    try{
      for(const key of Object.keys(localStorage)){
        if(!key.startsWith('lastwar.')||key.startsWith('lastwar.workspace.'))continue;
        const scoped=window.workspaceKey(key);
        if(localStorage.getItem(scoped)===null)localStorage.setItem(scoped,localStorage.getItem(key));
      }
    }catch{}
  }
  const originalFetch=window.fetch.bind(window);
  window.fetch=(input,options)=>originalFetch(window.workspaceApi(input),options);
  const originalOpen=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(method,url,...rest){
    return originalOpen.call(this,method,window.workspaceApi(url),...rest);
  };
  document.addEventListener('DOMContentLoaded',()=>{
    const utilities=document.getElementById('workspaceUtilities');
    if(utilities){
      const account=document.createElement('a');
      account.className='profile-avatar';
      account.href='/account?alliance='+encodeURIComponent(slug);
      account.setAttribute('aria-label','Account and leadership');
      account.title='Account & leadership';
      account.addEventListener('click',event=>{
        if(window.openLeadershipAccount && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey){
          event.preventDefault();window.openLeadershipAccount();
        }
      });
      const fallback=document.createElement('span');fallback.textContent='👤';fallback.setAttribute('aria-hidden','true');
      const picture=document.createElement('img');picture.alt='';picture.hidden=true;
      picture.onload=()=>{picture.hidden=false;fallback.hidden=true;};
      picture.onerror=()=>{picture.hidden=true;fallback.hidden=false;};
      picture.src='/api/auth/avatar';account.append(fallback,picture);
      document.querySelector('.top')?.append(account);
      const viewer=document.createElement('a');
      viewer.className='btn';
      viewer.href='/a/'+slug;
      viewer.textContent='Viewer page';
      utilities.append(viewer);
      const notifications=document.createElement('a');
      notifications.className='btn notification-bell';notifications.href='/account?alliance='+encodeURIComponent(slug)+'&notifications=1';
      notifications.setAttribute('aria-label','Notifications');
      notifications.addEventListener('click',event=>{
        if(window.openLeadershipAccount && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey){
          event.preventDefault();window.openLeadershipAccount({notifications:'1'});
        }
      });
      const bell=document.createElement('span');bell.textContent='🔔';bell.setAttribute('aria-hidden','true');
      const badge=document.createElement('span');badge.className='notification-badge';badge.hidden=true;badge.setAttribute('aria-hidden','true');
      notifications.append(bell,badge);
      utilities.append(notifications);
      originalFetch('/api/auth/notifications',{cache:'no-store'}).then(response=>response.ok?response.json():null)
        .then(data=>{if(data?.unread){badge.textContent=data.unread>99?'99+':String(data.unread);badge.hidden=false;notifications.setAttribute('aria-label',`Notifications, ${data.unread} unread`);}}).catch(()=>{});
      window.addEventListener('message',event=>{
        const frame=document.getElementById('accountFrame');
        if(event.origin===location.origin && event.source===frame?.contentWindow && event.data?.type==='alliance-avatar-changed'){
          picture.src='/api/auth/avatar?v='+Date.now();
        }
      });
    }
    const exportLink=document.getElementById('plExport');
    if(exportLink)exportLink.href=window.workspaceApi('/api/player-list/export.csv');
    const train=new URLSearchParams(location.search).get('train');
    if(train){
      document.getElementById('trainTab')?.click();
      const input=document.getElementById('trainDay');
      if(input){input.value=train;input.dispatchEvent(new Event('change',{bubbles:true}));input.scrollIntoView();}
    }
  });
  window.addEventListener('load',()=>{
    const player=new URLSearchParams(location.search).get('player');
    if(player&&/^\d+$/.test(player)){
      document.getElementById('playerListTab')?.click();
      window.openPlayerProfile?.(Number(player));
    }
  });
})();

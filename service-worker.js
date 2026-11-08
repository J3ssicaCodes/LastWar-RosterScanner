self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));
// Stay online-only while allowing browsers that require a fetch handler to install.
self.addEventListener('fetch',event=>{
  if(event.request.mode==='navigate')event.respondWith(fetch(event.request));
});
self.addEventListener('push',event=>{
  let message={title:'Alliance Tools',body:'You have an update.',target:'/account'};
  try{message={...message,...event.data.json()};}catch{}
  const target=typeof message.target==='string'&&message.target.startsWith('/')&&!message.target.startsWith('//')
    ?message.target:'/account';
  event.waitUntil(self.registration.showNotification(message.title,{
    body:message.body,icon:'/alliance-icon-192.png?v=2',badge:'/alliance-icon-192.png?v=2',data:{target},
  }));
});
self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const target=new URL(event.notification.data?.target||'/account',self.location.origin).href;
  event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(async windows=>{
    const open=windows.find(window=>window.url.startsWith(self.location.origin));
    if(open){await open.navigate(target);return open.focus();}
    return clients.openWindow(target);
  }));
});

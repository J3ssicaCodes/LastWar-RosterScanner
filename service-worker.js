self.addEventListener('push',event=>{
  let message={title:'Alliance Tools',body:'You have an update.',target:'/account'};
  try{message={...message,...event.data.json()};}catch{}
  const target=typeof message.target==='string'&&message.target.startsWith('/')&&!message.target.startsWith('//')
    ?message.target:'/account';
  event.waitUntil(self.registration.showNotification(message.title,{
    body:message.body,icon:'/alliance-icon-192.png',badge:'/alliance-icon-192.png',data:{target},
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

(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const parts=location.pathname.split('/').filter(Boolean);
  const inviteKind=['join','new-alliance','claim'].includes(parts[0])?parts[0]:null;
  let inviteCode=inviteKind?parts[1]:'';
  let account=null,alliance=new URLSearchParams(location.search).get('alliance');
  const message=(value,error=false)=>{$('status').textContent=value;$('status').className='message'+(error?' error':'');};
  async function api(path,body,method='POST'){
    const options={method,cache:'no-store'};
    if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
    const response=await fetch('/api/auth'+path,options);
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Please try again.');
    return data;
  }
  function serialize(form){return Object.fromEntries(new FormData(form).entries());}
  function link(container,label,url){
    const a=document.createElement('a');a.href=url;a.textContent=label;if(window.parent!==window&&url.startsWith('/a/'))a.target='_top';container.append(a);return a;
  }
  function showInvite(token,kind,destination){
    const prefix={referral:'new-alliance',leadership:'join',claim:'claim'}[kind];
    const url=location.origin+'/'+prefix+'/'+encodeURIComponent(token);
    destination.replaceChildren();link(destination,url,url);
  }
  function navigate(url){
    const destination=new URL(url,location.href);
    if(window.parent!==window&&destination.origin===location.origin&&destination.pathname.startsWith('/a/'))window.top.location.href=destination.href;
    else location.href=destination.href;
  }
  function avatarChanged(){if(window.parent!==window)window.parent.postMessage({type:'alliance-avatar-changed'},location.origin);}
  async function inspect(){
    if(!inviteCode)return null;
    const invite=await api('/inspect-invite',{code:inviteCode});
    $('inviteInfo').hidden=false;
    $('inviteDescription').textContent=invite.kind==='referral'
      ? 'This referral lets you create an alliance.'
      : 'This invitation is for '+invite.alliance.name+'.';
    return invite;
  }
  async function resumeInvite(invite){
    if(!invite)return;
    if(invite.kind==='referral'){$('createAlliance').hidden=false;return;}
    if(invite.kind==='leadership'){
      if(account.workspaces.some(workspace=>workspace.slug===invite.alliance.slug)){
        navigate('/a/'+invite.alliance.slug+'/manage');return;
      }
      const result=await api('/redeem',{code:inviteCode});
      navigate('/a/'+result.slug+'/manage');return;
    }
    $('claimSection').hidden=false;
    const roster=await api('/claims/roster',{code:inviteCode});
    $('claimPlayer').replaceChildren();
    for(const player of roster.players){
      const option=document.createElement('option');option.value=player.id;option.textContent=player.name;
      $('claimPlayer').append(option);
    }
  }
  async function refresh(){
    let invite=null;
    try{invite=await inspect();}catch(error){message(error.message,true);}
    try{account=await api('/me',undefined,'GET');}catch{account=null;}
    $('authSection').hidden=!!account;
    $('registerSection').hidden=!invite||!!account;
    $('inviteEntry').hidden=!!invite;
    $('accountSection').hidden=!account;
    $('passwordSection').hidden=!account;
    $('leadershipSection').hidden=true;
    $('claimReviewSection').hidden=true;
    $('auditSection').hidden=true;
    $('inboxSection').hidden=true;
    $('tasksSection').hidden=true;
    $('wallSection').hidden=true;
    $('analyticsSection').hidden=true;
    $('notificationSection').hidden=!account;
    $('pushSection').hidden=!account;
    $('myClaimsSection').hidden=!account;
    $('platformSection').hidden=!account?.platform_admin;
    $('supportBanner').hidden=!account?.support_actor;
    if(account?.support_actor)$('supportIdentity').textContent='Support mode: viewing as '+account.display_name+' ('+account.username+')';
    if(!account)return;
    loadAvatar();
    preparePush();
    await loadNotifications();
    await loadMyClaims();
    $('accountName').textContent=account.display_name+' · '+account.username;
    $('platformRole').textContent=account.platform_admin?'Platform Admin':'';
    if(account.platform_admin){await loadReferrals();await loadPlatformAlliances();}
    $('workspaces').replaceChildren();
    for(const workspace of account.workspaces){
      const li=document.createElement('li');
      link(li,workspace.name+' ['+workspace.tag+'] · '+workspace.role,'/a/'+workspace.slug+'/manage');
      $('workspaces').append(li);
    }
    if(!account.workspaces.length)$('workspaces').textContent='No leadership memberships yet.';
    if(!alliance&&account.workspaces.length===1)alliance=account.workspaces[0].slug;
    const membership=account.workspaces.find(workspace=>workspace.slug===alliance);
    if(membership){await loadLeadership(membership);await loadClaims(membership);await loadAudit();await loadCoordination();await loadAnalytics();}
    if(invite)await resumeInvite(invite);
  }
  function loadAvatar(){
    const preview=$('avatarPreview'),fallback=$('avatarFallback');
    preview.onload=()=>{preview.hidden=false;fallback.hidden=true;$('removeAvatar').hidden=false;};
    preview.onerror=()=>{preview.hidden=true;fallback.hidden=false;$('removeAvatar').hidden=true;};
    preview.src='/api/auth/avatar?v='+Date.now();
  }
  $('avatarForm').onsubmit=async event=>{
    event.preventDefault();
    const status=$('avatarStatus'),file=$('avatarFile').files[0];
    if(!file)return;
    if(file.size>6*1024*1024){status.textContent='Choose an image smaller than 6 MB.';status.className='message error';return;}
    const data=new FormData();data.append('picture',file);
    try{
      const response=await fetch('/api/auth/avatar',{method:'POST',body:data});
      const result=await response.json();
      if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'Could not save your picture.');
      $('avatarFile').value='';status.textContent='Profile picture saved.';status.className='message';loadAvatar();avatarChanged();
    }catch(error){status.textContent=error.message;status.className='message error';}
  };
  $('removeAvatar').onclick=async()=>{
    const status=$('avatarStatus');
    try{await api('/avatar',undefined,'DELETE');status.textContent='Profile picture removed.';status.className='message';loadAvatar();avatarChanged();}
    catch(error){status.textContent=error.message;status.className='message error';}
  };
  $('endSupport').onclick=async()=>{try{await api('/platform/support/stop',{});navigate('/account');}catch(e){message(e.message,true);}};
  async function loadPlatformAlliances(){
    const data=await api('/platform/alliances',undefined,'GET'), container=$('platformAlliances');
    container.replaceChildren();
    let last=null;
    for(const row of data.alliances){
      if(last!==row.slug){const heading=document.createElement('h4');heading.textContent=row.name+' ['+row.tag+'] · Server '+row.server_number;container.append(heading);last=row.slug;}
      if(!row.username){const label=document.createElement('p');label.textContent='No leadership accounts';container.append(label);continue;}
      const section=document.createElement('div'),title=document.createElement('p');
      title.textContent=row.display_name+' · '+row.username+' · '+row.role;section.append(title);
      if(row.platform_admin){const note=document.createElement('p');note.textContent='Platform admin account';section.append(note);container.append(section);continue;}
      const form=document.createElement('form'),reason=document.createElement('input'),pass=document.createElement('input');
      reason.placeholder='Support reason';reason.required=true;reason.maxLength=240;reason.setAttribute('aria-label','Support reason for '+row.username);
      pass.type='password';pass.placeholder='New password (12+ characters)';pass.minLength=12;pass.maxLength=128;pass.autocomplete='new-password';pass.setAttribute('aria-label','New password for '+row.username);
      const view=document.createElement('button'),reset=document.createElement('button');
      view.type='button';view.textContent='View as user';reset.type='button';reset.textContent='Reset password';
      view.onclick=async()=>{if(!form.reportValidity())return;try{await api('/platform/alliances/'+row.slug+'/support',{username:row.username,support_reason:reason.value});navigate('/a/'+row.slug+'/manage');}catch(e){message(e.message,true);}};
      reset.onclick=async()=>{if(!form.reportValidity()||!pass.value){message('Enter a support reason and a new password.',true);return;}try{await api('/platform/alliances/'+row.slug+'/reset-password',{username:row.username,support_reason:reason.value,new_password:pass.value});pass.value='';message('Password reset for '+row.username+'. They can sign in with the new password.');}catch(e){message(e.message,true);}};
      form.append(reason,pass,view,reset);section.append(form);container.append(section);
    }
    if(!data.alliances.length)container.textContent='No alliances found.';
  }
  async function loadReferrals(){
    const data=await api('/referrals',undefined,'GET');
    $('platformReferrals').replaceChildren();
    for(const referral of data.referrals){
      const row=document.createElement('div');row.className='row';
      const label=document.createElement('span');label.textContent='Expires '+referral.expires_at+
        (referral.revoked_at?' · revoked':referral.used_at?' · used':' · active');row.append(label);
      if(!referral.revoked_at&&!referral.used_at){
        const button=document.createElement('button');button.textContent='Revoke';
        button.onclick=async()=>{try{await api('/referrals/'+referral.id+'/revoke',{});await loadReferrals();}catch(e){message(e.message,true);}};
        row.append(button);
      }
      $('platformReferrals').append(row);
    }
    if(!data.referrals.length)$('platformReferrals').textContent='No referrals created yet.';
  }
  async function loadAnalytics(){
    const data=await api('/alliances/'+alliance+'/analytics',undefined,'GET');
    $('analyticsSection').hidden=false;
    $('analyticsSummary').textContent=data.completed_days+' completed weekdays · '+data.current_points.toLocaleString()+
      ' points this week vs '+data.previous_points.toLocaleString()+' last week · '+data.scores_recorded+'/'+
      data.scores_expected+' scores recorded · '+data.recorded_zeroes+' recorded zeroes · '+data.below_goal+' below 7.2M';
    $('missingScores').replaceChildren();
    for(const day of data.missing){
      const p=document.createElement('p');p.textContent=day.date+' · '+(day.players.length?day.players.join(', '):'All players recorded');$('missingScores').append(p);
    }
    $('goalSummary').replaceChildren();
    for(const day of data.save_days){
      const p=document.createElement('p');p.textContent=day.date+' Save · '+day.hits+' hit 7.2M · '+day.shortfalls+' short · '+day.overage.toLocaleString()+' over goal';$('goalSummary').append(p);
    }
    for(const day of data.push_days){
      const p=document.createElement('p');p.textContent=day.date+' Push · '+day.total.toLocaleString()+' points · '+day.contributors+' contributors of '+day.recorded+' recorded';$('goalSummary').append(p);
    }
    if(!$('goalSummary').children.length)$('goalSummary').textContent='Goal data appears when a Save or Push day is recorded in Train Manager.';
    $('trendSummary').replaceChildren();
    const trends=data.trends.filter(item=>item.change!==null).sort((a,b)=>b.change-a.change);
    for(const item of trends){
      const p=document.createElement('p');link(p,item.name+' · '+(item.change>=0?'+':'')+item.change.toLocaleString(),
        '/a/'+alliance+'/manage?player='+item.id);
      const detail=document.createElement('span');detail.className='muted';detail.textContent=' · '+item.recorded_days.join('/')+' days recorded across four weeks';p.append(detail);$('trendSummary').append(p);
    }
    if(!trends.length)$('trendSummary').textContent='More recorded weeks are needed for a comparison.';
    $('trainFairness').replaceChildren();
    for(const player of data.fairness.sort((a,b)=>a.awards_90-b.awards_90||a.name.localeCompare(b.name))){
      const p=document.createElement('p');
      p.textContent=player.name+' · Conductor '+(player.last_conductor||'never')+' · VIP '+(player.last_vip||'never')+
        ' · '+player.awards_30+' awards in 30 days / '+player.awards_90+' in 90 days'+
        (player.eligible?'':(' · Ineligible'+(player.ineligibility_reason?' · '+player.ineligibility_reason:'')));
      $('trainFairness').append(p);
    }
  }
  async function loadCoordination(){
    const [inbox,leaders]=await Promise.all([
      api('/alliances/'+alliance+'/inbox',undefined,'GET'),
      api('/alliances/'+alliance+'/leaders',undefined,'GET'),
    ]);
    $('inboxSection').hidden=false;$('inboxItems').replaceChildren();
    for(const claim of inbox.claims){
      const p=document.createElement('p');link(p,'Player claim · '+claim.player_name,'/account?alliance='+alliance+'&claim='+claim.id);$('inboxItems').append(p);
    }
    for(const job of inbox.jobs){
      const p=document.createElement('p');link(p,(job.pending?job.pending+' scan rows to review':'Failed upload')+' · '+job.destination,
        '/a/'+alliance+'/manage?job='+encodeURIComponent(job.id));$('inboxItems').append(p);
    }
    for(const train of inbox.trains){
      const p=document.createElement('p');link(p,'Dice-roll winner needed · '+train.assignment_date,'/a/'+alliance+'/manage?train='+train.assignment_date);$('inboxItems').append(p);
    }
    if(!$('inboxItems').children.length)$('inboxItems').textContent='Nothing needs attention.';
    $('taskAssignee').replaceChildren();
    for(const leader of leaders.leaders){
      const option=document.createElement('option');option.value=leader.username;option.textContent=leader.display_name+' · '+leader.role;$('taskAssignee').append(option);
    }
    $('tasksSection').hidden=false;$('wallSection').hidden=false;
    if(!$('taskForm').elements.start_date.value)$('taskForm').elements.start_date.value=new Date().toISOString().slice(0,10);
    await Promise.all([loadTasks(),loadWall()]);
  }
  async function loadTasks(){
    const data=await api('/alliances/'+alliance+'/tasks?mine='+$('onlyMyTasks').checked,undefined,'GET');
    $('taskItems').replaceChildren();
    for(const item of data.occurrences){
      const box=document.createElement('article');box.className='card';box.id='task-'+item.id;
      const label=document.createElement('strong');label.textContent=item.title+' · '+item.status;box.append(label);
      const due=document.createElement('p');due.textContent='Due '+new Date(item.due_at+'Z').toLocaleString()+' · '+item.assignee;box.append(due);
      if(item.description){const description=document.createElement('p');description.textContent=item.description;box.append(description);}
      const actions=document.createElement('div');actions.className='actions';
      for(const [action,label] of (item.status==='pending'?[['done','Done'],['skip','Skip'],['reassign','Reassign']]:[['reopen','Reopen']])){
        const button=document.createElement('button');button.textContent=label;
        button.onclick=async()=>{
          const assignee=action==='reassign'?(prompt('Enter the new leader’s username')||''):'';
          if(action==='reassign'&&!assignee)return;
          try{await api('/alliances/'+alliance+'/tasks/'+item.id,{action,assignee});await loadTasks();}
          catch(error){message(error.message,true);}
        };
        actions.append(button);
      }
      box.append(actions);$('taskItems').append(box);
    }
    if(!data.occurrences.length)$('taskItems').textContent='No tasks in this view.';
    const id=new URLSearchParams(location.search).get('task');
    if(id)document.getElementById('task-'+id)?.scrollIntoView();
  }
  async function loadWall(){
    const data=await api('/alliances/'+alliance+'/wall',undefined,'GET');
    $('wallPosts').replaceChildren();
    for(const post of data.posts){
      const box=document.createElement('article');box.className='card';
      const heading=document.createElement('strong');heading.textContent=(post.pinned?'📌 ':'')+post.display_name;box.append(heading);
      const content=document.createElement('p');content.textContent=post.body;box.append(content);
      const pin=document.createElement('button');pin.textContent=post.pinned?'Unpin':'Pin';
      pin.onclick=async()=>{try{await api('/alliances/'+alliance+'/wall/'+post.id+'/pin',{pinned:!post.pinned},'PUT');await loadWall();}catch(e){message(e.message,true);}};
      box.append(pin);
      for(const reply of data.replies.filter(item=>item.post_id===post.id)){
        const p=document.createElement('p');p.textContent=reply.display_name+' · '+reply.body;box.append(p);
      }
      const form=document.createElement('form'),input=document.createElement('input'),button=document.createElement('button');
      input.name='body';input.placeholder='Reply';input.required=true;input.maxLength=4000;button.textContent='Reply';form.append(input,button);
      form.onsubmit=async event=>{event.preventDefault();try{await api('/alliances/'+alliance+'/wall/'+post.id+'/replies',serialize(form));await loadWall();}catch(e){message(e.message,true);}};
      box.append(form);$('wallPosts').append(box);
    }
    if(!data.posts.length)$('wallPosts').textContent='No posts yet.';
  }
  $('onlyMyTasks').onchange=()=>loadTasks().catch(error=>message(error.message,true));
  $('taskForm').onsubmit=async event=>{
    event.preventDefault();
    const form=new FormData(event.currentTarget);
    const schedule=Object.fromEntries(form.entries());
    for(const field of ['interval','month_day','month_week','month_weekday'])schedule[field]=Number(schedule[field]);
    schedule.weekdays=form.getAll('weekdays').map(Number);schedule.end_date=schedule.end_date||null;
    try{await api('/alliances/'+alliance+'/tasks',{title:form.get('title'),description:form.get('description'),
      assignee:form.get('assignee'),completion_rule:form.get('completion_rule'),schedule});message('Task assigned.');await loadTasks();}
    catch(error){message(error.message,true);}
  };
  $('wallForm').onsubmit=async event=>{event.preventDefault();try{await api('/alliances/'+alliance+'/wall',serialize(event.currentTarget));event.currentTarget.reset();await loadWall();}catch(e){message(e.message,true);}};
  async function loadAudit(){
    const query=new URLSearchParams(serialize($('auditForm')));
    const data=await api('/alliances/'+alliance+'/audit?'+query,undefined,'GET');
    $('auditSection').hidden=false;$('auditEvents').replaceChildren();
    for(const event of data.events){
      const line=document.createElement('p');
      line.textContent=event.created_at+' · '+event.actor_name+' · '+event.action;
      $('auditEvents').append(line);
    }
    if(!data.events.length)$('auditEvents').textContent='No matching activity.';
  }
  $('auditForm').onsubmit=event=>{event.preventDefault();loadAudit().catch(error=>message(error.message,true));};
  async function loadNotifications(){
    const data=await api('/notifications',undefined,'GET');
    $('unreadCount').textContent=data.unread?'('+data.unread+' unread)':'';
    $('notificationList').replaceChildren();
    for(const notification of data.notifications){
      const row=document.createElement('div');row.className='row';
      const button=document.createElement('button');
      button.textContent=(notification.read_at?'':'● ')+notification.title;
      button.onclick=async()=>{
        try{await api('/notifications/'+notification.id+'/read',{});navigate(notification.target);}
        catch(error){message(error.message,true);}
      };
      row.append(button);$('notificationList').append(row);
    }
    if(!data.notifications.length)$('notificationList').textContent='No notifications yet.';
  }
  async function loadMyClaims(){
    const data=await api('/my-claims',undefined,'GET');
    $('myClaims').replaceChildren();
    for(const claim of data.claims){
      const box=document.createElement('div'),title=document.createElement('p');
      box.id='my-claim-'+claim.id;
      title.textContent=claim.name+' · '+claim.player_name+' · '+claim.status.replace('_',' ');
      box.append(title);
      if(claim.response){const reply=document.createElement('p');reply.textContent=claim.response;box.append(reply);}
      if(claim.status==='more_proof'){
        const form=document.createElement('form');
        const file=document.createElement('input');file.type='file';file.name='screenshot';file.accept='image/png,image/jpeg,image/webp,image/gif';file.required=true;
        const note=document.createElement('input');note.name='note';note.placeholder='Optional note';
        const button=document.createElement('button');button.textContent='Send more proof';
        form.append(file,note,button);
        form.onsubmit=async event=>{
          event.preventDefault();
          try{const response=await fetch('/api/auth/claims/'+claim.id+'/more-proof',{method:'POST',body:new FormData(form)});
            const result=await response.json();if(!response.ok)throw Error(result.detail);message('Proof submitted.');await loadMyClaims();}
          catch(error){message(error.message,true);}
        };
        box.append(form);
      }
      $('myClaims').append(box);
    }
    $('myClaimsSection').hidden=!data.claims.length;
    const claimId=new URLSearchParams(location.search).get('claim');
    if(claimId&&!alliance)document.getElementById('my-claim-'+claimId)?.scrollIntoView();
  }
  async function loadClaims(member){
    const data=await api('/alliances/'+alliance+'/claims',undefined,'GET');
    $('claimReviewSection').hidden=false;$('claimReviews').replaceChildren();
    for(const claim of data.claims){
      const box=document.createElement('article');box.className='card';box.id='claim-'+claim.id;
      const title=document.createElement('h3');title.textContent=claim.player_name+' · '+claim.status.replace('_',' ');box.append(title);
      const detail=document.createElement('p');detail.textContent='Birthday '+claim.birthday+(claim.note?' · '+claim.note:'');box.append(detail);
      if(claim.already_claimed||claim.competing){
        const warning=document.createElement('p');warning.className='error';
        warning.textContent=(claim.already_claimed?'Already claimed. ':'')+(claim.competing?'Other claims are pending.':'');box.append(warning);
      }
      const proof=document.createElement('img');proof.src='/api/auth/alliances/'+alliance+'/claims/'+claim.id+'/proof';proof.alt='Submitted in-game screenshot';proof.style.maxWidth='100%';proof.style.maxHeight='350px';box.append(proof);
      if(claim.handled_by){const handled=document.createElement('p');handled.className='muted';handled.textContent='Handled by '+claim.handled_by+' · '+claim.handled_at;box.append(handled);}
      if(['pending','more_proof'].includes(claim.status)){
        const actions=document.createElement('div');actions.className='actions';
        for(const [action,label] of [['approve','Approve'],['more_proof','Ask for More Proof'],['reject','Reject']]){
          const button=document.createElement('button');button.textContent=label;
          button.onclick=async()=>{
            let confirm_conflicts=false,replace_existing=false;
            if(action==='approve'&&claim.competing){confirm_conflicts=confirm('Other claims are pending. Approve this claim?');if(!confirm_conflicts)return;}
            if(action==='approve'&&claim.already_claimed){replace_existing=confirm('This profile is already claimed. Replace its current link?');if(!replace_existing)return;}
            const note=action==='more_proof'?(prompt('What additional proof would help?')||''):'';
            try{await api('/alliances/'+alliance+'/claims/'+claim.id+'/decision',{action,message:note,confirm_conflicts,replace_existing});message('Claim updated.');await loadClaims(member);}
            catch(error){message(error.message,true);}
          };
          actions.append(button);
        }
        box.append(actions);
      }
      const discussion=document.createElement('details'),summary=document.createElement('summary');
      summary.textContent='Leadership discussion';discussion.append(summary);
      const comments=document.createElement('div');discussion.append(comments);
      const form=document.createElement('form'),input=document.createElement('input'),send=document.createElement('button');
      input.name='body';input.placeholder='Private leadership note';input.maxLength=1000;input.required=true;send.textContent='Add note';form.append(input,send);discussion.append(form);
      discussion.ontoggle=async()=>{if(!discussion.open)return;try{const result=await api('/alliances/'+alliance+'/claims/'+claim.id+'/comments',undefined,'GET');comments.replaceChildren();for(const comment of result.comments){const p=document.createElement('p');p.textContent=comment.display_name+' · '+comment.body;comments.append(p);}}catch(error){message(error.message,true);}};
      form.onsubmit=async event=>{event.preventDefault();try{await api('/alliances/'+alliance+'/claims/'+claim.id+'/comments',serialize(form));input.value='';discussion.open=false;discussion.open=true;}catch(error){message(error.message,true);}};
      box.append(discussion);$('claimReviews').append(box);
    }
    if(!data.claims.length)$('claimReviews').textContent='No player claims yet.';
    const claimId=new URLSearchParams(location.search).get('claim');
    if(claimId)document.getElementById('claim-'+claimId)?.scrollIntoView();
  }
  async function loadLeadership(member){
    $('leadershipSection').hidden=false;
    $('leadershipAlliance').textContent=member.name+' · '+member.role;
    $('leadershipInvite').hidden=member.role!=='R5';
    $('referralInvite').hidden=member.role!=='R5';
    $('r5Controls').hidden=member.role!=='R5';
    const invites=await api('/alliances/'+alliance+'/invites',undefined,'GET');
    $('invites').replaceChildren();
    for(const invite of invites.invites){
      const row=document.createElement('div');row.className='row';
      const label=document.createElement('span');
      label.textContent=invite.kind+' · expires '+invite.expires_at+(invite.revoked_at?' · revoked':'');
      row.append(label);
      if(!invite.revoked_at&&(member.role==='R5'||invite.kind==='claim')){
        const revoke=document.createElement('button');revoke.textContent='Revoke';
        revoke.onclick=async()=>{try{await api('/alliances/'+alliance+'/invites/'+invite.id+'/revoke',{});await loadLeadership(member);}catch(e){message(e.message,true);}};
        row.append(revoke);
      }
      $('invites').append(row);
    }
    if(member.role!=='R5')return;
    const result=await api('/alliances/'+alliance+'/members',undefined,'GET');
    $('members').replaceChildren();$('transferSelect').replaceChildren();
    for(const person of result.members){
      const row=document.createElement('div');row.className='row';
      const label=document.createElement('span');label.textContent=person.display_name+' · '+person.role;row.append(label);
      if(person.role==='R4'){
        const option=document.createElement('option');option.value=person.username;option.textContent=person.display_name;$('transferSelect').append(option);
        const remove=document.createElement('button');remove.textContent='Remove';
        remove.onclick=async()=>{if(!confirm('Remove '+person.display_name+' from leadership?'))return;try{await api('/alliances/'+alliance+'/members/'+encodeURIComponent(person.username),undefined,'DELETE');await refresh();}catch(e){message(e.message,true);}};
        row.append(remove);
      }
      $('members').append(row);
    }
    const visibility=await api('/alliances/'+alliance+'/visibility',undefined,'GET');
    $('visibilityForm').elements.timezone.value=visibility.timezone;
    $('fieldOptions').replaceChildren();
    for(const [field,labelText] of Object.entries({rank:'Role',power:'Power',hq_level:'HQ level',kills:'Kills',donations:'Donations'})){
      const label=document.createElement('label'),check=document.createElement('input');
      check.type='checkbox';check.name='fields';check.value=field;check.checked=visibility.fields.includes(field);
      label.append(check,document.createTextNode(' '+labelText));$('fieldOptions').append(label);
    }
  }
  $('loginForm').onsubmit=async event=>{
    event.preventDefault();
    try{
      const body=serialize(event.currentTarget);
      body.setup_code=new URLSearchParams(location.hash.slice(1)).get('setup')||'';
      await api('/login',body);await refresh();
    }catch(error){message(error.message,true);}
  };
  $('registerForm').onsubmit=async event=>{
    event.preventDefault();
    try{
      const result=await api('/register',{...serialize(event.currentTarget),code:inviteCode});
      if(result.invite_kind==='leadership'){navigate('/a/'+result.slug+'/manage');return;}
      await refresh();
    }catch(error){message(error.message,true);}
  };
  $('claimForm').onsubmit=async event=>{
    event.preventDefault();
    const body=new FormData(event.currentTarget);body.set('code',inviteCode);
    try{const response=await fetch('/api/auth/claims',{method:'POST',body});const result=await response.json();
      if(!response.ok)throw Error(result.detail||'Could not submit the claim.');
      $('claimSection').hidden=true;message('Your claim was sent to leadership.');history.replaceState(null,'','/account');inviteCode='';await refresh();}
    catch(error){message(error.message,true);}
  };
  $('myBirthdayForm').onsubmit=async event=>{
    event.preventDefault();
    try{await api('/my-birthday',serialize(event.currentTarget),'PATCH');message('Birthday updated.');}
    catch(error){message(error.message,true);}
  };
  let pushConfig=null;
  async function preparePush(){
    if(!('serviceWorker' in navigator)||!('PushManager' in window))return;
    try{await navigator.serviceWorker.register('/service-worker.js');pushConfig=await api('/push/config',undefined,'GET');}
    catch{}
  }
  $('enablePush').onclick=async()=>{
    if(!pushConfig?.configured){$('pushStatus').textContent='Phone notifications are being set up.';return;}
    try{
      const permission=await Notification.requestPermission();
      if(permission!=='granted')throw Error('Notifications are turned off for this device.');
      const padding='='.repeat((4-pushConfig.public_key.length%4)%4);
      const key=Uint8Array.from(atob((pushConfig.public_key+padding).replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
      const registration=await navigator.serviceWorker.ready;
      const subscription=await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});
      await api('/push/subscribe',subscription.toJSON());
      $('pushStatus').textContent='Phone notifications are on for this device.';
    }catch(error){$('pushStatus').textContent=error.message;}
  };
  $('disablePush').onclick=async()=>{
    try{
      const registration=await navigator.serviceWorker.ready;
      const subscription=await registration.pushManager.getSubscription();
      if(subscription){await api('/push/unsubscribe',subscription.toJSON());await subscription.unsubscribe();}
      $('pushStatus').textContent='Phone notifications are off for this device.';
    }catch(error){$('pushStatus').textContent=error.message;}
  };
  preparePush();
  $('inviteEntryForm').onsubmit=event=>{
    event.preventDefault();
    const value=serialize(event.currentTarget).invite.trim();
    try{const url=new URL(value);location.href=url.pathname;}catch{message('Paste the complete invitation link.',true);}
  };
  $('createAllianceForm').onsubmit=async event=>{
    event.preventDefault();
    try{const values=serialize(event.currentTarget);values.server=Number(values.server);const result=await api('/create-alliance',{...values,code:inviteCode});navigate('/a/'+result.slug+'/manage');}
    catch(error){message(error.message,true);}
  };
  $('passwordForm').onsubmit=async event=>{
    event.preventDefault();
    try{await api('/change-password',serialize(event.currentTarget));event.currentTarget.reset();message('Password changed.');}
    catch(error){message(error.message,true);}
  };
  $('logoutButton').onclick=async()=>{try{await api('/logout',{});navigate(alliance?'/a/'+alliance:'/account');}catch(e){message(e.message,true);}};
  async function createInvite(kind,output,slug){
    try{const result=await api('/invites',{kind,alliance_slug:slug||null});showInvite(result.code,kind,output);if(alliance)await refresh();}
    catch(error){message(error.message,true);}
  }
  $('claimInvite').onclick=()=>createInvite('claim',$('inviteOutput'),alliance);
  $('leadershipInvite').onclick=()=>createInvite('leadership',$('inviteOutput'),alliance);
  $('referralInvite').onclick=()=>createInvite('referral',$('inviteOutput'),alliance);
  $('platformReferral').onclick=()=>createInvite('referral',$('platformOutput'),null);
  $('platformSupportForm').onsubmit=async event=>{
    event.preventDefault();
    const values=serialize(event.currentTarget),action=event.submitter.value;
    try{
      if(action==='leadership'){
        const result=await api('/invites',{kind:'leadership',alliance_slug:values.slug,support_reason:values.support_reason});
        showInvite(result.code,'leadership',$('platformOutput'));
      }else{
        if(!values.username||!confirm('Transfer R5 in this alliance?'))return;
        await api('/alliances/'+encodeURIComponent(values.slug)+'/transfer-r5',
          {username:values.username,support_reason:values.support_reason});
        message('R5 transferred.');
      }
    }catch(error){message(error.message,true);}
  };
  $('transferForm').onsubmit=async event=>{
    event.preventDefault();
    const username=serialize(event.currentTarget).username;
    if(!username||!confirm('Transfer R5 to this leader? The current R5 becomes R4.'))return;
    try{await api('/alliances/'+alliance+'/transfer-r5',{username});message('R5 transferred.');await refresh();}
    catch(error){message(error.message,true);}
  };
  $('visibilityForm').onsubmit=async event=>{
    event.preventDefault();
    const form=new FormData(event.currentTarget);
    try{await api('/alliances/'+alliance+'/visibility',{fields:form.getAll('fields'),timezone:form.get('timezone')},'PUT');message('Viewer settings saved.');}
    catch(error){message(error.message,true);}
  };
  refresh().catch(error=>message(error.message,true));
})();

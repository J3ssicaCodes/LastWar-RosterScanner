/* A shared, searchable guide for alliance members and leaders. */
(() => {
  'use strict';
  const guides=[
    {group:'Everyone',title:'Open your alliance and sign in',body:`
      <p class="alliance-help-path">Alliance page → Leadership Login → Sign in</p>
      <ol><li>Open the alliance link shared by your leadership. The viewer shows VS standings and train assignments.</li><li>R4s and R5s: tap <strong>Leadership Login</strong> at the top of the page.</li><li>Enter your own username and password, then tap <strong>Sign in</strong>. In the workspace, tap your profile picture for account and leadership tools.</li></ol>
      <figure><img src="/help-images/viewer.webp" alt="Alliance viewer showing Leadership Login beside Install App and Help" loading="lazy"><figcaption>On the alliance page, tap Leadership Login to open your workspace.</figcaption></figure>
      <figure><img src="/help-images/signin.webp" alt="Sign-in screen with username and password fields and an invite entry box" loading="lazy"><figcaption>Enter your own account details here. If you received an invite code, use Have an invite?</figcaption></figure>
      <p>The viewer link is shareable: anyone who receives it can see what the alliance has chosen to make public.</p>`},
    {group:'Everyone',title:'Claim your player profile and add your birthday',body:`
      <p class="alliance-help-path">Claim link → Sign in → Choose name → Submit proof</p><ol><li>Open the player claim link provided by an R4 or R5. Sign in or create your own account.</li><li>Choose your existing roster name and enter your birthday as <strong>month and day only</strong>.</li><li>Upload a useful in-game screenshot, such as your profile or a Power Ranking screenshot showing your green-highlighted row. Add a short note if it helps identify you.</li><li>Submit the claim and wait for leadership to review it. After approval, you can update your birthday from your account.</li></ol>
      <p>Your birthday and proof are private and are not shown on the public viewer.</p>`},
    {group:'Leadership',title:'Create a birthday and player claim link',body:`
      <p class="alliance-help-path">Profile picture → Leadership tools → Create player claim link</p><ol><li>Sign in and tap your profile picture in the top-right corner.</li><li>Under <strong>Leadership tools</strong>, tap <strong>Create player claim link</strong>.</li><li>Copy the link that appears and share it in alliance chat.</li></ol>
      <p>R4s and R5s can create the link. Multiple players can use it; it expires after seven days. You can revoke an active link and create another when needed.</p>`},
    {group:'Leadership',title:'Review a player claim',body:`
      <p class="alliance-help-path">Profile picture → Player Claims → Review</p><ol><li>Open your profile picture and scroll to <strong>Player Claims</strong>, or open the claim from the Leadership Inbox.</li><li>Review the roster name, requested birthday, screenshot, and any note together.</li><li>Select <strong>Approve</strong>, <strong>Ask for More Proof</strong>, or <strong>Reject</strong>.</li></ol>
      <p>Approval links the account to the existing player and saves the month and day. Warnings identify competing claims or names already claimed; review those carefully before replacing anything. Leadership discussion stays with the claim.</p>`},
    {group:'Leadership',title:'Upload media and use Scan Queue',body:`
      <ol><li>Choose the appropriate upload action for <strong>Roster Scanner</strong>, <strong>Player List</strong>, or <strong>VS Points</strong>.</li><li>Select screenshots or a supported recording and start the upload. Storm Roster accepts screenshots; Player List and VS also accept video.</li><li>Open <strong>Scan Queue</strong> to see progress, errors, and work needing review. Open a review, correct flagged rows, and save or dismiss them.</li></ol>
      <p>If a scan fails, check the message and try uploading again. If Google Drive selection is unavailable, use your device’s file picker. Make sure the scan is finished before removing an original file.</p>`},
    {group:'Leadership',title:'Plan Storm rosters and event maps',body:`
      <ol><li>In <strong>Roster Scanner</strong>, choose an event and upload screenshots of Task Force A, B, or both. Keep the team heading visible.</li><li>Review names, ranks, power, participants, and substitutes; use the pencil to correct a row.</li><li>Adjust Phase 1 and Phase 2 assignments, then generate and download the maps. Use <strong>Manage events</strong> to adjust event layouts and priorities.</li></ol>`},
    {group:'Leadership',title:'Update the Player List and manage archives',body:`
      <p>Use <strong>Player List</strong> to browse, sort, export, and open player profiles. Upload leaderboard screenshots or a recording to update stats; review flagged changes before saving.</p>
      <p>For a full roster video, review any prompts about missing or returning players. Archived players are hidden from active counts while their history is preserved. Find <strong>Player Archive</strong> in Admin to reactivate them.</p>`},
    {group:'Leadership',title:'View and review VS scores',body:`
      <p>Open <strong>VS Points</strong> to move between weeks and export scores. Upload a Daily Rank screenshot or recording, check the identified day, and review flagged rows before saving. If the scan cannot identify the weekday, choose it during review.</p>
      <p>A dash means a score was not recorded; <strong>0</strong> is a recorded zero. On a player profile, the weekly chart compares this week and last week against the dotted 7.2M goal line.</p>`},
    {group:'Leadership',title:'Assign a train and handle a dice-roll winner',body:`
      <ol><li>Open <strong>Train Manager</strong>, choose the date and goal, and review the nominees and eligibility reasons.</li><li>Choose a Conductor and select <strong>Wheel of Fortune</strong>, <strong>Manual VIP</strong>, or <strong>Dice Roll</strong>.</li><li>Save the assignment. For a dice roll, save with VIP pending, then return to the assignment and record the winner when known.</li></ol>
      <p>Check train history and rotation before assigning. An R4 or R5 can also manage restrictions in Admin’s <strong>Naughty List</strong>.</p>`},
    {group:'Leadership',title:'Prepare and reuse train emails',body:`
      <p>In <strong>Train Manager</strong>, choose an email template or the random option, generate the draft, review it, and copy it for posting in-game. You can edit the draft before sharing.</p>
      <p>Save an email to reopen and copy it later from <strong>Saved alliance emails</strong>. Manage the editable starter templates in Admin.</p>`},
    {group:'Leadership',title:'Invite leaders or refer another alliance',body:`
      <p>Tap your profile picture and open <strong>Leadership tools</strong>. An R5 can create a shared <strong>R4 team invite</strong>; each person signs in with their own account and redeems it. Revoking the code stops new redemptions without removing existing members.</p>
      <p>An R5 or Platform Admin can create a separate <strong>alliance referral</strong> for someone to create a new alliance. The new creator becomes its R5; the referrer does not automatically join it. Transferring R5 is a separate action.</p>`},
    {group:'Leadership',title:'Inbox, wall, tasks, and notifications',body:`
      <p>Tap your profile picture to open <strong>Leadership Inbox</strong>, <strong>My Tasks</strong>, and the private <strong>Leadership Wall</strong>. Inbox items link to claims, scan work, and pending dice-roll winners.</p>
      <p>Assign one-off or recurring tasks with the alliance time zone. Each occurrence can be completed, skipped, or reassigned. Use the bell for unread notifications; opening one does not complete the underlying work. Phone notifications are optional and depend on the device and browser.</p>`},
    {group:'Leadership',title:'Performance, audit history, and Clear Data',body:`
      <p>Use <strong>Player Performance</strong> and <strong>This week at a glance</strong> in the account area to inspect VS coverage, trends, and train rotation. Missing scores and recorded zeroes are different.</p>
      <p>R4s and R5s can search their alliance’s <strong>Audit Log</strong> from the account area. Only an R5 can use <strong>Clear Data</strong>; select tables carefully because it removes the selected alliance data. The audit log and OCR dictionary are preserved.</p>`},
  ];
  function init(){
    if(window.self!==window.top)return; // The workspace already has a Help button above embedded account tools.
    const target=document.getElementById('workspaceUtilities')||document.querySelector('header .actions')||document.querySelector('main');
    if(!target)return;
    const style=document.createElement('style');style.textContent=`
      .alliance-help-trigger{display:inline-flex;align-items:center;justify-content:center;gap:5px;min-height:42px;padding:8px 13px;border:1px solid var(--stroke,var(--account-border,#72688d));border-radius:10px;background:var(--panel2,var(--account-input,#342b4c));color:var(--text,var(--account-text,#fff));font:inherit;font-weight:750;cursor:pointer}
      .workspace-utilities .alliance-help-trigger{flex:0 0 auto;width:auto;min-width:0}
      dialog.alliance-help-dialog{width:min(760px,calc(100vw - 24px));max-width:760px;max-height:min(88vh,900px);overflow:auto;padding:20px;border:1px solid var(--stroke,var(--account-border,#625b7b));border-radius:16px;background:var(--panel,var(--account-panel,#1c2038));color:var(--text,var(--account-text,#f7f4ff));box-shadow:0 20px 70px #0008}
      dialog.alliance-help-dialog::backdrop{background:#090615b8}
      .alliance-help-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.alliance-help-head h2{margin:0 0 4px}.alliance-help-head p{margin:0;color:var(--muted,var(--account-muted,#bcb9d2))}
      .alliance-help-close{flex:none;min-width:42px;min-height:42px;border:1px solid var(--stroke,var(--account-border,#72688d));border-radius:10px;background:var(--panel2,var(--account-input,#342b4c));color:inherit;font-size:20px;cursor:pointer}
      .alliance-help-search{display:block;margin:16px 0 10px;font-size:13px;font-weight:700}.alliance-help-search input{display:block;width:100%;margin-top:5px;padding:10px 12px;border:1px solid var(--stroke,var(--account-border,#72688d));border-radius:9px;background:var(--panel2,var(--account-input,#17182d));color:inherit;font:inherit}
      .alliance-help-shortcuts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:12px 0 18px}.alliance-help-shortcuts button{padding:12px;text-align:left;border:1px solid var(--stroke,var(--account-border,#625b7b));border-radius:10px;background:var(--panel2,var(--account-input,#342b4c));color:inherit;font:inherit;font-weight:700;cursor:pointer}.alliance-help-shortcuts button span{display:block;margin-top:4px;font-size:12px;font-weight:400;color:var(--muted,var(--account-muted,#bcb9d2))}.alliance-help-heading{font-size:15px;margin:18px 0 8px}.alliance-help-path{padding:9px 11px;border-left:3px solid var(--cyan,var(--account-accent,#c6abed));background:var(--panel,var(--account-panel,#1c2038));border-radius:4px;font-weight:700;overflow-wrap:anywhere}.alliance-help-dialog figure{margin:16px 0}.alliance-help-dialog figure img{display:block;width:100%;height:auto;border:1px solid var(--stroke,var(--account-border,#625b7b));border-radius:8px}.alliance-help-dialog figcaption{margin-top:6px;color:var(--muted,var(--account-muted,#bcb9d2));font-size:12px;line-height:1.4}
      .alliance-help-dialog details{border:1px solid var(--stroke,var(--account-border,#4b4861));border-radius:10px;margin:8px 0;padding:12px 14px;background:var(--panel2,var(--account-input,#24233a))}
      .alliance-help-dialog summary{cursor:pointer;font-weight:750;line-height:1.4}.alliance-help-group{display:block;margin-bottom:3px;color:var(--cyan,var(--account-accent,#e6a1d5));font-size:11px;letter-spacing:.05em;text-transform:uppercase}
      .alliance-help-dialog p,.alliance-help-dialog li{font-size:14px;line-height:1.55}.alliance-help-dialog ol{padding-left:22px}.alliance-help-dialog li+li{margin-top:6px}
      .alliance-help-dialog :focus-visible,.alliance-help-trigger:focus-visible{outline:2px solid var(--cyan,var(--account-accent,#e6a1d5));outline-offset:2px}
      @media(max-width:600px){dialog.alliance-help-dialog{padding:15px;max-height:90vh}.alliance-help-shortcuts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:12px 0 18px}.alliance-help-shortcuts button{padding:12px;text-align:left;border:1px solid var(--stroke,var(--account-border,#625b7b));border-radius:10px;background:var(--panel2,var(--account-input,#342b4c));color:inherit;font:inherit;font-weight:700;cursor:pointer}.alliance-help-shortcuts button span{display:block;margin-top:4px;font-size:12px;font-weight:400;color:var(--muted,var(--account-muted,#bcb9d2))}.alliance-help-heading{font-size:15px;margin:18px 0 8px}.alliance-help-path{padding:9px 11px;border-left:3px solid var(--cyan,var(--account-accent,#c6abed));background:var(--panel,var(--account-panel,#1c2038));border-radius:4px;font-weight:700;overflow-wrap:anywhere}.alliance-help-dialog figure{margin:16px 0}.alliance-help-dialog figure img{display:block;width:100%;height:auto;border:1px solid var(--stroke,var(--account-border,#625b7b));border-radius:8px}.alliance-help-dialog figcaption{margin-top:6px;color:var(--muted,var(--account-muted,#bcb9d2));font-size:12px;line-height:1.4}
      .alliance-help-dialog details{padding:11px}.alliance-help-dialog p,.alliance-help-dialog li{font-size:14px}}
    `;document.head.append(style);
    const button=document.createElement('button');button.type='button';button.className='alliance-help-trigger';button.textContent='? Help';button.setAttribute('aria-haspopup','dialog');
    if(target.matches('main'))target.insertBefore(button,target.firstChild);else target.append(button);
    const dialog=document.createElement('dialog');dialog.className='alliance-help-dialog';dialog.setAttribute('aria-labelledby','allianceHelpTitle');
    dialog.innerHTML=`<div class="alliance-help-head"><div><h2 id="allianceHelpTitle">Alliance Tools Help</h2><p>What would you like to do? Choose a guide or search below.</p></div><button type="button" class="alliance-help-close" aria-label="Close help">×</button></div><label class="alliance-help-search">Search guides<input type="search" placeholder="Try birthday, scans, trains…" autocomplete="off"></label><div class="alliance-help-start"><h3 class="alliance-help-heading">Popular guides</h3><div class="alliance-help-shortcuts"><button type="button" data-guide="1">🎂 Add my birthday<span>Claim your player profile</span></button><button type="button" data-guide="2">🔗 Share a birthday link<span>Create a link for players</span></button><button type="button" data-guide="4">📷 Upload a scan<span>Upload and review results</span></button><button type="button" data-guide="8">🚂 Assign a train<span>Choose a conductor and VIP</span></button></div><h3 class="alliance-help-heading">All guides</h3></div><p class="alliance-help-empty" hidden>No matching guides. Try another word.</p><div class="alliance-help-guides"></div>`;
    const list=dialog.querySelector('.alliance-help-guides');
    for(const [index,guide] of guides.entries()){
      const section=document.createElement('details');section.dataset.guide=index;section.dataset.search=(guide.group+' '+guide.title+' '+guide.body.replace(/<[^>]+>/g,' ')).toLowerCase();
      section.innerHTML=`<summary><span class="alliance-help-group">${guide.group}</span>${guide.title}</summary>${guide.body}`;
      list.append(section);
    }
    document.body.append(dialog);
    const search=dialog.querySelector('input'),sections=[...list.querySelectorAll('details')];
    search.addEventListener('input',()=>{
      const words=search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);let count=0;dialog.querySelector('.alliance-help-start').hidden=words.length>0;
      for(const section of sections){const visible=words.every(word=>section.dataset.search.includes(word));section.hidden=!visible;if(words.length&&visible)section.open=true;count+=Number(visible);}
      dialog.querySelector('.alliance-help-empty').hidden=count>0;
    });
    dialog.querySelectorAll('[data-guide]').forEach(shortcut=>{if(shortcut.tagName!=='BUTTON')return;shortcut.addEventListener('click',()=>{const section=sections[Number(shortcut.dataset.guide)];section.open=true;section.scrollIntoView({behavior:'smooth',block:'start'});section.querySelector('summary').focus();});});
    button.addEventListener('click',()=>{dialog.showModal();});
    dialog.querySelector('.alliance-help-close').addEventListener('click',()=>dialog.close());
    dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();

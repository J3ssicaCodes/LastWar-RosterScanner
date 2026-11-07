/* Optional Google Drive selection for Media Upload.
 * Uses Google's own account chooser and Picker with drive.file scope.
 * The short-lived access token is never persisted in browser storage.
 */
(() => {
  'use strict';
  const SCOPE='https://www.googleapis.com/auth/drive.file';
  let config=null;
  let ready=false;
  let errorMessage='';
  let prep=null;
  let picking=false;

  function externalScript(url){
    return new Promise((resolve,reject)=>{
      const script=document.createElement('script');
      script.src=url;script.async=true;
      script.onload=()=>resolve();
      script.onerror=()=>{
        script.remove();
        reject(Error('Could not load Google sign in. Check your connection.'));
      };
      document.head.append(script);
    });
  }

  function prepare(){
    if(prep)return prep;
    prep=(async()=>{
      const response=await fetch('/api/media/google-config',{cache:'no-store'});
      if(!response.ok)throw Error('Google Drive configuration is unavailable.');
      config=await response.json();
      if(!config.configured)
        throw Error('Google Drive needs one-time setup by the site owner before it can be linked.');
      await Promise.all([
        window.google?.accounts?.oauth2 || externalScript('https://accounts.google.com/gsi/client'),
        window.gapi || externalScript('https://apis.google.com/js/api.js')
      ]);
      if(!window.google?.accounts?.oauth2||!window.gapi)
        throw Error('Google sign in could not load. Please try again.');
      await new Promise((resolve,reject)=>{
        window.gapi.load('picker',{callback:resolve,onerror:()=>reject(Error('Google Drive picker could not load.'))});
      });
      errorMessage='';
      ready=true;
    })().catch(err=>{prep=null;errorMessage=err.message;throw err;});
    return prep;
  }

  function showPicker(callbacks,accessToken,expiresAt){
    const gp=google.picker;
    const view=new gp.DocsView(gp.ViewId.DOCS);
    view.setMode(gp.DocsViewMode.LIST);
    view.setIncludeFolders(true);
    view.setMimeTypes(callbacks.destination==='storm'
      ? 'image/png,image/jpeg,image/webp'
      : 'image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm,video/x-m4v');
    const picker=new gp.PickerBuilder()
      .addView(view)
      .setOAuthToken(accessToken)
      .setDeveloperKey(config.api_key)
      .setAppId(config.project_number)
      .setOrigin(window.location.origin)
      .setCallback(data=>{
        if(data.action===gp.Action.CANCEL){
          callbacks.onCancel?.();
          return;
        }
        if(data.action===gp.Action.PICKED){
          const doc=(data[gp.Response.DOCUMENTS]||[])[0];
          if(!doc){
            callbacks.onError?.(Error('No Google Drive file selected.'));
            return;
          }
          const id=doc[gp.Document.ID]||doc.id;
          const name=doc[gp.Document.NAME]||doc.name||'Selected file';
          const mime=doc[gp.Document.MIME_TYPE]||doc.mimeType||'';
          if(!/^[A-Za-z0-9_-]{8,256}$/.test(String(id||''))){
            callbacks.onError?.(Error('Invalid Google Drive file selection.'));
            return;
          }
          const ext=String(name).split('.').pop().toLowerCase();
          const isImage=/^image\/(png|jpeg|webp)$/.test(mime)||['jpg','jpeg','png','webp'].includes(ext);
          const isVideo=/^video\/(mp4|quicktime|webm|x-m4v)$/.test(mime)||['mp4','mov','m4v','webm'].includes(ext);
          if(!(callbacks.destination==='storm'?isImage:(isImage||isVideo))){
            callbacks.onError?.(Error(callbacks.destination==='storm'
              ? 'Storm Roster only accepts screenshots.'
              : 'Choose a supported screenshot or video.'));
            return;
          }
          callbacks.onSelected?.({
            file_id:String(id),name:String(name),mime_type:String(mime),
            access_token:accessToken,expires_at:expiresAt
          });
        }
      }).build();
    picker.setVisible(true);
  }

  // This is called directly by a user click. Do not await inside the
  // Google OAuth popup gesture: browsers may otherwise block sign in.
  function choose(callbacks){
    if(!ready){
      callbacks.onError?.(Error(errorMessage||'Google Drive is loading. Tap again shortly.'));
      return;
    }
    if(picking){
      callbacks.onError?.(Error('Finish the open Google Drive selection first.'));
      return;
    }
    picking=true;
    let finished=false;
    const finish=handler=>value=>{
      if(finished)return;
      finished=true;
      picking=false;
      handler?.(value);
    };
    const selected=finish(callbacks.onSelected);
    const cancel=finish(callbacks.onCancel);
    const fail=finish(callbacks.onError);
    try{
      const oauth=google.accounts.oauth2;
      const client=oauth.initTokenClient({
        client_id:config.client_id,scope:SCOPE,include_granted_scopes:false,
        error_callback:error=>{
          if(error?.type==='popup_closed')cancel();
          else fail(Error('Google sign in could not open. Allow popups and try again.'));
        },
        callback:response=>{
          if(finished)return;
          if(!response||response.error||!response.access_token||!oauth.hasGrantedAllScopes(response,SCOPE)){
            fail(Error('Google Drive access was not granted. Choose a Google account and try again.'));
            return;
          }
          const expiresAt=Date.now()+(Number(response.expires_in)||3600)*1000;
          try{
            showPicker({destination:callbacks.destination,onSelected:selected,onCancel:cancel,onError:fail},
              response.access_token,expiresAt);
          }catch{
            fail(Error('Could not open Google Drive. Try selecting the file again.'));
          }
        }
      });
      client.requestAccessToken({prompt:'select_account'});
    }catch{
      fail(Error('Could not open Google sign in. Try again.'));
    }
  }

  window.AllianceGoogleDrive={prepare,choose,isReady:()=>ready,
    error:()=>errorMessage};
  // Initialize libraries early so the picker click remains an actual user gesture.
  prepare().catch(()=>{});
})();

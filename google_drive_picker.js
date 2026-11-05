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
  let tokenClient=null;
  let accessToken=null;
  let expiresAt=0;
  let selectionCallbacks=null;

  function externalScript(url){
    return new Promise((resolve,reject)=>{
      const script=document.createElement('script');
      script.src=url;script.async=true;
      script.onload=()=>resolve();
      script.onerror=()=>reject(Error('Could not load Google sign in. Check your connection.'));
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
        externalScript('https://accounts.google.com/gsi/client'),
        externalScript('https://apis.google.com/js/api.js')
      ]);
      if(!window.google?.accounts?.oauth2||!window.gapi)
        throw Error('Google sign in could not load. Please try again.');
      await new Promise((resolve,reject)=>{
        window.gapi.load('picker',{callback:resolve,onerror:()=>reject(Error('Google Drive picker could not load.'))});
      });
      tokenClient=google.accounts.oauth2.initTokenClient({
        client_id:config.client_id,scope:SCOPE,
        error_callback:error=>{
          if(error?.type==='popup_closed')selectionCallbacks?.onCancel?.();
          else selectionCallbacks?.onError?.(Error('Google sign in could not open. Allow popups and try again.'));
        },
        callback:response=>{
          if(!response||response.error||!response.access_token){
            selectionCallbacks?.onError?.(Error('Google Drive access was not granted.'));
            return;
          }
          accessToken=response.access_token;
          expiresAt=Date.now()+Math.max(60,Number(response.expires_in)||3500)*1000;
          showPicker();
        }
      });
      ready=true;
    })().catch(err=>{errorMessage=err.message;throw err;});
    return prep;
  }

  function showPicker(){
    const callbacks=selectionCallbacks;
    if(!callbacks||!accessToken||!window.google?.picker){
      callbacks?.onError?.(Error('Google Drive authorization is not ready.'));
      return;
    }
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
    selectionCallbacks=callbacks;
    if(!ready){
      callbacks.onError?.(Error(errorMessage||'Google Drive is loading. Tap again shortly.'));
      return;
    }
    if(accessToken&&Date.now()<expiresAt-90000){
      showPicker();
    }else{
      accessToken=null;
      tokenClient.requestAccessToken({prompt:'consent'});
    }
  }

  window.AllianceGoogleDrive={prepare,choose,isReady:()=>ready,
    error:()=>errorMessage};
  // Initialize libraries early so the picker click remains an actual user gesture.
  prepare().catch(()=>{});
})();

/* Export only local reference evidence. Preparing or downloading never refreshes NSX. */
(() => {
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;return node;};
  const endpoint=()=>new URL('reference-exports/',location.href.split('#')[0].split('?')[0]);
  const request=async(url,options={})=>{
    const response=await fetch(url,{credentials:'same-origin',...options,headers:{Accept:'application/json',...options.headers}});
    if(response.redirected)throw new Error('Your session has expired. Reload to sign in.');
    let data;try{data=await response.json();}catch(_){throw new Error('Unable to prepare this export. Please retry.');}
    if(!response.ok)throw new Error(data.error||'Unable to prepare this export. Please retry.');return data;
  };
  const post=data=>({method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('#interface-csrf input')?.value||''},body:JSON.stringify(data)});
  window.referenceExportControl=(identity,entries,selected)=>{
    const toolbar=make('div'),button=make('button','Export references');toolbar.className='reference-export-toolbar';button.type='button';toolbar.append(button);
    button.addEventListener('click',()=>{
      const previous=document.querySelector('dialog.reference-export-dialog');if(previous)previous.close();
      const dialog=make('dialog'),header=make('header'),title=make('h2','Export references'),close=make('button','Close');
      dialog.className='reference-export-dialog';title.id='reference-export-title';dialog.setAttribute('aria-labelledby',title.id);close.type='button';close.setAttribute('aria-label','Close export dialog');header.append(title,close);
      const form=make('form'),format=make('select'),scope=make('select'),source=make('select');
      const field=(label,select)=>{const wrapper=make('div'),caption=make('label',label);wrapper.className='reference-export-field';select.id='reference-export-'+label.toLowerCase();caption.htmlFor=select.id;wrapper.append(caption,select);return wrapper;};
      for(const [value,label] of [['csv','CSV — spreadsheet'],['pdf','PDF — report'],['json','JSON — structured data'],['yaml','YAML — structured data'],['html','HTML — offline report']]){const option=make('option',label);option.value=value;format.append(option);}
      const section=entries[selected()]?.exportSection||entries[0]?.exportSection;
      for(const entry of entries.filter(entry=>entry.exportSection)){const option=make('option',entry.label);option.value=entry.exportSection;scope.append(option);}const all=make('option','All reference categories');all.value='all';scope.append(all);scope.value=section;
      const saved=make('option','Saved snapshot');saved.value='saved_snapshot';source.append(saved);
      if(identity.view==='vms'){const current=make('option','Cached current direct associations + saved links');current.value='current_cache';source.append(current);}
      const note=make('p','Includes every page in the selected category. Coverage and evidence timestamps are retained. No NSX check is started. Downloads expire after 24 hours.');note.className='reference-export-help';
      const sourceNote=make('p','Saved evidence is unchanged. Cached current observations, if selected, are frozen when requested; saved attachments, IP matches and rule configuration retain their own timestamps.');sourceNote.className='reference-export-help';sourceNote.hidden=true;source.addEventListener('change',()=>{sourceNote.hidden=source.value!=='current_cache';});
      const actions=make('div'),prepare=make('button','Prepare export'),cancel=make('button','Cancel export'),download=make('a','Download file'),status=make('p');
      actions.className='reference-export-actions';prepare.type='submit';prepare.className='primary';cancel.type='button';cancel.hidden=true;download.className='button primary';download.hidden=true;download.setAttribute('download','');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      actions.append(prepare,cancel,download);form.append(field('Format',format),field('Include',scope),field('Evidence',source),note,sourceNote,actions,status);
      const recent=make('details'),recentTitle=make('summary','Recent exports from this snapshot'),list=make('ul');recent.append(recentTitle,list);dialog.append(header,form,recent);document.body.append(dialog);
      let alive=true,timer=null,jobUrl=null,key=null,lastSelection=null;
      const render=data=>{
        const pending=['queued','running'].includes(data.status);prepare.disabled=pending;prepare.classList.toggle('primary',data.status!=='ready');cancel.hidden=!pending;download.hidden=data.status!=='ready';
        if(data.download_url){download.href=data.download_url;download.textContent='Download '+data.format.toUpperCase();}
        status.textContent=data.status==='queued'?'Export queued. You can close this dialog while it prepares.':data.status==='running'?'Preparing all reference pages…':data.status==='ready'?'Ready. Includes all retained references for your selection.':data.error||('Export '+data.status+'.');
        if(pending&&alive)timer=setTimeout(poll,2000);
        else if(alive){key=null;loadRecent();}
      };
      const poll=async()=>{try{const data=await request(jobUrl);if(alive)render(data);}catch(error){if(alive){status.textContent=error.message;prepare.disabled=false;cancel.hidden=true;}}};
      const loadRecent=async()=>{try{const data=await request(endpoint());if(!alive)return;list.replaceChildren();for(const item of data.items){const li=make('li'),label=make('span',item.filename||((item.object_name||'Object')+' · '+item.format.toUpperCase()+' · '+item.section.replaceAll('_',' ')+' · '+item.status));li.append(label);if(item.status==='ready'){const link=make('a','Download');link.href=item.download_url;link.setAttribute('download','');link.setAttribute('aria-label','Download '+(item.filename||item.format));li.append(link);}else if(['queued','running'].includes(item.status)){const resume=make('button','Check progress');resume.type='button';resume.onclick=()=>{clearTimeout(timer);jobUrl=new URL(item.status_url,location.origin);render(item);};li.append(resume);}list.append(li);}if(!data.items.length)list.append(make('li','No exports prepared yet.'));}catch(error){if(alive)list.replaceChildren(make('li',error.message));}};
      form.addEventListener('submit',async event=>{event.preventDefault();clearTimeout(timer);prepare.disabled=true;download.hidden=true;status.textContent='Requesting export…';const choice={...identity,format:format.value,section:scope.value,source:source.value};const signature=JSON.stringify(choice);if(signature!==lastSelection||!key){key=crypto.randomUUID();lastSelection=signature;}try{const data=await request(endpoint(),post({...choice,request_key:key}));if(!alive)return;jobUrl=new URL(data.status_url,location.origin);render(data);}catch(error){if(alive){status.textContent=error.message;prepare.disabled=false;}}});
      cancel.addEventListener('click',async()=>{clearTimeout(timer);cancel.disabled=true;try{const data=await request(jobUrl,post({}));if(alive)render(data);}catch(error){if(alive)status.textContent=error.message;}finally{cancel.disabled=false;}});
      close.onclick=()=>dialog.close();dialog.addEventListener('close',()=>{alive=false;clearTimeout(timer);dialog.remove();button.focus();},{once:true});
      dialog.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const controls=[...dialog.querySelectorAll('button,select,input,a[href],summary,[tabindex]')].filter(node=>{const collapsed=node.closest('details:not([open])');return !node.disabled&&node.getAttribute('tabindex')!=='-1'&&node.getClientRects().length&&(!collapsed||node===collapsed.querySelector(':scope > summary'));});const first=controls[0],last=controls.at(-1);if(!first)return;if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if((!event.shiftKey&&document.activeElement===last)||!dialog.contains(document.activeElement)){event.preventDefault();first.focus();}});
      dialog.showModal();format.focus();loadRecent();
    });return toolbar;
  };
})();

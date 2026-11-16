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
      const form=make('form'),format=make('select'),source=make('select');
      const field=(label,select)=>{const wrapper=make('div'),caption=make('label',label);wrapper.className='reference-export-field';select.id='reference-export-'+label.toLowerCase();caption.htmlFor=select.id;wrapper.append(caption,select);return wrapper;};
      for(const [value,label] of [['csv','CSV — one file per category'],['xlsx','Excel — one sheet per category'],['pdf','PDF — report'],['html','HTML — offline report'],['json','JSON — structured data'],['yaml','YAML — structured data']]){const option=make('option',label);option.value=value;format.append(option);}
      const saved=make('option','Saved snapshot');saved.value='saved_snapshot';source.append(saved);
      if(identity.view==='vms'){const current=make('option','Cached current direct associations + saved links');current.value='current_cache';source.append(current);}
      // Remembered per object type; never includes object identities.
      const memoryKey='reference-export:'+identity.view;let memory={};try{memory=JSON.parse(localStorage.getItem(memoryKey)||'{}')||{};}catch(_){memory={};}
      const remember=value=>{try{localStorage.setItem(memoryKey,JSON.stringify(value));}catch(_){}};
      const available=entries.filter(entry=>entry.exportSection),open=entries[selected()]?.exportSection||available[0]?.exportSection;
      let schemas=null,schemaReady;const schemaLoaded=new Promise(resolve=>{schemaReady=resolve;});
      const categories=make('fieldset'),legend=make('legend','Categories and columns');categories.className='reference-export-categories';categories.append(legend);
      const rows=available.map(entry=>{
        const key=entry.exportSection,row=make('div'),check=make('input'),label=make('label',entry.label),preset=make('select'),custom=make('div');
        row.className='reference-export-category';check.type='checkbox';check.id='reference-export-category-'+key;label.htmlFor=check.id;
        check.checked=Array.isArray(memory.sections)?memory.sections.includes(key):key===open;
        for(const [value,text] of [['essential','Essential columns'],['full','All columns'],['custom','Choose columns']]){const option=make('option',text);option.value=value;preset.append(option);}
        preset.setAttribute('aria-label','Columns for '+entry.label);preset.value=memory.presets?.[key]||'essential';
        custom.className='reference-export-columns';custom.hidden=true;custom.setAttribute('role','group');custom.setAttribute('aria-label','Columns for '+entry.label);
        row.append(check,label,preset,custom);categories.append(row);
        return {key,label:entry.label,check,preset,custom};
      });
      const context=make('select'),raw=make('input'),rawLabel=make('label','Include raw evidence (JSON)'),rawRow=make('div');
      for(const [value,text] of [['separate','Separate about file or sheet'],['rows','On every row']]){const option=make('option',text);option.value=value;context.append(option);}
      context.value=memory.context==='rows'?'rows':'separate';raw.type='checkbox';raw.id='reference-export-raw';raw.checked=memory.raw===true;rawLabel.htmlFor=raw.id;rawRow.className='reference-export-check';rawRow.append(raw,rawLabel);
      if(memory.format&&[...format.options].some(option=>option.value===memory.format))format.value=memory.format;
      const preview=make('p');preview.className='reference-export-preview';preview.setAttribute('aria-live','polite');
      const columnsFor=row=>{
        const schema=schemas?.[row.key];if(!schema)return null;
        if(row.preset.value==='full')return schema.map(column=>column.key);
        if(row.preset.value==='custom')return [...row.custom.querySelectorAll('input:checked')].map(input=>input.value);
        return schema.filter(column=>column.essential||column.required).map(column=>column.key);
      };
      const drawColumns=row=>{
        const schema=schemas?.[row.key];row.custom.hidden=row.preset.value!=='custom'||!row.check.checked;if(!schema||row.custom.childElementCount)return;
        const chosen=memory.columns?.[row.key];
        for(const column of schema){const id='reference-export-'+row.key+'-'+column.key,wrap=make('div'),input=make('input'),label=make('label',column.label+(column.required?' (always included)':''));
          input.type='checkbox';input.id=id;input.value=column.key;label.htmlFor=id;input.checked=column.required||(Array.isArray(chosen)?chosen.includes(column.key):column.essential);input.disabled=column.required;
          input.addEventListener('change',update);wrap.append(input,label);row.custom.append(wrap);}
      };
      function update(){
        rows.forEach(row=>{row.preset.disabled=!row.check.checked;drawColumns(row);});
        const chosen=rows.filter(row=>row.check.checked),count=chosen.reduce((total,row)=>total+(columnsFor(row)?.length||0),0)+(raw.checked?chosen.length:0);
        const shape=format.value==='csv'?(chosen.length===1&&context.value==='rows'?'one CSV file':'ZIP with one CSV per category and about.csv'):format.value==='xlsx'?'one sheet per category plus About':format.value.toUpperCase()+' file';
        preview.textContent=chosen.length?chosen.length+' categor'+(chosen.length===1?'y':'ies')+(schemas?' · '+count+' columns':'')+' · '+shape+'. Rows are counted when the export is prepared.':'Choose at least one category.';
        prepare.disabled=!chosen.length||prepare.dataset.pending==='true';
      }
      rows.forEach(row=>{row.check.addEventListener('change',update);row.preset.addEventListener('change',update);});
      [format,context,raw].forEach(control=>control.addEventListener('change',update));
      const note=make('p','Includes every page in each selected category. Coverage, evidence timestamps and the evidence basis are always kept. No NSX check is started. Downloads expire after 24 hours.');note.className='reference-export-help';
      const sourceNote=make('p','Saved evidence is unchanged. Cached current observations, if selected, are frozen when requested; saved attachments, IP matches and rule configuration retain their own timestamps.');sourceNote.className='reference-export-help';sourceNote.hidden=true;source.addEventListener('change',()=>{sourceNote.hidden=source.value!=='current_cache';});
      const actions=make('div'),prepare=make('button','Prepare export'),cancel=make('button','Cancel export'),download=make('a','Download file'),status=make('p');
      actions.className='reference-export-actions';prepare.type='submit';prepare.className='primary';cancel.type='button';cancel.hidden=true;download.className='button primary';download.hidden=true;download.setAttribute('download','');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
      actions.append(prepare,cancel,download);form.append(field('Format',format),categories,field('Context',context),rawRow,field('Evidence',source),preview,note,sourceNote,actions,status);
      const recent=make('details'),recentTitle=make('summary','Recent exports from this snapshot'),list=make('ul');recent.append(recentTitle,list);dialog.append(header,form,recent);document.body.append(dialog);
      let alive=true,timer=null,jobUrl=null,key=null,lastSelection=null;
      const render=data=>{
        const pending=['queued','running'].includes(data.status);prepare.dataset.pending=String(pending);prepare.disabled=pending;prepare.classList.toggle('primary',data.status!=='ready');cancel.hidden=!pending;download.hidden=data.status!=='ready';
        if(data.download_url){download.href=data.download_url;const extension=(data.filename||'').split('.').pop();download.textContent='Download '+(['zip','csv','xlsx'].includes(extension)?extension:data.format).toUpperCase();}
        status.textContent=data.status==='queued'?'Export queued. You can close this dialog while it prepares.':data.status==='running'?'Preparing all reference pages…':data.status==='ready'?'Ready. Includes all retained references for your selection.':data.error||('Export '+data.status+'.');
        if(pending&&alive)timer=setTimeout(poll,2000);
        else if(alive){key=null;loadRecent();}
      };
      const poll=async()=>{try{const data=await request(jobUrl);if(alive)render(data);}catch(error){if(alive){status.textContent=error.message;prepare.disabled=false;cancel.hidden=true;}}};
      const loadRecent=async()=>{try{const data=await request(endpoint());if(!alive)return;if(data.columns&&!schemas){schemas=data.columns;update();}schemaReady();list.replaceChildren();for(const item of data.items){const li=make('li'),label=make('span',item.filename||((item.object_name||'Object')+' · '+item.format.toUpperCase()+' · '+item.section.replaceAll('_',' ')+' · '+item.status));li.append(label);if(item.status==='ready'){const link=make('a','Download');link.href=item.download_url;link.setAttribute('download','');link.setAttribute('aria-label','Download '+(item.filename||item.format));li.append(link);}else if(['queued','running'].includes(item.status)){const resume=make('button','Check progress');resume.type='button';resume.onclick=()=>{clearTimeout(timer);jobUrl=new URL(item.status_url,location.origin);render(item);};li.append(resume);}list.append(li);}if(!data.items.length)list.append(make('li','No exports prepared yet.'));}catch(error){schemaReady();if(alive)list.replaceChildren(make('li',error.message));}};
      form.addEventListener('submit',async event=>{event.preventDefault();const chosen=rows.filter(row=>row.check.checked);if(!chosen.length){status.textContent='Choose at least one category.';return;}clearTimeout(timer);prepare.disabled=true;download.hidden=true;status.textContent='Requesting export…';await schemaLoaded;if(!alive)return;const columns={};for(const row of chosen){const keys=columnsFor(row);if(keys&&keys.length)columns[row.key]=keys;}const choice={...identity,format:format.value,source:source.value,sections:chosen.map(row=>row.key),columns,context:context.value,raw:raw.checked};remember({format:format.value,sections:choice.sections,presets:Object.fromEntries(rows.map(row=>[row.key,row.preset.value])),columns,context:context.value,raw:raw.checked});const signature=JSON.stringify(choice);if(signature!==lastSelection||!key){key=crypto.randomUUID();lastSelection=signature;}try{const data=await request(endpoint(),post({...choice,request_key:key}));if(!alive)return;jobUrl=new URL(data.status_url,location.origin);render(data);}catch(error){if(alive){status.textContent=error.message;prepare.disabled=false;}}});
      cancel.addEventListener('click',async()=>{clearTimeout(timer);cancel.disabled=true;try{const data=await request(jobUrl,post({}));if(alive)render(data);}catch(error){if(alive)status.textContent=error.message;}finally{cancel.disabled=false;}});
      close.onclick=()=>dialog.close();dialog.addEventListener('close',()=>{alive=false;clearTimeout(timer);dialog.remove();button.focus();},{once:true});
      dialog.addEventListener('keydown',event=>{if(event.key!=='Tab')return;const controls=[...dialog.querySelectorAll('button,select,input,a[href],summary,[tabindex]')].filter(node=>{const collapsed=node.closest('details:not([open])');return !node.disabled&&node.getAttribute('tabindex')!=='-1'&&node.getClientRects().length&&(!collapsed||node===collapsed.querySelector(':scope > summary'));});const first=controls[0],last=controls.at(-1);if(!first)return;if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if((!event.shiftKey&&document.activeElement===last)||!dialog.contains(document.activeElement)){event.preventDefault();first.focus();}});
      update();dialog.showModal();format.focus();loadRecent();
    });return toolbar;
  };
})();

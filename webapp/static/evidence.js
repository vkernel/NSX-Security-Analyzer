/* Shared read-only evidence UI. Raw values always enter the DOM as text. */
(() => {
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
  window.evidenceState=(container,message,retry)=>{
    const state=make('div');state.className='evidence-state';state.setAttribute('role','status');state.append(make('p',message));
    if(retry){const button=make('button','Retry');button.type='button';button.onclick=retry;state.append(button);}
    container.replaceChildren(state);
  };
  window.technicalEvidence=(raw)=>{
    if(typeof raw!=='string')raw=JSON.stringify(raw,null,2) ?? 'Not recorded';
    const block=make('div');block.className='code-block technical-viewer';
    let json=false;try{JSON.parse(raw);json=true;}catch{}
    const toolbar=make('div');toolbar.className='code-toolbar';
    const lines=raw.split('\n'),list=make('ol');list.className='code-lines';list.tabIndex=0;list.setAttribute('aria-label','Read-only '+(json?'JSON':'text')+' with line numbers');
    toolbar.append(make('span',(json?'JSON':'Text')+' · '+lines.length+' lines · Read only'));
    const action=(label,fn)=>{const button=make('button',label);button.type='button';button.onclick=()=>fn(button);toolbar.append(button);return button;};
    action('Copy',async button=>{try{
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(raw);
      else{const field=make('textarea',raw);field.readOnly=true;field.style.cssText='position:fixed;opacity:0';block.append(field);field.select();try{if(!document.execCommand('copy'))throw new Error('Copy unavailable');}finally{field.remove();button.focus();}}
      button.textContent='Copied';}catch{button.textContent='Copy unavailable';}setTimeout(()=>button.textContent='Copy',2000);});
    const wrap=action('Wrap',button=>{const active=block.classList.toggle('wrap');button.setAttribute('aria-pressed',String(active));});wrap.setAttribute('aria-pressed','false');
    const expand=action('Expand',button=>{const active=block.classList.toggle('expanded');button.textContent=active?'Reduce':'Expand';button.setAttribute('aria-pressed',String(active));});expand.setAttribute('aria-pressed','false');
    let position=0;const more=make('button','Show more lines');more.type='button';
    const render=()=>{const end=Math.min(position+200,lines.length);for(;position<end;position++){
      const li=make('li'),line=lines[position];
      if(json && line.length<20000){const pattern=/"(?:\\.|[^"\\])*"\s*:|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;let offset=0;for(const match of line.matchAll(pattern)){li.append(document.createTextNode(line.slice(offset,match.index)));const token=make('span',match[0]);token.className=match[0].endsWith(':')?'json-key':match[0].startsWith('"')?'json-string':'json-value';li.append(token);offset=match.index+match[0].length;}li.append(document.createTextNode(line.slice(offset)||' '));}
      else li.textContent=line||' ';list.append(li);
    }more.hidden=position===lines.length;more.textContent='Show next '+Math.min(200,lines.length-position)+' lines';};
    more.onclick=render;block.append(toolbar,list,more);render();return block;
  };
  window.enhanceTechnicalEvidence=container=>{
    container.querySelectorAll('.code-block:not(.technical-viewer)').forEach(block=>{const lines=block.querySelectorAll('.code-lines li');if(lines.length)block.replaceWith(window.technicalEvidence(Array.from(lines,li=>li.textContent).join('\n')));});
    container.querySelectorAll('pre').forEach(pre=>pre.replaceWith(window.technicalEvidence(pre.textContent)));
  };
  // Preserve DOM nodes/listeners so returning retains expanded sections and pagination.
  window.openRelatedEvidence=async(body,query,label)=>{
    const dialog=body.closest('dialog'),heading=dialog.querySelector('h2'),nodes=Array.from(body.childNodes),title=heading.textContent,scroll=dialog.scrollTop;
    const previousRequest=dialog.reportRequest, previousGeneration=body.evidenceGeneration, previousPending=dialog.tagPending,previousWide=dialog.classList.contains('evidence-firewall-wide');
    const previousBack=dialog.querySelector('.evidence-back');if(previousBack)previousBack.hidden=true;
    const token={};dialog.reportRequest=token;
    const back=make('button','← Back to '+title.replace(/ —.*$/,''));back.type='button';back.className='evidence-back';
    const restore=()=>{if(dialog.tagPending!==previousPending)dialog.tagPending?.abort();dialog.tagPending=previousPending;dialog.reportRequest=previousRequest;body.evidenceGeneration=previousGeneration;body.replaceChildren(...nodes);heading.textContent=title;dialog.classList.toggle('evidence-firewall-wide',previousWide);back.remove();if(previousBack)previousBack.hidden=false;dialog.scrollTop=scroll;};
    back.onclick=restore;dialog.querySelector('.dialog-heading').after(back);heading.textContent=label;
    const open=async()=>{window.evidenceState(body,'Loading evidence…');try{
      const record=await window.reportData(null,{op:'resolve',...query});if(!dialog.open||dialog.reportRequest!==token)return;
      const button=make('button');button.className='detail-button';button.dataset.title=label;button.dataset.evidenceView=record.view;
      button.dataset[record.view==='tags'?'tagRow':'evidenceRow']=record.id;
      button.hidden=true;document.querySelector('main').append(button);button.click();dialog.addEventListener('close',()=>button.remove(),{once:true});back.addEventListener('click',()=>button.remove(),{once:true});
    }catch(error){if(dialog.open&&dialog.reportRequest===token)window.evidenceState(body,error.message,open);}};
    dialog.addEventListener('close',()=>back.remove(),{once:true});await open();
  };
})();

/* One table vocabulary for reference drawers: names first, quiet identities, explicit states. */
(() => {
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
  window.evidenceBadge=(value,kind='state')=>{
    const text=value==null?'Not recorded':String(value).replaceAll('_',' '),badge=make('span',text);badge.className='evidence-status';
    const key=text.toUpperCase();
    badge.dataset.tone=kind==='action'&&key==='ALLOW'?'positive':kind==='action'&&['DROP','REJECT'].includes(key)?'negative':['DISABLED','PARTIAL','UNAVAILABLE','NOT COLLECTED'].includes(key)?'warning':'neutral';
    return badge;
  };
  window.evidenceObject=(body,item,query)=>{
    const value=typeof item==='string'?{path:item}:item||{},label=value.name||value.display_name||value.tag||(value.path?.startsWith('/infra/')?value.path.split('/').pop():value.path)||'Not recorded',root=make('div');root.className='evidence-object';
    if(query){const link=make('button',label);link.type='button';link.className='evidence-object-link';link.onclick=()=>window.openRelatedEvidence(body,query,label);root.append(link);}
    else root.append(make('span',label));
    if(value.path&&value.path!==label){const path=make('code',value.path);path.className='evidence-path';path.title=value.path;root.append(path);}
    return root;
  };
  window.evidenceTable=({title,columns,load,current=()=>true,wide=false,empty='No relationships recorded in this snapshot.'})=>{
    const root=make('section'),heading=make('h3',title),status=make('p'),coverage=make('p'),content=make('div'),pager=make('nav'),previous=make('button','Previous'),next=make('button','Next');
    coverage.className='evidence-note';coverage.hidden=true;
    root.className='evidence-table-section';status.className='evidence-table-status';status.setAttribute('role','status');content.className='evidence-table-content';pager.className='evidence-pagination';pager.setAttribute('aria-label',title+' pages');previous.type=next.type='button';previous.setAttribute('aria-label','Previous '+title.toLowerCase());next.setAttribute('aria-label','Next '+title.toLowerCase());pager.append(previous,next);pager.hidden=true;root.append(heading,status,content,pager);
    root.insertBefore(coverage,content);
    let loaded=false,busy=false,version=0,page=0;
    async function render(target=page){
      if(busy||!current())return;busy=true;const token=++version;root.setAttribute('aria-busy','true');status.textContent='Loading '+title.toLowerCase()+'…';previous.disabled=next.disabled=true;
      try{
        const data=await load(target);if(token!==version||!current())return;page=target;loaded=true;
        const scroll=make('div'),table=make('table'),caption=make('caption',title),head=make('thead'),labels=make('tr'),rows=make('tbody');scroll.className='evidence-table-scroll';scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',title+' table');table.className='evidence-table'+(wide?' evidence-firewall-table':'');
        for(const column of columns){const th=make('th',column.label);th.scope='col';if(column.width)th.style.width=column.width;labels.append(th);}head.append(labels);
        for(const item of data.items){const row=make('tr');for(const column of columns){const td=make('td'),value=column.cell(item);if(value?.nodeType)td.append(value);else td.textContent=value==null?'Not recorded':String(value);row.append(td);}rows.append(row);}
        const states={complete:'Complete',partial:'Partial',configured:'Configured',not_applicable:'Not applicable',unavailable:'Unavailable',not_collected:'Not collected',not_supported:'Not supported'};
        const missing=data.status&&data.status!=='complete';
        if(!data.items.length){const row=make('tr'),td=make('td',data.status==='not_applicable'?'This member category does not apply to the saved definition.':missing?(data.status==='not_collected'?(data.notes?.length?'Member list was not collected; see the collection notes.':'Member identities were not collected in this snapshot. Run a new collection.'):'No member identities saved for this category; membership is not established.'):empty);td.colSpan=columns.length;td.className='evidence-table-empty';row.append(td);rows.append(row);}
        table.append(caption,head,rows);scroll.append(table);content.replaceChildren(scroll);status.textContent=data.items.length?`${page*25+1}–${page*25+data.items.length} shown · Page ${page+1}`:'No entries recorded';pager.hidden=!page&&!data.has_next;previous.disabled=!page;next.disabled=!data.has_next;
        if(data.status){status.textContent=(states[data.status]||data.status)+' · '+status.textContent;status.dataset.membershipStatus=data.status;}
        coverage.textContent=[data.saved_count!=null?`${data.saved_count} saved${data.reported_count!=null?' · '+data.reported_count+' reported by NSX':''}`:'',data.checked_at?'Collected '+(window.workspaceTimeText?window.workspaceTimeText(data.checked_at):data.checked_at):'',...(data.notes||[])].filter(Boolean).join(' · ');coverage.hidden=!coverage.textContent;
      }catch(error){if(current()){status.textContent='Unable to load '+title.toLowerCase();window.evidenceState(content,error.message,()=>render(target));}}
      finally{busy=false;root.removeAttribute('aria-busy');}
    }
    previous.onclick=()=>render(page-1);next.onclick=()=>render(page+1);
    return {panel:root,wide,show:()=>{if(!loaded)render();},reload:()=>render()};
  };
  window.evidenceCategories=(entries)=>{
    const root=make('div'),nav=make('nav');root.className='evidence-reference-tables';root.dataset.evidenceSection='relationships';root.dataset.evidenceActivate='';nav.className='evidence-category-tabs';nav.setAttribute('aria-label','Reference categories');root.append(nav,...entries.map(entry=>entry.panel));let visible=false,selected=0;
    const size=()=>root.closest('dialog')?.classList.toggle('evidence-firewall-wide',visible&&!!entries[selected].wide);
    window.WorkspaceTabs(nav,entries,{onSelect:index=>{selected=index;if(visible){size();entries[index].show();}}});
    root.addEventListener('evidence-visible',()=>{visible=true;size();entries[selected].show();});return root;
  };
  window.firewallReferenceColumns=(body,inspect,{referenceLabel='Matched groups'}={})=>{
    const lines=values=>{const root=make('div');root.className='evidence-reference-lines';for(const value of values)root.append(make('span',value));return root;};
    return [
      {label:'Type',cell:item=>item.firewall_type},
      {label:'Category',cell:item=>item.category},
      {label:'Policy',cell:item=>item.policy_name},
      {label:'Rule',cell:item=>window.evidenceObject(body,item,{path:item.path})},
      {label:'ID',cell:item=>item.rule_id},
      {label:'Seq',cell:item=>item.sequence_number},
      {label:'Action',cell:item=>window.evidenceBadge(item.action,'action')},
      {label:'State',cell:item=>window.evidenceBadge(item.disabled===true?'Disabled':item.disabled===false?'Enabled':null)},
      {label:'Matched fields',cell:item=>lines(item.matched_fields?.length?item.matched_fields:['Not retained'])},
      {label:referenceLabel,cell:item=>{const root=make('div');root.className='evidence-reference-lines';const matches=item.field_matches||[],groups=item.matched_groups||[];const known=new Set(matches.map(match=>match.group.path)),shown=[...matches,...groups.filter(group=>!known.has(group.path)).map(group=>({field:matches.length?'Field not retained':'',group}))];if(shown.length){for(const match of shown.slice(0,3)){const entry=make('div'),link=make('button',match.group.name||match.group.path);entry.className='evidence-firewall-match';link.type='button';link.className='evidence-object-link';link.title=match.group.path;link.onclick=()=>window.openRelatedEvidence(body,{path:match.group.path},match.group.name||match.group.path);if(match.field)entry.append(make('small',match.field));entry.append(link);root.append(entry);}}if(shown.length>3||item.groups_truncated)root.append(make('small','More groups in rule inspection'));if(!groups.length)root.append(make('span','Not retained'));return root;}},
      {label:'Reason / limits',cell:item=>lines([item.reference_reason||'Not retained',item.field_note].filter(Boolean))},
      {label:'Details',cell:item=>{const button=make('button','Inspect');button.type='button';button.setAttribute('aria-label','Inspect references for '+(item.name||item.path));button.onclick=()=>inspect(item,button);return button;}}
    ].map((column,index)=>({...column,width:[4,7,9,17,4,4,6,7,8,12,16,6][index]+'%'}));
  };
  // Both direct inventory selection and related-object navigation use this renderer.
  window.objectReferenceTables=(body,row)=>{
    if(!['group','custom_service'].includes(row?.kind)||!row.path||!window.reportData||!document.querySelector('[data-server-table]'))return;
    const dialog=body.closest('dialog'),generation={};body.evidenceGeneration=generation;
    const current=()=>body.evidenceGeneration===generation&&dialog?.open;
    const load=(section,page)=>window.reportData(null,{op:'object-references',path:row.path,section,page});
    const inspect=item=>window.openRelatedEvidence(body,{path:item.path},item.name||item.path);
    const firewall=window.evidenceTable({title:'Firewall references',wide:true,current,load:page=>load('firewall',page),
      columns:window.firewallReferenceColumns(body,inspect,{referenceLabel:row.kind==='custom_service'?'Matched service':'Matched groups'})});
    const objects=window.evidenceTable({title:'Other referencing objects',current,load:page=>load('objects',page),
      columns:[{label:'Object',cell:item=>window.evidenceObject(body,item,{path:item.path})}]});
    body.querySelectorAll('[data-evidence-section="relationships"]').forEach(node=>node.remove());
    const note=make('p','Saved configuration references only. Collection coverage applies; references do not establish effective policy or observed traffic.');note.className='evidence-note';note.dataset.evidenceSection='relationships';
    // Saved renderers can restore References just before opening the dialog.
    const deferred=table=>({...table,show:()=>queueMicrotask(table.show)});
    body.append(note,window.evidenceCategories([{...deferred(firewall),label:'Firewall rules'},{...deferred(objects),label:'Other objects'}]));
  };
  window.objectMembershipTables=(body,row)=>{
    if(row?.kind!=='group'||!row.path||!window.reportData||!document.querySelector('[data-server-table]'))return;
    const generation=body.evidenceGeneration,dialog=body.closest('dialog');
    const current=()=>body.evidenceGeneration===generation&&dialog?.open;
    const columns=[{label:'Member',cell:item=>window.evidenceObject(body,{...item,path:item.vm_path||item.path},item.vm_path?{path:item.vm_path,view:'vms'}:null)},
      {label:'Identity',cell:item=>item.external_id||item.id||item.identity}];
    const entries=[['virtual-machines','VMs','Resolved VM members'],['ip-addresses','IP addresses','IP members'],['logical-ports','Ports','Resolved port members'],['logical-switches','Segments','Resolved segment members']].map(([section,label,title])=>{
      const table=window.evidenceTable({title,columns,current,empty:'NSX returned no members for this category.',load:page=>window.reportData(null,{op:'group-members',path:row.path,section,page})});
      return {...table,label,show:()=>queueMicrotask(table.show)};
    });
    const note=make('p','Resolved members saved at collection time. Categories are separate; IP or port membership does not establish VM membership. Excluded groups and older snapshots may have no member list.');note.className='evidence-note';note.dataset.evidenceSection='members';
    const categories=window.evidenceCategories(entries);categories.dataset.evidenceSection='members';categories.querySelector('nav').setAttribute('aria-label','Member categories');
    body.append(note,categories);
  };
  // Existing group/service snapshots keep their evidence and links, but use the same table surface.
  window.referenceListTables=root=>{
    for(const details of root.querySelectorAll('details[data-evidence-section="relationships"]')){
      const list=details.querySelector(':scope > ul');if(!list)continue;
      const section=make('section'),title=details.querySelector(':scope > summary')?.textContent||'References',heading=make('h3',title),scroll=make('div'),table=make('table'),caption=make('caption',title),head=make('thead'),row=make('tr'),th=make('th','Referencing object'),rows=make('tbody');section.className='evidence-table-section';section.dataset.evidenceSection='relationships';scroll.className='evidence-table-scroll';scroll.tabIndex=0;scroll.setAttribute('role','region');scroll.setAttribute('aria-label',title+' table');table.className='evidence-table';th.scope='col';row.append(th);head.append(row);
      for(const item of [...list.children]){const row=make('tr'),cell=make('td');cell.append(...item.childNodes);cell.querySelectorAll('code').forEach(code=>code.classList.add('evidence-path'));row.append(cell);rows.append(row);}
      table.append(caption,head,rows);scroll.append(table);section.append(heading,...[...details.children].filter(child=>child.tagName!=='SUMMARY'&&child!==list),scroll);details.replaceWith(section);
    }
  };
})();

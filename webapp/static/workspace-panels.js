/* Native dialogs retain page state. Records keep their own forms, scripts and authorization. */
(()=>{
 const embedded=window.self!==window.top;
 if(embedded){
  try{if(parent.location.origin===location.origin&&parent.document.querySelector('[data-workspace-frame]'))document.documentElement.classList.add('workspace-embedded');}catch(_){}
 }
 const node=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
 const dirty=root=>!!root.querySelector('form[data-pending="true"], [data-panel-dirty="true"]');
 document.addEventListener('input',e=>{const form=e.target.closest('form');if(form&&form.method.toLowerCase()==='post')form.dataset.panelDirty='true';});
 document.addEventListener('submit',e=>queueMicrotask(()=>{if(!e.defaultPrevented)delete e.target.dataset.panelDirty;}));
 const allowClose=root=>!dirty(root)||window.confirm('Discard unsaved changes and close this panel?');
 const dialog=node('dialog');dialog.className='workspace-panel';dialog.setAttribute('aria-labelledby','workspace-panel-title');
 const header=node('header'),title=node('h2');title.id='workspace-panel-title';
 const back=node('button','Back'),full=node('a','Open full page'),close=node('button','Close');
 back.type=close.type='button';full.className='button';back.hidden=true;
 header.append(back,title,full,close);const body=node('div');body.className='workspace-panel-body';dialog.append(header,body);document.body.append(dialog);
 let origin,restore,frame,stack=[],loadingTimer,frameURL='',overlayRestore;
 const footer=node('footer'),done=node('button','Done editing');done.type='button';done.className='button primary';footer.append(node('span','Changes stay in your draft. Save the draft to persist them.'),done);footer.hidden=true;dialog.append(footer);done.onclick=()=>dialog.close();
 function closePanel(){if(overlayRestore){overlayRestore();return;}if(restore){dialog.close();return;}let root=body;try{if(frame)root=frame.contentDocument||body;}catch(_){}if(allowClose(root))dialog.close();}
 close.onclick=closePanel;dialog.addEventListener('cancel',e=>{e.preventDefault();closePanel();});
 dialog.addEventListener('close',()=>{footer.hidden=true;clearTimeout(loadingTimer);restore?.();restore=null;frame=null;body.replaceChildren();stack=[];back.hidden=true;origin?.focus();document.dispatchEvent(new CustomEvent('workspace-panel-closed'));});
 const safeURL=value=>{const u=new URL(value,location.href);if(u.origin!==location.origin)throw Error('Only local pages can open in this panel.');return u.href;};
 function loadRecord(url,push=true){
  if(frame){try{if(!allowClose(frame.contentDocument))return;}catch(_){}}
  url=safeURL(url);if(push&&frameURL&&url!==frameURL)stack.push(frameURL);frameURL=url;back.hidden=!stack.length;full.href=url;
  body.replaceChildren();const status=node('p','Loading record…');status.setAttribute('role','status');body.append(status);
  frame=node('iframe');frame.dataset.workspaceFrame='';frame.title='Record details';frame.src=url;body.append(frame);
  clearTimeout(loadingTimer);loadingTimer=setTimeout(()=>{status.textContent='Loading is taking longer than expected. Use Open full page to continue.';},15000);
  const loadedFrame=frame;frame.addEventListener('load',()=>{if(frame!==loadedFrame)return;clearTimeout(loadingTimer);try{
   const doc=frame.contentDocument;if(!doc?.querySelector('main'))throw Error();
   doc.documentElement.classList.add('workspace-embedded');status.remove();title.textContent=doc.querySelector('h1')?.textContent||'Record details';frame.title=title.textContent;
   frameURL=safeURL(frame.contentWindow.location.href);full.href=frameURL;
   // Navigate related records in this same panel; avoid stacked record drawers.
   doc.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(!a||e.ctrlKey||e.metaKey||e.shiftKey||a.target||a.hasAttribute('download')||a.hasAttribute('data-panel-download')||a.getAttribute('href').startsWith('#'))return;const target=new URL(a.href);if(target.origin!==location.origin)return;e.preventDefault();loadRecord(target.href);});
  }catch(_){status.textContent='This page cannot be displayed here. Use Open full page.';}});
 }
 full.addEventListener('click',e=>{try{if(frame&&!allowClose(frame.contentDocument))e.preventDefault();}catch(_){}});
 back.onclick=()=>{if(overlayRestore){overlayRestore();return;}if(!stack.length)return;const previous=stack[stack.length-1];let root;try{root=frame?.contentDocument;}catch(_){}if(root&&!allowClose(root))return;stack.pop();if(root)root.querySelectorAll('[data-panel-dirty]').forEach(f=>delete f.dataset.panelDirty);loadRecord(previous,false);};
 document.addEventListener('click',e=>{
  const link=e.target.closest('a[data-record-panel]');if(!link||embedded||e.defaultPrevented||e.button||e.ctrlKey||e.metaKey||e.shiftKey||link.target)return;
  if(typeof dialog.showModal!=='function')return;
  e.preventDefault();origin=link;title.textContent=link.textContent.trim()||'Record details';full.hidden=false;frameURL='';loadRecord(link.href);dialog.showModal();
 });
 // Move real nodes, rather than copies, so form values and event handlers survive.
 function openSection(details,trigger){
  if(embedded||document.querySelector('dialog[open]'))return;
  details.dataset.panelReading='true';origin=trigger;title.textContent=details.querySelector('summary')?.textContent||'Details';full.hidden=true;back.hidden=true;
  const component=details.closest('.naming-component');footer.hidden=!component;
  const source=component||details,marker=document.createComment('panel content');
  if(component)component.before(marker);else details.append(marker);
  const forms=new Map();source.querySelectorAll('input,select,textarea,button').forEach(control=>{if(control.form&&!source.contains(control.form)){const f=control.form;if(!f.id)f.id='panel-form-'+Math.random().toString(36).slice(2);forms.set(control,control.getAttribute('form'));control.setAttribute('form',f.id);}});
  const content=node('div');if(component){content.append(component);details.open=true;}else for(const child of [...details.childNodes])if(child!==marker&&child!==details.querySelector('summary'))content.append(child);
  content.addEventListener('input',e=>{if(e.target.form)e.target.form.dispatchEvent(new Event('change',{bubbles:true}));});
  body.replaceChildren(content);restore=()=>{delete details.dataset.panelReading;marker.before(...content.childNodes);marker.remove();forms.forEach((value,c)=>value===null?c.removeAttribute('form'):c.setAttribute('form',value));details.open=false;};dialog.showModal();
 }
 document.addEventListener('click',e=>{const summary=e.target.closest('details[data-section-panel] > summary');if(!summary||embedded||typeof dialog.showModal!=='function'||document.querySelector('dialog[open]'))return;e.preventDefault();openSection(summary.parentElement,summary);});
 // Reuse the component panel for its scope/tag picker instead of stacking dialogs.
 window.WorkspacePanels={openPicker(picker){if(!dialog.open||!restore)return null;const nodes=[...body.childNodes],savedTitle=title.textContent;footer.hidden=true;const content=[...picker.childNodes];body.replaceChildren(...content);title.textContent='Choose collected evidence';back.hidden=false;
  const resume=()=>{picker.append(...content);body.replaceChildren(...nodes);title.textContent=savedTitle;back.hidden=true;footer.hidden=false;overlayRestore=null;};overlayRestore=resume;return resume;
 }};
 const evidence=document.querySelector('.finding-evidence'),history=document.querySelector('.review-history');
 if(evidence&&history){const nav=node('nav');nav.className='panel-evidence-tabs';nav.setAttribute('aria-label','Finding review sections');evidence.before(nav);window.WorkspaceTabs(nav,[{label:'Summary',panel:evidence},{label:'History',panel:history}]);}

 // One view of naming setup at a time; validation remains server-authoritative.
 function namingSteps(){const editor=document.querySelector('.naming-editor');if(!editor)return;
  const form=editor.querySelector('form'),components=form.querySelector('[data-naming-components]');if(!components)return;
  const tabs=node('nav');tabs.className='panel-step-tabs';tabs.setAttribute('aria-label','Edit naming draft');
  const scope=form.querySelector('.form-grid'),componentHeader=document.getElementById('policy-components');
  const componentNodes=[componentHeader,componentHeader?.nextElementSibling,components,form.querySelector('[data-add-component]'),form.querySelector('[data-component-status]')].filter(Boolean);
  const scopeNodes=[scope,...form.querySelectorAll(':scope > p.muted')];
  const previewSection=document.getElementById('policy-preview')?.closest('section'),mappings=document.getElementById('policy-mappings');
  const select=(mode)=>{editor.hidden=['preview','activate'].includes(mode);if(previewSection)previewSection.hidden=!['preview','activate'].includes(mode);if(mappings)mappings.hidden=mode!=='scope';document.querySelectorAll('.workspace-steps a').forEach(a=>{const match={scope:'#policy-scope',components:'#policy-components',preview:'#policy-preview',activate:'#policy-activate'}[mode];if(a.getAttribute('href')===match)a.setAttribute('aria-current','step');else a.removeAttribute('aria-current');});scopeNodes.forEach(n=>n.hidden=mode!=='scope');componentNodes.forEach(n=>n.hidden=mode!=='components');tabs.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.step===mode)));};
  for(const [key,label]of [['scope','1. Scope'],['components','2. Name components']]){const b=node('button',label);b.type='button';b.dataset.step=key;b.onclick=()=>select(key);tabs.append(b);}form.before(tabs);
  document.querySelector('a[href="#policy-scope"]')?.addEventListener('click',()=>select('scope'));
  document.querySelector('a[href="#policy-components"]')?.addEventListener('click',()=>select('components'));
  document.querySelector('a[href="#policy-preview"]')?.addEventListener('click',()=>select('preview'));
  document.querySelector('a[href="#policy-activate"]')?.addEventListener('click',()=>select('activate'));
  document.addEventListener('invalid',e=>{if(e.target.form!==form)return;if(restore){const finish=restore;restore=null;finish();dialog.close();}select(components.contains(e.target)?'components':'scope');const detail=e.target.closest('details');if(detail)detail.open=true;},true);
  select(components.querySelector('.errorlist')?'components':document.getElementById('policy-activate')?'preview':'scope');
  function mark(){components.querySelectorAll('fieldset > details').forEach(d=>{d.dataset.sectionPanel='';if(!d.querySelector('.errorlist'))d.open=false;});}mark();new MutationObserver(mark).observe(components,{childList:true});
 }
 namingSteps();
})();
/* Evidence lists are searchable/paginated already; expose one category at a time. */
(()=>{
 function tabs(root){
  if(!root||root.dataset.panelTabbed)return;root.dataset.panelTabbed='true';
  const nav=document.createElement('nav');nav.className='panel-evidence-tabs';nav.setAttribute('aria-label','Evidence categories');root.prepend(nav);
  let selected=0;
  const render=()=>{if(nav.parentNode!==root)root.prepend(nav);const lists=[...root.children].filter(n=>n.tagName==='DETAILS');const labels={'virtual-machines':'Members','ip-addresses':'IP members','logical-ports':'Port members','logical-switches':'Switch members',entries:'Services',service_entries:'Services',source_groups:'Sources',destination_groups:'Destinations',scope:'Applied to',parent:'Parent policy',references:'References',definition:'Definition',rules:'Rules',services:'Services',members:'Members',vm_members:'Members',ip_members:'IP members',vif_members:'Network interfaces'};window.WorkspaceTabs(nav,lists.map(list=>({label:labels[list.dataset.evidenceKey]||list.querySelector('summary').textContent,panel:list})),{selected,onSelect:index=>{selected=index;lists.forEach((list,i)=>list.open=i===index);}});};
  new MutationObserver(render).observe(root,{childList:true});render();
 }
 tabs(document.getElementById('saved-approval-sections'));tabs(document.querySelector('[data-live-results]'));
 // Refresh only safe list regions after a record drawer closes; never reload the page.
 document.addEventListener('workspace-panel-closed',async()=>{
  const roots=[...document.querySelectorAll('#finding-list-results,#activity-results')];if(!roots.length||!window.RefreshRegion)return;
  try{const response=await fetch(location.href,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000)});if(!response.ok||response.redirected)return;const doc=new DOMParser().parseFromString(await response.text(),'text/html');
   roots.forEach(root=>{const fresh=doc.getElementById(root.id);if(fresh)window.RefreshRegion.replace(root,fresh);});
  }catch(_){} // Existing live status remains the authoritative connectivity indicator.
 });
})();

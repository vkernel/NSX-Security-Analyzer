/* Notify about newer evidence without replacing the snapshot being reviewed. */
(()=>{const el=document.querySelector('[data-segmentation-status]');if(!el)return;
let timer;async function check(){try{if(!document.hidden){const response=await fetch(el.dataset.url,{credentials:'same-origin',headers:{Accept:'application/json'}});if(response.ok){const data=await response.json();document.getElementById('seg-new').hidden=!data.latest||data.latest===el.dataset.snapshot;}}}catch(_){}finally{timer=setTimeout(check,30000);}}
window.addEventListener('pagehide',()=>clearTimeout(timer));check();})();
/* Keep graph exploration local; preserve the pinned snapshot and rule evidence. */
(()=>{
 const nodes=[...document.querySelectorAll('[data-application]')];
 function focus(id, label){
  const connected=new Set([id]);
  document.querySelectorAll('[data-edge-source]').forEach(e=>{if(e.dataset.edgeSource===id)connected.add(e.dataset.edgeDestination);if(e.dataset.edgeDestination===id)connected.add(e.dataset.edgeSource);});
  nodes.forEach(n=>{n.classList.toggle('is-active',n.dataset.application===id);n.classList.toggle('is-muted',id!==null&&!connected.has(n.dataset.application));if(n.dataset.application===id)n.setAttribute('aria-current','true');else n.removeAttribute('aria-current');});
  const clear=document.querySelector('[data-clear-application]');if(clear)clear.disabled=id===null;
  document.querySelectorAll('[data-edge-source]').forEach(e=>{
   const active=id===null||e.dataset.edgeSource===id||e.dataset.edgeDestination===id;
   e.classList.toggle('is-muted',!active);e.classList.toggle('is-active',id!==null&&active);
  });
  document.querySelectorAll('[data-connection-source]').forEach(e=>{e.hidden=id!==null&&e.dataset.connectionSource!==id&&e.dataset.connectionDestination!==id;});
  const status=document.querySelector('[data-application-status]');if(status)status.textContent=id===null?'All displayed applications':`Connections involving ${label}`;
 }
 nodes.forEach(node=>node.addEventListener('click',event=>{event.preventDefault();const id=node.dataset.application;const detail=document.getElementById(`application-${id}`);if(detail){focus(id,detail.querySelector('summary').textContent.trim());if(window.WorkspacePanels?.openSection)window.WorkspacePanels.openSection(detail,node);else detail.querySelector('summary').click();}}));
 document.querySelector('[data-clear-application]')?.addEventListener('click',()=>focus(null,''));focus(null,'');
 const search=document.querySelector('[data-group-search]');
 const options=()=>[...document.querySelectorAll('[data-group-option]')];
 let searchTimer,controller,generation=0;
 const searchStatus=document.querySelector('[data-group-search-status]');
 async function findGroups(version){
  controller=new AbortController();
  if(searchStatus)searchStatus.textContent='Finding groups…';
  try{
   const url=new URL(search.dataset.groupUrl,location.href);url.searchParams.set('q',search.value);
   const response=await fetch(url,{credentials:'same-origin',signal:controller.signal,headers:{Accept:'application/json'}});
   if(!response.ok||response.redirected)throw Error('Unable to search groups. Try again.');
   const data=await response.json();if(version!==generation)return;
   const list=document.querySelector('[data-group-options]');
   const selected=options().filter(o=>o.querySelector('input').checked);
   const paths=new Set(selected.map(o=>o.querySelector('input').value));
   const labels=data.results.filter(g=>!paths.has(g.path)).map(g=>{
    const label=document.createElement('label');label.dataset.groupOption='';
    const input=document.createElement('input');input.type='checkbox';input.name='groups';input.value=g.path;
    const text=document.createElement('span'),name=document.createElement('strong'),detail=document.createElement('small');
    name.textContent=g.name;detail.textContent=g.type+' · '+g.path;text.append(name,detail);label.append(input,text);return label;
   });
   selected.forEach(o=>o.hidden=false);list.replaceChildren(...selected,...labels);count();
   if(searchStatus)searchStatus.textContent=data.more?`First 50 of ${data.total} matches. Narrow your search to find another group.`:`${data.total} matching group${data.total===1?'':'s'}. Selected groups stay available.`;
  }catch(error){if(version===generation&&error.name!=='AbortError'&&searchStatus)searchStatus.textContent=error.message;}
 }
 search?.addEventListener('input',()=>{
  const term=search.value.toLowerCase();
  if(!search.dataset.groupUrl){options().forEach(o=>{o.hidden=!(o.textContent+' '+o.querySelector('input').value).toLowerCase().includes(term);});return;}
  clearTimeout(searchTimer);controller?.abort();const version=++generation;searchTimer=setTimeout(()=>findGroups(version),250);
 });
 function count(){const total=options().filter(o=>o.querySelector('input').checked).length;const status=document.querySelector('[data-group-count]');if(status)status.textContent=`${total} / 12 groups selected`;options().forEach(o=>{const input=o.querySelector('input');input.disabled=total>=12&&!input.checked;});}
 document.addEventListener('change',event=>{if(event.target.closest('[data-group-option]'))count();});count();
 window.addEventListener('pagehide',()=>{clearTimeout(searchTimer);controller?.abort();});
})();
/* Fetch connection evidence only after opening it; keep successful results locally. */
(()=>{
 const pending=new Set();
 async function load(root){
  if(!root||root.dataset.loaded==='true'||root.dataset.loading==='true')return;
  root.dataset.loading='true';
  const status=root.querySelector('[role=status]'),button=root.querySelector('[data-load-connection]');
  if(status)status.textContent='Loading connection rules…';
  if(button)button.disabled=true;
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);pending.add(controller);
  try{
   const response=await fetch(root.dataset.url,{credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{Accept:'text/html'}});
   if(response.redirected||(!response.ok&&response.status!==409))throw Error('Connection details unavailable. Retry, or sign in again if your session expired.');
   const doc=new DOMParser().parseFromString(await response.text(),'text/html');
   root.replaceChildren(...doc.body.childNodes);root.dataset.loaded='true';
  }catch(error){
   if(status)status.textContent=error.name==='AbortError'?'Loading timed out. Retry to view the connection rules.':error.message;
   if(button){button.disabled=false;button.textContent='Retry loading rules';}
  }finally{clearTimeout(timer);pending.delete(controller);delete root.dataset.loading;}
 }
 document.addEventListener('toggle',event=>{
  if(event.target.matches('.application-connection')&&event.target.open)load(event.target.querySelector('[data-connection-details]'));
 },true);
 document.addEventListener('click',event=>{
  const button=event.target.closest('[data-load-connection]');if(button){event.preventDefault();load(button.closest('[data-connection-details]'));}
 });
 window.addEventListener('pagehide',()=>pending.forEach(controller=>controller.abort()));
})();
/* Narrow screens start with readable relationship lists; the map is an explicit view. */
(()=>{
 const canvas=document.getElementById('application-map-canvas'),toggle=document.querySelector('[data-map-toggle]');if(!canvas||!toggle)return;
 const viewport=canvas.querySelector('.application-map-viewport'),svg=viewport?.querySelector('svg'),controls=canvas.querySelector('.application-map-controls');if(!svg)return;
 const narrow=matchMedia('(max-width:700px)');let expanded=false,width=null;
 function layout(){toggle.hidden=!narrow.matches;toggle.setAttribute('aria-expanded',String(expanded));toggle.textContent=expanded?'Hide map':'Show map';canvas.hidden=narrow.matches&&!expanded;if(narrow.matches&&expanded&&width===null){width=1000;svg.style.width=width+'px';}}
 toggle.addEventListener('click',()=>{expanded=!expanded;layout();});narrow.addEventListener('change',layout);controls.hidden=false;layout();
 canvas.querySelector('[data-map-fit]').addEventListener('click',()=>{width=viewport.clientWidth;svg.style.width='100%';viewport.scrollTo?.(0,0);});
 canvas.querySelectorAll('[data-map-zoom]').forEach(button=>button.addEventListener('click',()=>{width=Math.max(320,Math.min(2400,(svg.getBoundingClientRect().width||1000)*(button.dataset.mapZoom==='in'?1.25:.8)));svg.style.width=width+'px';}));
 let drag;
 viewport.addEventListener('pointerdown',event=>{if(event.button!==0||event.target.closest('a'))return;drag={x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture?.(event.pointerId);});
 viewport.addEventListener('pointermove',event=>{if(!drag)return;viewport.scrollLeft=drag.left+drag.x-event.clientX;viewport.scrollTop=drag.top+drag.y-event.clientY;});
 ['pointerup','pointercancel','lostpointercapture'].forEach(name=>viewport.addEventListener(name,()=>drag=null));
})();

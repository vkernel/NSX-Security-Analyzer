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
 nodes.forEach(node=>node.addEventListener('click',event=>{event.preventDefault();const id=node.dataset.application;const detail=document.getElementById(`application-${id}`);if(detail){focus(id,detail.querySelector('summary').textContent.trim());detail.querySelector('summary').click();}}));
 document.querySelector('[data-clear-application]')?.addEventListener('click',()=>focus(null,''));focus(null,'');
 const search=document.querySelector('[data-group-search]');
 const options=[...document.querySelectorAll('[data-group-option]')];
 search?.addEventListener('input',()=>{const term=search.value.toLowerCase();options.forEach(o=>{o.hidden=!(o.textContent+' '+o.querySelector('input').value).toLowerCase().includes(term);});});
 function count(){const total=options.filter(o=>o.querySelector('input').checked).length;const status=document.querySelector('[data-group-count]');if(status)status.textContent=`${total} / 12 groups selected`;options.forEach(o=>{const input=o.querySelector('input');input.disabled=total>=12&&!input.checked;});}
 options.forEach(o=>o.querySelector('input').addEventListener('change',count));count();
})();

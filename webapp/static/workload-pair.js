/* Progressive enhancement: safe DOM construction, keyboard search, no NSX writes. */
(()=>{
 const form=document.getElementById('pair-form');
 const controls=[];
 form?.querySelectorAll('[data-vm-picker]').forEach(wrapper=>{
  const input=wrapper.querySelector('[role=combobox]'),hidden=wrapper.querySelector('input[type=hidden]'),list=wrapper.querySelector('[role=listbox]'),selection=wrapper.querySelector('[data-vm-selection]');
  let timer,controller,version=0,active=-1,buttons=[];
  const close=()=>{list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;};
  const select=row=>{clearTimeout(timer);controller?.abort();version++;hidden.value=row.value;input.value=row.name;selection.textContent=[row.ips.join(', ')||'IP evidence unavailable',row.environment].join(' · ');close();};
  async function search(){const revision=++version;controller?.abort();controller=new AbortController();list.replaceChildren();list.hidden=false;input.setAttribute('aria-expanded','true');list.textContent='Searching…';
   try{const response=await fetch(form.dataset.vmUrl+'&q='+encodeURIComponent(input.value),{credentials:'same-origin',signal:controller.signal});if(!response.ok)throw Error('Search failed');const data=await response.json();if(revision!==version)return;
    list.replaceChildren();buttons=[];active=-1;(data.results||[]).forEach((row,index)=>{const option=document.createElement('div');option.role='option';option.id=list.id+'-'+index;option.tabIndex=-1;const name=document.createElement('strong');name.textContent=row.name;const detail=document.createElement('small');detail.textContent=[row.ips.join(', ')||'IP evidence unavailable',row.environment].join(' · ');option.append(name,detail);option.addEventListener('mousedown',e=>{e.preventDefault();select(row);});option.addEventListener('click',()=>select(row));option.choose=()=>select(row);list.append(option);buttons.push(option);});
    if(!buttons.length)list.textContent=data.message||'No VMs found. Try a name, IP address or tag.';
    if(data.more){const hint=document.createElement('small');hint.textContent='First 25 matches. Narrow your search.';list.append(hint);}
   }catch(error){if(error.name!=='AbortError'&&revision===version)list.textContent='VM search unavailable. Type again to retry.';}
  }
  input.addEventListener('input',()=>{hidden.value='';selection.textContent='Select a matching workload';clearTimeout(timer);controller?.abort();version++;timer=setTimeout(search,250);});
  input.addEventListener('focus',()=>{if(!hidden.value)search();});
  input.addEventListener('keydown',e=>{if(e.key==='Escape'){close();return;}if(['ArrowDown','ArrowUp'].includes(e.key)){e.preventDefault();if(list.hidden){search();return;}if(!buttons.length)return;active=(active+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons.forEach((b,i)=>b.setAttribute('aria-selected',String(i===active)));input.setAttribute('aria-activedescendant',buttons[active].id);buttons[active].scrollIntoView({block:'nearest'});}else if(e.key==='Enter'&&!list.hidden&&active>=0){e.preventDefault();buttons[active].choose();}});
  input.addEventListener('blur',()=>{clearTimeout(timer);controller?.abort();version++;setTimeout(close,150);});
  controls.push({input,hidden,selection,close,cancel(){clearTimeout(timer);controller?.abort();version++;}});
 });
 document.getElementById('pair-swap')?.addEventListener('click',()=>{if(controls.length!==2)return;const [a,b]=controls;controls.forEach(c=>{c.cancel();c.close();});[a.input.value,b.input.value]=[b.input.value,a.input.value];[a.hidden.value,b.hidden.value]=[b.hidden.value,a.hidden.value];[a.selection.textContent,b.selection.textContent]=[b.selection.textContent,a.selection.textContent];});
 document.querySelectorAll('[data-pair-rule]').forEach(row=>row.addEventListener('toggle',()=>{if(!row.open)return;document.querySelectorAll('[data-pair-rule]').forEach(other=>{other.classList.toggle('pair-selected',other===row);});document.getElementById('pair-focus-title').textContent=row.dataset.name;document.getElementById('pair-focus-explanation').textContent=row.dataset.explanation;}));
 const drawer=document.querySelector('[data-rule-drawer]');
 if(drawer&&typeof drawer.showModal==='function'){
  let origin,body,trigger;
  const restore=()=>{if(body&&origin){origin.append(body);origin.open=false;}body=null;trigger?.focus();};
  drawer.addEventListener('close',restore);
  drawer.querySelector('[data-close-rule]').addEventListener('click',()=>drawer.close());
  document.querySelectorAll('[data-pair-rule]').forEach(row=>row.querySelector('summary').addEventListener('click',event=>{
   event.preventDefault();origin=row;trigger=row.querySelector('summary');body=row.querySelector('.pair-rule-body');
   document.getElementById('rule-drawer-title').textContent=row.dataset.name;
   document.getElementById('pair-focus-title').textContent=row.dataset.name;
   document.getElementById('pair-focus-explanation').textContent=row.dataset.explanation;
   document.querySelectorAll('[data-pair-rule]').forEach(other=>other.classList.toggle('pair-selected',other===row));
   if(!body.dataset.tabbed&&window.WorkspaceTabs){
    body.dataset.tabbed='true';const nav=document.createElement('nav');nav.className='panel-evidence-tabs';nav.setAttribute('aria-label','Firewall rule evidence');
    const summary=document.createElement('section'),references=document.createElement('section'),technical=document.createElement('section'),history=document.createElement('section');
    const facts=document.createElement('p');facts.textContent=row.querySelector('summary').textContent.trim();summary.append(facts);
    for(const child of [...body.children]){if(child.matches('.pair-match'))references.append(child);else if(child.matches('details'))technical.append(child);else summary.append(child);}
    history.textContent='This is saved configuration evidence. Open the saved firewall rule for its snapshot context; this view does not contain review history.';
    body.append(nav,summary,references,history,technical);window.WorkspaceTabs(nav,[{label:'Summary',panel:summary},{label:'References',panel:references},{label:'History',panel:history},{label:'Technical details',panel:technical}]);
   }
   drawer.querySelector('[data-rule-drawer-body]').replaceChildren(body);drawer.showModal();
  }));
 }
 document.querySelector('[data-copy-pair]')?.addEventListener('click',async()=>{const status=document.querySelector('[data-copy-status]');try{await navigator.clipboard.writeText(location.href);status.textContent='Snapshot link copied';}catch(_){status.textContent='Copy the address from your browser to share this snapshot.';}});
 document.querySelectorAll('[data-membership-check]').forEach(form=>form.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('button'),status=form.querySelector('[data-membership-result]');button.disabled=true;status.textContent='Checking live VM membership…';const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
  try{const response=await fetch(form.action,{method:'POST',body:new FormData(form),credentials:'same-origin',signal:controller.signal});let result;try{result=await response.json();}catch(_){throw Error('Session expired or membership service unavailable. Sign in again if necessary.');}if(!response.ok)throw Error(result.error||'Membership check denied.');status.textContent=result.message+' Checked '+new Date(result.checked_at).toLocaleString()+'. '+result.note;}catch(error){status.textContent=error.name==='AbortError'?'Membership read timed out. No conclusion was made. You may retry.':error.message;}finally{clearTimeout(timer);button.disabled=false;}
 }));
})();

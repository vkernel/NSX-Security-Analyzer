/* Contextual corrections; the server remains authoritative for selection and permissions. */
(() => {
 const form=document.getElementById('naming-proposals');if(!form)return;
 const boxes=[...form.querySelectorAll('[name=selected]')],all=form.querySelector('[data-naming-select-all]'),status=form.querySelector('[data-naming-selected]');
 const panel=form.querySelector('[data-naming-action-panel]'),bar=form.querySelector('.naming-selection-bar');
 const node=(tag,text)=>{const n=document.createElement(tag);if(text)n.textContent=text;return n;};
 const selected=()=>boxes.filter(b=>b.checked);
 let actionDialog,confirmation,opener,lastSubmit,approved=false;
 function update(){const n=selected().length;if(status)status.textContent=n?`${n} group${n===1?'':'s'} selected on this page`:'No groups selected';if(all){all.checked=n>0&&n===boxes.length;all.indeterminate=n>0&&n<boxes.length;}
  if(bar)bar.hidden=!n;
  form.querySelectorAll('button[name=action],[data-naming-open]').forEach(b=>b.disabled=b.hidden||(b.value!=='import'&&(!n||n>100)));
  if(n>100&&status)status.textContent='Select at most 100 groups to continue';
 }
 const actions={
  propose:{label:'Save component proposals',fields:['scope','value','note','owner'],help:'Propose a scope and value for each selected group. Values remain separate from NSX tags until reviewed; membership is unchanged.'},
  workflow:{label:'Update progress',fields:['progress','ticket','note','owner'],help:'Track external work. Change prepared and Awaiting verification require a change ticket and an assigned owner. A later collection verifies the result.'},
  assign:{label:'Assign owner',fields:['owner'],help:'Assign one operator to the selected groups. Existing proposals and recorded decisions are retained.'},
  review:{label:'Mark proposals reviewed',fields:[],help:'Review the selected component proposals against their current group definitions. Stale or empty proposals cannot be marked reviewed.'},
  reject:{label:'Reject / clear proposals',fields:[],help:'Reject the selected proposals or exceptions and clear their proposed component values. NSX objects remain unchanged.'},
  exception:{label:'Accept naming exception',fields:['owner','expires_at','note'],help:'Record an administrator exception with an owner, expiry date and reason. Changed evidence or policy can make the exception stale.'},
  import:{label:'Import CSV as drafts',fields:['csv_file','note','owner'],help:'Import component proposals by current group path. The CSV determines the affected groups; table selection is not used. Up to 1 MB and 1,000 rows. No NSX changes are sent.'}
 };
 function choose(action){const config=actions[action]||actions.propose;form.querySelectorAll('[data-naming-field]').forEach(w=>{const show=config.fields.includes(w.dataset.namingField);w.hidden=!show;w.querySelectorAll('input,select,textarea,button').forEach(c=>{c.disabled=!show;if(c.name)c.required=show&&({propose:['scope','value'],assign:['owner'],exception:['owner','expires_at','note'],import:['csv_file']}[action]||[]).includes(c.name);});});
  panel.querySelector('[data-naming-action-help]').textContent=config.help;
  panel.querySelector('[data-naming-import-help]').hidden=action!=='import';
  panel.querySelector('[data-naming-process-help]').hidden=!['propose','workflow','review'].includes(action);
  form.querySelectorAll('button[name=action]').forEach(b=>{b.hidden=b.value!==action;b.classList.toggle('primary',b.value===action);});
  if(actionDialog)actionDialog.querySelector('h2').textContent=action==='import'?'Import component proposals':`Naming corrections · ${selected().length} selected`;
  update();
 }
 function open(action,trigger){if(!actionDialog)return;opener=trigger;const choice=panel.querySelector('[data-naming-action-choice]');choice.value=action;choose(action);actionDialog.showModal();choice.focus();}
 if(panel&&typeof document.createElement('dialog').showModal==='function'){
  actionDialog=node('dialog');actionDialog.className='workspace-panel naming-action-dialog';actionDialog.setAttribute('aria-labelledby','naming-action-title');
  const header=node('header'),title=node('h2','Naming corrections'),close=node('button','Close');title.id='naming-action-title';close.type='button';header.append(title,close);
  const body=node('div');body.className='workspace-panel-body';panel.classList.remove('form-card');panel.querySelector('h2').remove();body.append(panel);const actionFooter=node('footer');actionFooter.append(panel.querySelector('[data-naming-submit-actions]'));actionDialog.append(header,body,actionFooter);form.append(actionDialog);
  close.onclick=()=>actionDialog.close();actionDialog.addEventListener('close',()=>opener?.focus());
  const choice=panel.querySelector('[data-naming-action-choice]');choice.parentElement.hidden=false;choice.onchange=()=>choose(choice.value);
  form.querySelector('[data-naming-open]')?.addEventListener('click',e=>open('propose',e.currentTarget));
  form.querySelectorAll('[data-naming-row-action]').forEach(a=>a.addEventListener('click',e=>{e.preventDefault();boxes.forEach(b=>b.checked=b.closest('tr')===a.closest('tr'));update();open('workflow',a);}));
  const importButton=document.querySelector('[data-naming-import]');if(importButton){importButton.hidden=false;importButton.onclick=()=>open('import',importButton);}
  confirmation=node('dialog');confirmation.className='naming-confirmation';confirmation.setAttribute('aria-labelledby','naming-confirm-title');
  const h=node('h2','Review changes');h.id='naming-confirm-title';const description=node('p');description.dataset.confirmDescription='';const list=node('div');list.className='confirmation-list';
  const note=node('p','All affected groups must pass server validation; otherwise nothing is saved. No NSX changes will be sent.');const footer=node('footer'),cancel=node('button','Back'),confirm=node('button','Confirm changes');cancel.type=confirm.type='button';confirm.className='button primary';footer.append(cancel,confirm);confirmation.append(h,description,list,note,footer);form.append(confirmation);
  function back(){confirmation.close();actionDialog.showModal();lastSubmit?.focus();}cancel.onclick=back;confirmation.addEventListener('cancel',e=>{e.preventDefault();back();});
  confirm.onclick=()=>{confirmation.close();approved=true;form.requestSubmit(lastSubmit);};
  form.addEventListener('submit',event=>{
   const button=event.submitter;if(!button||approved){approved=false;return;}
   event.preventDefault();lastSubmit=button;list.replaceChildren();
   if(button.value==='import'){description.textContent='Import the group paths and component values in the selected CSV as drafts. Table selection is not used.';list.append(node('p',panel.querySelector('[type=file]')?.files[0]?.name||'CSV proposals'));}
   else {const groups=new Map();for(const box of selected()){const row=box.closest('tr'),key=row.dataset.policy||'No policy';groups.set(key,[...(groups.get(key)||[]),row.dataset.name||box.value]);}
    description.textContent=`${button.textContent.trim()} for ${selected().length} group${selected().length===1?'':'s'} in this environment?`;
    for(const [policy,names]of groups){list.append(node('h3',policy));const ul=node('ul');names.forEach(name=>ul.append(node('li',name)));list.append(ul);}
   }
   actionDialog.close();confirmation.showModal();cancel.focus();
  });
  choose('propose');
  if(form.dataset.actionError==='true'){const action=form.dataset.submittedAction||'propose';open(actions[action]?action:'propose',form.querySelector('[data-naming-open]'));}
 }
 all?.addEventListener('change',()=>{boxes.forEach(b=>b.checked=all.checked);update();});boxes.forEach(b=>b.addEventListener('change',update));
 form.querySelector('[data-naming-clear]')?.addEventListener('click',()=>{boxes.forEach(b=>b.checked=false);update();});update();
})();

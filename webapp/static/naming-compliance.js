(() => {
 const form=document.getElementById('naming-proposals');if(!form)return;
 const boxes=[...form.querySelectorAll('[name=selected]')],all=form.querySelector('[data-naming-select-all]'),status=form.querySelector('[data-naming-selected]');
 function update(){const n=boxes.filter(b=>b.checked).length;if(status)status.textContent=n?`${n} groups selected on this page`:'No groups selected';if(all){all.checked=n>0&&n===boxes.length;all.indeterminate=n>0&&n<boxes.length;}form.querySelectorAll('button[name=action]:not([value=import])').forEach(b=>b.disabled=!n);}
 form.addEventListener('submit',event=>{
  const button=event.submitter;if(!button||button.value==='import')return;
  const selected=boxes.filter(box=>box.checked);
  if(selected.length>100){event.preventDefault();window.alert('Select at most 100 groups. No changes were saved.');return;}
  const groups=new Map();for(const box of selected){const row=box.closest('tr'),key=row.dataset.policy||'No policy';groups.set(key,[...(groups.get(key)||[]),row.dataset.name||box.value]);}
  const summary=[...groups].map(([policy,names])=>policy+':\n'+names.join('\n')).join('\n\n');
  if(!window.confirm(`${button.textContent.trim()} for ${selected.length} groups in this environment?\n\n${summary}\n\nAll selected groups must pass validation; otherwise nothing is saved. No NSX changes will be sent.`))event.preventDefault();
 });
 all?.addEventListener('change',()=>{boxes.forEach(b=>b.checked=all.checked);update();});boxes.forEach(b=>b.addEventListener('change',update));update();
})();

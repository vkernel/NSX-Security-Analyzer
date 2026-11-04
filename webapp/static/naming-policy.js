/* Ordered formset; no executable user-supplied templates or naming expressions. */
(() => {
 const root=document.querySelector('[data-naming-components]'),add=document.querySelector('[data-add-component]');
 if(!root||!add)return;
 add.hidden=false;
 const total=document.querySelector('[name="components-TOTAL_FORMS"]'),status=document.querySelector('[data-component-status]');
 function order(){[...root.children].forEach((row,i)=>{row.querySelector('[name$="-ORDER"]').value=i+1;});root.closest('form').dispatchEvent(new Event('change',{bubbles:true}));}
 add.onclick=()=>{const n=Number(total.value);if(n>=16){status.textContent='Up to 16 components are supported. Remove components and save before adding more.';return;}
 const template=document.getElementById('naming-empty-component').innerHTML.replaceAll('__prefix__',String(n));
 const container=document.createElement('div');container.innerHTML=template;root.append(container.firstElementChild);total.value=n+1;order();root.lastElementChild.querySelector('input').focus();};
 // Bind to the row itself: the sidebar moves it outside this list and form.
 const attached=new WeakSet();
 function attach(row){
  if(attached.has(row))return;attached.add(row);
  const value=key=>row.querySelector(`[name$="-${key}"]`)?.value||'';
  function summary(){
   const source=value('source');
   for(const key of ['scope','literal']){const field=row.querySelector(`[name$="-${key}"]`);const wrapper=field?.closest('.form-field');if(wrapper)wrapper.hidden=key==='scope'?source!=='tag':source!=='fixed';}
   const removed=row.querySelector('[name$="-DELETE"]')?.checked;row.classList.toggle('component-removed',!!removed);
   const detail=source==='fixed'?`Fixed text: ${value('literal')||'Not set'}`:
    source==='tag'?`Tag scope: ${value('scope')||'Not set'}`:
    source==='single_host_ip'?'Explicit single host IP':source==='identity'?'Group path suffix':source==='type'?'Group type':'Choose a source';
   row.querySelector('summary').textContent=`${removed?'Remove · ':''}${value('field')||'New field'} · ${detail}`;
  }
  const grid=row.querySelector('.form-grid');
  if(grid&&!row.querySelector('[data-component-advanced]')){
   const advanced=document.createElement('details');advanced.dataset.componentAdvanced='';const heading=document.createElement('summary');heading.textContent='Formatting and value mappings';const fields=document.createElement('div');fields.className='form-grid';advanced.append(heading,fields);
   for(const key of ['case','format','max_length','mapping_text','ORDER']){const control=row.querySelector(`[name$="-${key}"]`);const wrapper=control?.closest('.form-field');if(wrapper)fields.append(wrapper);}
   if(fields.childElementCount){grid.after(advanced);if(advanced.querySelector('.errorlist'))advanced.open=true;}
  }
  row.addEventListener('input',summary);row.addEventListener('change',event=>{
   if(event.target.matches('[name$="-source"]') && value('source')==='single_host_ip'){
    row.querySelector('[name$="-case"]').value='keep';row.querySelector('[name$="-format"]').value='ip_address';
    const length=row.querySelector('[name$="-max_length"]');if(Number(length.value)<45)length.value='45';
    row.querySelector('[name$="-required"]').checked=true;
   }
   summary();
  });summary();
 }
 function enhance(){root.querySelectorAll('.naming-component').forEach(attach);}
 enhance();new MutationObserver(enhance).observe(root,{childList:true});
 root.addEventListener('click',event=>{const button=event.target.closest('[data-move]');if(!button)return;const row=button.closest('fieldset');
 if(button.dataset.move==='up'&&row.previousElementSibling)row.previousElementSibling.before(row);
 if(button.dataset.move==='down'&&row.nextElementSibling)row.nextElementSibling.after(row);
 order();button.focus();status.textContent='Component order updated. Save the draft to preview names.';});
})();

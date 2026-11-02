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
   const detail=source==='fixed'?`Fixed text: ${value('literal')||'Not set'}`:
    source==='tag'?`Tag scope: ${value('scope')||'Not set'}`:
    source==='identity'?'Group path suffix':source==='type'?'Group type':'Choose a source';
   row.querySelector('summary').textContent=`${value('field')||'New field'} · ${detail}`;
  }
  row.addEventListener('input',summary);row.addEventListener('change',summary);summary();
 }
 function enhance(){root.querySelectorAll('.naming-component').forEach(attach);}
 enhance();new MutationObserver(enhance).observe(root,{childList:true});
 root.addEventListener('click',event=>{const button=event.target.closest('[data-move]');if(!button)return;const row=button.closest('fieldset');
 if(button.dataset.move==='up'&&row.previousElementSibling)row.previousElementSibling.before(row);
 if(button.dataset.move==='down'&&row.nextElementSibling)row.nextElementSibling.after(row);
 order();button.focus();status.textContent='Component order updated. Save the draft to preview names.';});
})();

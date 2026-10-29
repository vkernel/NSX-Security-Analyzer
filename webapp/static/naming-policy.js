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
 root.addEventListener('input',event=>{const row=event.target.closest('fieldset');if(row&&event.target.name.endsWith('-field'))row.querySelector('summary').textContent=event.target.value||'New field';});
 root.addEventListener('click',event=>{const button=event.target.closest('[data-move]');if(!button)return;const row=button.closest('fieldset');
 if(button.dataset.move==='up'&&row.previousElementSibling)row.previousElementSibling.before(row);
 if(button.dataset.move==='down'&&row.nextElementSibling)row.nextElementSibling.after(row);
 order();button.focus();status.textContent='Component order updated. Save the draft to preview names.';});
})();

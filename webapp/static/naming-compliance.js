(() => {
 const form=document.getElementById('naming-proposals');if(!form)return;
 const boxes=[...form.querySelectorAll('[name=selected]')],all=form.querySelector('[data-naming-select-all]'),status=form.querySelector('[data-naming-selected]');
 function update(){const n=boxes.filter(b=>b.checked).length;if(status)status.textContent=n?`${n} groups selected on this page`:'No groups selected';if(all){all.checked=n>0&&n===boxes.length;all.indeterminate=n>0&&n<boxes.length;}form.querySelectorAll('button[name=action]:not([value=import])').forEach(b=>b.disabled=!n);}
 all?.addEventListener('change',()=>{boxes.forEach(b=>b.checked=all.checked);update();});boxes.forEach(b=>b.addEventListener('change',update));update();
})();

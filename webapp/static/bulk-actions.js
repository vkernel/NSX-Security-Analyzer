/* Shared page-local selection. Re-resolves controls after live region replacement. */
(() => {
  const boxes = id => [...document.querySelectorAll('[data-bulk-choice]')].filter(box => box.dataset.bulkChoice === id && !box.disabled);
  function update(id) {
    const toolbar = document.querySelector(`[data-bulk-toolbar="${id}"]`);
    if (!toolbar) return;
    const available=boxes(id), selected=available.filter(box=>box.checked), count=selected.length;
    toolbar.hidden=!count;
    toolbar.querySelector('[data-bulk-count]')?.replaceChildren(document.createTextNode(`${count} selected on this page`));
    let disabled=false;
    toolbar.querySelectorAll('[data-bulk-action]').forEach(button=>{
      button.hidden=count>0 && selected.every(box=>!(box.dataset.bulkAllowed || '').split(' ').includes(button.dataset.bulkAction));
      button.disabled=!count || count>100 || selected.some(box=>!(box.dataset.bulkAllowed || '').split(' ').includes(button.dataset.bulkAction)) || (button.hasAttribute('data-bulk-one-environment') && new Set(selected.map(box=>box.dataset.environment)).size!==1);
      disabled ||= button.disabled && !button.hidden;
      button.title=button.disabled?'Every selected item must support this action; deletion previews also require one environment.':'';
    });
    const help=toolbar.querySelector('[data-bulk-help]');
    if(help) help.textContent=disabled?'Unavailable actions require every selected item to be eligible. Deletion previews also require one environment.':'Selection applies to this page only, up to 100 items.';
    document.querySelectorAll('[data-bulk-all]').forEach(all=>{
      if(all.dataset.bulkAll!==id)return;
      all.disabled=!available.length;all.checked=count>0&&count===Math.min(100,available.length);all.indeterminate=count>0&&!all.checked;
    });
  }
  document.addEventListener('change',event=>{
    const target=event.target;
    if(target.dataset.bulkAll){boxes(target.dataset.bulkAll).forEach((box,index)=>box.checked=target.checked&&index<100);update(target.dataset.bulkAll);}
    if(target.dataset.bulkChoice)update(target.dataset.bulkChoice);
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-bulk-action],[data-bulk-clear]');if(!button)return;
    const toolbar=button.closest('[data-bulk-toolbar]'), id=toolbar.dataset.bulkToolbar;
    if(button.hasAttribute('data-bulk-clear')){boxes(id).forEach(box=>box.checked=false);update(id);return;}
    update(id);if(button.disabled)return;
    const form=document.getElementById(id), selected=boxes(id).filter(box=>box.checked);
    const values=form.querySelector('[data-bulk-values]');values.replaceChildren();
    selected.forEach(box=>{const input=document.createElement('input');input.type='hidden';input.name='selected';input.value=box.value;values.append(input);});
    form.elements.action.value=button.dataset.bulkAction;
    form.elements.return_to.value=location.pathname+location.search+location.hash;
    form.requestSubmit();
  });
  const refresh=()=>document.querySelectorAll('[data-bulk-toolbar]').forEach(bar=>update(bar.dataset.bulkToolbar));
  window.addEventListener('pageshow',refresh);
  document.addEventListener('bulk:updated',refresh);
})();

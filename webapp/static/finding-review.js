(() => {
  const form=document.querySelector('[data-finding-review]');if(!form)return;
  const action=form.elements.action,owner=form.elements.owner,ticket=form.elements.change_ticket;
  const search=form.querySelector('#reviewer-search'),status=form.querySelector('[data-owner-results]');
  const options=Array.from(owner.options,option=>({value:option.value,label:option.textContent}));
  search.hidden=false;
  const filter=()=>{
    const selected=owner.value,query=search.value.trim().toLocaleLowerCase();
    const matches=options.filter(option=>option.value && option.label.toLocaleLowerCase().includes(query));
    const shown=options.filter(option=>!option.value || option.value===selected || matches.includes(option));
    owner.replaceChildren(...shown.map(option=>new Option(option.label,option.value,false,option.value===selected)));
    status.textContent=query?matches.length+' matching owner(s).'+(selected&&!matches.some(option=>option.value===selected)?' Current selection retained.':''):'';
  };
  search.addEventListener('input',filter);
  const update=()=>{
    form.querySelectorAll('[data-review-field]').forEach(field=>field.hidden=field.dataset.reviewField!==action.value);
    owner.disabled=action.value!=='assign';owner.required=action.value==='assign';
    ticket.disabled=action.value!=='complete';ticket.required=action.value==='complete';
  };
  action.addEventListener('change',update);update();
})();

/* Shared editing feedback. Field values stay in memory, never browser storage. */
(() => {
  const states = [];
  for (const form of document.querySelectorAll('form[data-edit-form]')) {
    const fields = () => [...form.elements].filter(el => el.name && !['submit','button','hidden'].includes(el.type));
    const capture = () => JSON.stringify(fields().map(el => [el.name, el.type==='checkbox'||el.type==='radio'?el.checked:el.value]).sort((a,b)=>a[0].localeCompare(b[0])));
    const baseline = capture();
    let status = form.querySelector('.unsaved-status');
    if (!status) {
      status=document.createElement('p');status.className='unsaved-status';status.setAttribute('role','status');
      form.prepend(status);
    }
    const state={dirty:false, submitting:false};states.push(state);
    const update=()=>{state.dirty=form.dataset.pending==='true'||capture()!==baseline;status.textContent=state.dirty?'Unsaved changes':form.querySelector('.errorlist')?'Changes not saved — check the highlighted fields.':'No unsaved changes';};
    form.addEventListener('input',update);form.addEventListener('change',update);
    // Buttons can be outside the form in the shared dialog footer.
    form.addEventListener('submit',event=>queueMicrotask(()=>{if(!event.defaultPrevented)state.submitting=true;}));
    form.addEventListener('close',()=>setTimeout(update,0),true);
    window.addEventListener('pageshow',()=>{state.submitting=false;update();});
    const errors=[...form.querySelectorAll('.errorlist')];
    if(errors.length){
      const summary=document.createElement('div');summary.className='form-error-summary';summary.tabIndex=-1;summary.setAttribute('role','alert');
      const title=document.createElement('strong');title.textContent='Check the following before saving';const list=document.createElement('ul');summary.append(title,list);
      errors.forEach(error=>{
        const field=error.closest('.form-field')?.querySelector('input,select,textarea');
        const item=document.createElement('li');const text=error.textContent.trim();
        if(field?.id){const link=document.createElement('a');link.href='#'+field.id;link.textContent=text;link.addEventListener('click',event=>{event.preventDefault();let parent=field.parentElement;while(parent){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}field.focus();field.scrollIntoView({block:'center'});});field.setAttribute('aria-invalid','true');item.append(link);}else item.textContent=text;
        list.append(item);
      });form.prepend(summary);summary.focus();
    }
    update();
  }
  window.addEventListener('beforeunload',event=>{if(states.some(state=>state.dirty&&!state.submitting)){event.preventDefault();event.returnValue='';}});
})();

/* Evidence picker: values are text nodes, never executable markup. */
(() => {
 const config=document.querySelector('[data-naming-selectors]');if(!config)return;
 const dialog=document.querySelector('[data-naming-picker]'),search=dialog.querySelector('[data-picker-search]'),results=dialog.querySelector('[data-picker-results]'),status=dialog.querySelector('[data-picker-status]');
 let target=null,scope='',environment='',timer,controller,opener;
 function node(tag,text){const e=document.createElement(tag);e.textContent=text;return e;}
 function set(input,value){input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));}
 function env(input){return input.closest('form')?.querySelector('[name=environment]')?.value??config.dataset.environment;}
 function close(){controller?.abort();clearTimeout(timer);dialog.close();opener?.focus();}
 dialog.querySelector('[data-picker-close]').onclick=close;
 dialog.addEventListener('cancel',()=>{controller?.abort();clearTimeout(timer);});
 dialog.querySelector('[data-picker-manual]').onclick=()=>{close();target?.focus();};
 async function load(){
  controller?.abort();controller=new AbortController();const active=controller;results.replaceChildren();status.textContent='Loading collected evidence…';
  const params=new URLSearchParams({environment,scope,q:search.value});
  try{const response=await fetch(config.dataset.url+'?'+params,{signal:active.signal,headers:{Accept:'application/json'}});if(!response.ok)throw Error();const data=await response.json();if(active!==controller)return;
   status.textContent=`${data.total} match(es). Showing up to 50. `+(data.warnings||[]).join(' ');
   for(const item of data.results){const section=node('section',''),button=node('button',item.value);button.type='button';button.className='button';button.onclick=()=>{set(target,item.value);close();};section.append(button,node('p',`${item.groups} groups · ${item.vms} VMs · ${item.groups?'Group definition evidence':'VM-only suggestion'}`));
    section.append(node('small',item.environments.map(e=>`${e.name}: ${e.groups} groups / ${e.vms} VMs`).join(' · ')));
    if(item.missing_environments.length)section.append(node('p','Not observed in: '+item.missing_environments.join(', ')));
    results.append(section);
   }
   if(!data.results.length)results.append(node('p','No matching collected values. Try another search or enter a new value manually.'));
  }catch(e){if(e.name!=='AbortError')status.textContent='Evidence could not be loaded. Search again to retry, or enter a value manually.';}
 }
 search.oninput=()=>{clearTimeout(timer);controller?.abort();timer=setTimeout(load,250);};
 function pick(input,actualScope,button){target=input;scope=actualScope;environment=env(input);opener=button;search.value='';dialog.querySelector('h2').textContent=scope?'Choose a value for '+scope:'Choose a tag scope';dialog.showModal();search.focus();load();}
 function componentScope(form){const field=form.querySelector('[name=match_field]')?.value;const rows=[...form.querySelectorAll('.naming-component')];return rows.find(r=>r.querySelector('[name$="-field"]')?.value===field)?.querySelector('[name$="-scope"]')?.value||'';}
 function attach(input,scopeGetter,label){if(!input||input.dataset.pickerReady)return;input.dataset.pickerReady='1';const button=node('button',label);button.type='button';button.className='button naming-picker-trigger';const hint=node('small','');hint.setAttribute('role','status');button.onclick=()=>{const actual=scopeGetter();if(actual===null){hint.textContent='Choose the component scope (and category field, if applicable) first.';input.focus();return;}hint.textContent='';pick(input,actual,button);};input.after(button,hint);}
 function enhance(){
  document.querySelectorAll('[name$="-scope"], [name="scope"]').forEach(input=>attach(input,()=>'', 'Choose existing scope'));
  const category=document.querySelector('[name=match_value]');attach(category,()=>componentScope(category.form)||null,'Choose existing category value');
  const value=document.querySelector('#naming-proposals [name=value]');attach(value,()=>value.form.querySelector('[name=scope]').value||null,'Choose existing tag value');
  document.querySelectorAll('.naming-component').forEach(row=>{
   if(row.dataset.mappingReady)return;row.dataset.mappingReady='1';
   const textarea=row.querySelector('[name$="-mapping_text"]');if(!textarea)return;
   const details=node('details',''),summary=node('summary','Build a value mapping'),wrap=node('div','');wrap.className='naming-mapping-builder';details.append(summary,wrap);
   const before=node('input',''),after=node('input','');before.type=after.type='text';before.maxLength=256;after.maxLength=128;
   const beforeLabel=node('label','Existing tag value or type');beforeLabel.append(before);const afterLabel=node('label','Naming output (for example PRD)');afterLabel.append(after);wrap.append(beforeLabel,afterLabel);
   attach(before,()=>row.querySelector('[name$="-scope"]').value||null,'Choose existing tag value');
   const button=node('button','Add / update mapping'),feedback=node('p','');button.type='button';feedback.setAttribute('role','status');wrap.append(button,feedback);
   button.onclick=()=>{const a=before.value.trim(),b=after.value.trim();if(!a||!b||/[=\r\n]/.test(a)||/[\r\n]/.test(b)){feedback.textContent='Enter a value and output; the source must not contain = or line breaks.';return;}
    const lines=textarea.value.split('\n').filter(s=>s.trim()&&s.split('=')[0].trim()!==a);lines.push(a+'='+b);const output=lines.join('\n');if(output.length>8000){feedback.textContent='Mapping limit reached.';return;}set(textarea,output);feedback.textContent='Mapping added to the draft. Save and preview before activation.';
   };textarea.before(details);
  });
  const field=document.querySelector('[name=match_field]');if(field){let list=document.getElementById('naming-logical-fields');if(!list){list=document.createElement('datalist');list.id='naming-logical-fields';field.after(list);field.setAttribute('list',list.id);}list.replaceChildren(...[...document.querySelectorAll('.naming-component [name$="-field"]')].map(e=>{const o=document.createElement('option');o.value=e.value;return o;}));}
 }
 enhance();document.querySelector('[data-naming-components]')?.addEventListener('input',enhance);
 const root=document.querySelector('[data-naming-components]');if(root)new MutationObserver(()=>{enhance();schedule();}).observe(root,{childList:true});
 // Server evaluates an illustrative saved group with the same validation/evaluator as assessment.
 const form=root?.closest('form'),example=document.querySelector('[data-naming-example]');let exampleTimer,exampleController;
 function schedule(){if(!form||!example)return;clearTimeout(exampleTimer);exampleController?.abort();example.textContent='Updating example…';exampleTimer=setTimeout(refreshExample,600);}
 async function refreshExample(){exampleController=new AbortController();const active=exampleController;const data=new FormData(form);data.set('policy_id',config.dataset.policy);const selected=form.querySelector('[name=environment]');if(selected)data.set('environment',selected.value);
  try{const response=await fetch(config.dataset.exampleUrl,{method:'POST',body:data,signal:active.signal,headers:{Accept:'application/json'}});if(!response.ok)throw Error();const result=await response.json();if(active!==exampleController)return;example.replaceChildren();if(result.name){example.append(node('strong',`${result.environment} · ${result.name} → ${result.proposed||'Needs review'}`));example.append(node('p',result.status));const list=node('ol','');for(const c of result.components)list.append(node('li',`${c.field}: ${c.observed} → ${c.output||'Unavailable'}${c.issue?' · '+c.issue:''}`));example.append(list);}example.append(node('p',result.message));if(result.warning)example.append(node('p',result.warning));}
  catch(e){if(e.name!=='AbortError')example.textContent='Example unavailable. Edit a field to retry, or save and use Preview affected groups.';}
 }
 form?.addEventListener('input',schedule);form?.addEventListener('change',schedule);schedule();
})();

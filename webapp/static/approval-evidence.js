/* Read-only evidence updates never replace the decision form or open disclosures. */
(() => {
  const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  const text=value=>{if(typeof value==='string')return value;if(value && (value.display_name||value.name))return [value.display_name||value.name,value.path||value.id||value.external_id,...(value.ip_addresses||[])].filter(Boolean).join(' · ');return JSON.stringify(value);};
  function section(parent,title,load,key){
    const details=make('details'),summary=make('summary',title),input=make('input'),list=make('ul'),controls=make('div'),previous=make('button','Previous'),next=make('button','Next'),status=make('span');
    details.className='review-disclosure';details.dataset.evidenceKey=key||'';input.type='search';input.placeholder='Search this list';input.setAttribute('aria-label','Search '+title);previous.type=next.type='button';previous.className=next.className='button';
    controls.className='form-actions';controls.append(previous,status,next);details.append(summary,input,list,controls);parent.append(details);
    let page=0,version=0,timer;
    async function render(){const current=++version;status.textContent='Loading…';try{const data=await load(input.value,page);if(current!==version)return;list.replaceChildren(...data.items.map(v=>make('li',text(v))));status.textContent=data.count?`${page*25+1}–${Math.min((page+1)*25,data.count)} of ${data.count}`:'No entries available';previous.disabled=page===0;next.disabled=(page+1)*25>=data.count;}catch(e){status.textContent=e.message;}}
    details.addEventListener('toggle',()=>{if(details.open)render();});input.addEventListener('input',()=>{clearTimeout(timer);page=0;timer=setTimeout(render,200);});previous.onclick=()=>{page--;render();};next.onclick=()=>{page++;render();};
  }
  const saved=document.getElementById('approval-saved-sections');
  if(saved){for(const data of JSON.parse(saved.textContent)){section(document.getElementById('saved-approval-sections'),`${data.title} (${data.available ? data.items.length : "not retained"})`,async(q,page)=>{const rows=data.items.filter(v=>text(v).toLowerCase().includes(q.toLowerCase()));return {items:rows.slice(page*25,(page+1)*25),count:rows.length};},data.key);}}
  const root=document.querySelector('[data-live-evidence]');if(!root)return;
  const url=root.dataset.liveEvidence,status=root.querySelector('[data-live-status]'),bar=root.querySelector('progress'),form=root.querySelector('form'),button=form.querySelector('button'),results=root.querySelector('[data-live-results]');
  let timer,signature='',busy=false,failures=0;
  async function get(target,options){const response=await fetch(target,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000),...options});if(response.redirected)throw Error('Session expired. Reload and sign in.');if(!response.ok){let data;try{data=await response.json();}catch{}throw Error(data?.error||'Unable to load verification. Reconnecting automatically.');}return response.json();}
  function show(data){
    status.textContent=data.message;
    if(!data.stale && ['queued','running','changed','failed','stale'].includes(data.status))document.querySelectorAll('[data-finding-review] button[value=approve],[data-finding-review] button[value=complete]').forEach(b=>b.disabled=true);
    const active=['queued','running'].includes(data.status);button.disabled=active;bar.hidden=!active;bar.value=data.progress||0;
    root.querySelector('[data-live-time]').textContent=data.finished_at?'Checked '+new Date(data.finished_at).toLocaleString()+(data.stale?' · Older than current saved evidence':''):'';

    const key=data.status+data.requested_at+data.finished_at;if(key!==signature){signature=key;results.replaceChildren();const r=data.result||{};
      if(r.coverage)results.append(make('p','Coverage: '+r.coverage));
      const facts=make('dl');facts.className='review-facts';for(const [label,value] of r.facts||[]){const item=make('div');item.append(make('dt',label),make('dd',value));facts.append(item);}results.append(facts);
      for(const error of r.errors||[])results.append(make('p',error));
      for(const change of r.changes||[])results.append(make('p',`${change.field}: ${change.before} → ${change.after}`));
      for(const entry of r.sections||[])section(results,`${entry.title}: ${entry.count}${entry.retained<entry.count?' (first '+entry.retained+' retained)':''}`,async(q,page)=>{const u=new URL(url,location.href);u.search=new URLSearchParams({section:entry.key,q,page});return get(u);},entry.key);
    }
    timer=setTimeout(refresh,active?3000:15000);
  }
  async function syncDecision(){
    const response=await fetch(location.href,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(15000)});
    if(!response.ok||response.redirected)throw Error('Approval status unavailable. Reconnecting automatically.');
    const doc=new DOMParser().parseFromString(await response.text(),'text/html');
    const current=document.querySelector('[data-finding-review]'),next=doc.querySelector('[data-finding-review]');
    if(!current||!next)throw Error('Approval controls unavailable.');
    const changed=current.querySelector('[name=revision]').value!==next.querySelector('[name=revision]').value;
    let notice=root.querySelector('[data-review-change]');
    if(changed){
      current.querySelectorAll('button[name=action]').forEach(b=>b.disabled=true);
      if(!notice){notice=make('p');notice.dataset.reviewChange='';const link=make('a','Review updated finding');link.href=location.href;notice.append('This finding changed. Your draft is retained; review the updated evidence before continuing. ',link);root.append(notice);}
      return;
    }
    // The server remains the authority; successful verification alone never enables approval.
    current.querySelectorAll('button[name=action]').forEach(button=>{
      const fresh=next.querySelector(`button[name=action][value="${button.value}"]`);
      button.disabled=!fresh||fresh.disabled;
      if(fresh?.hasAttribute('aria-describedby'))button.setAttribute('aria-describedby',fresh.getAttribute('aria-describedby'));else button.removeAttribute('aria-describedby');
    });
    const oldManual=current.querySelector('.review-manual'),newManual=next.querySelector('.review-manual');
    if(newManual&&!oldManual)current.prepend(newManual.cloneNode(true));
    if(!newManual&&oldManual)oldManual.remove();
    const oldBlock=current.querySelector('#decision-blocker'),newBlock=next.querySelector('#decision-blocker');
    if(oldBlock)oldBlock.remove();
    if(newBlock)current.querySelector('.review-buttons').before(newBlock.cloneNode(true));
    const oldReady=document.querySelector('.review-readiness'),newReady=doc.querySelector('.review-readiness');
    if(oldReady&&newReady)oldReady.replaceWith(newReady);
  }
  async function refresh(){
    if(busy)return;clearTimeout(timer);
    if(document.hidden){timer=setTimeout(refresh,15000);return;}
    busy=true;
    try{show(await get(url));await syncDecision();failures=0;root.querySelector('[data-live-refresh]').hidden=true;}
    catch(e){status.textContent=e.message;root.querySelector('[data-live-refresh]').hidden=false;clearTimeout(timer);timer=setTimeout(refresh,Math.min(60000,3000*2**Math.min(++failures,4)));}
    finally{busy=false;}
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
  window.addEventListener('online',refresh);
  form.addEventListener('submit',async event=>{event.preventDefault();clearTimeout(timer);button.disabled=true;try{show(await get(url,{method:'POST',body:new FormData(form)}));}catch(e){status.textContent=e.message;button.disabled=false;timer=setTimeout(refresh,3000);}});
  root.querySelector('[data-live-refresh]').onclick=refresh;
  refresh();
})();

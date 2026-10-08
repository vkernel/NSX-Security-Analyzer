/* Snapshot data is fetched in bounded pages; the original audit stays in PostgreSQL. */
(() => {
  function endpoint(payload, params) {
    const url = new URL('data/', location.href.split('#')[0].split('?')[0]);
    url.search = new URLSearchParams(params).toString();
    return url;
  }
  window.reportData = async (payload, params, signal) => {
    const response = await fetch(endpoint(payload,params), {signal,credentials:'same-origin',headers:{Accept:'application/json'}});
    if (response.redirected) throw new Error('Your session has expired. Reload the page to sign in.');
    let result;
    try { result = await response.json(); } catch (_) { throw new Error('Unable to load this section. Please try again.'); }
    if (!response.ok) throw new Error(result.error || 'Unable to load this section. Please try again.');
    return result;
  };
  window.createRemoteReportController = (widget, {payload,rowPool,renderRow,columnFilters}) => {
    const tools = widget.querySelector('.table-tools'), tbody = widget.querySelector('tbody');
    const search=tools.querySelector('input'), mode=tools.querySelector('.search-mode'), syntax=tools.querySelector('.search-syntax');
    syntax.title='Regular expressions are evaluated by PostgreSQL. Use plain text for literal searches.';
    const evidence=tools.querySelector('.search-evidence'), error=tools.querySelector('.search-error');
    const size=tools.querySelector('.page-size'), sort=tools.querySelector('.sort-key'), order=tools.querySelector('.sort-order');
    const previous=tools.querySelector('.previous'), next=tools.querySelector('.next'), status=tools.querySelector('.page-status');
    const empty=widget.querySelector('.no-matches'), panel=widget.dataset.serverTable;
    if (['25','50','100'].includes(document.body.dataset.reportPageSize)) size.value=document.body.dataset.reportPageSize;
    const policy=['dfw-policies','empty-policies'].includes(panel), tags=panel.startsWith('tags-'), scopes=panel==='tags-scopes';
    const dfw=policy || ['dfw-rules','zero-hit-rules','disabled-rules','unknown-statistics','empty-group-rules','dfw-scope-rules'].includes(panel);
    const keys = panel==='all-vms' ? ['name','power_state','tag_count','group_count',null] : scopes ? ['name','tag_count','vm_count','group_count','other_count',null] : tags ? ['name','status','vm_count','group_count','other_count',null]
      : dfw ? ['name',policy?'category':'policy_name',policy?'status':'hit_status',policy?'rule_count':'hit_count',null]
      : ['name','kind','usage','membership',null];
    const permitted = [...new Set([...keys.filter(Boolean),'path',...(dfw && !policy?['category','rule_id','policy_rule_id']:[]),...(tags&&!scopes?['scope']:[]),...(!dfw&&!tags&&panel!=='all-vms'?['method','references']:[])])];
    sort.replaceChildren(...permitted.map(key=>new Option(key.replaceAll('_',' '),key)));
    let countToken='', page=0, timer, sequence=0, pending, mounted=true, lastParams=null, shown=[];
    function params(op='rows') { return {op,panel,count_token:countToken,q:search.value,mode:mode.value,syntax:syntax.value,evidence:evidence.checked?'1':'0',size:size.value,sort:sort.value,order:order.value,page,filters:JSON.stringify([...filters])}; }
    const getValues=async (index,text,signal)=> {const result=await window.reportData(payload,{...params('values'),column:index,value:text},signal);getValues.message=result.message || '';return result.values;};
    getValues.remote=true;
    const filters=columnFilters(widget.querySelector('table'),()=>{page=0;lastParams=null;refresh();},getValues);
    const headers=[];
    widget.querySelectorAll('thead th').forEach((header,index)=>{
      const key=keys[index]; if(!key)return;
      const button=document.createElement('button');button.type='button';button.className='header-sort';button.textContent='↕';
      button.setAttribute('aria-label','Sort by '+header.textContent.replace(' ▾',''));
      button.addEventListener('click',()=>{order.value=sort.value===key&&order.value==='asc'?'desc':'asc';sort.value=key;page=0;lastParams=null;refresh();});
      header.append(button);headers.push({header,button,key});
    });
    async function refresh() {
      if(widget.closest('[data-panel]').hidden)return;
      mounted=true;
      const query=params(), fingerprint=JSON.stringify(query);
      if(lastParams===fingerprint || widget.dataset.pendingQuery===fingerprint)return;
      widget.dataset.pendingQuery=fingerprint;
      pending?.abort();pending=new AbortController();const version=++sequence;
      widget.setAttribute('aria-busy','true');status.textContent='Loading…';error.textContent='';previous.disabled=next.disabled=true;empty.hidden=true;
      // Remove stale rows immediately so a failed request cannot look like new results.
      tbody.replaceChildren();shown.forEach(id=>delete rowPool[id]);shown=[];
      try {
        const result=await window.reportData(payload,query,pending.signal);
        if(version!==sequence || !mounted)return;
        countToken=result.count_token || '';page=result.page;lastParams=JSON.stringify(params());
        result.rows.forEach(row=>{rowPool[row.id]=row;shown.push(row.id);});
        tbody.innerHTML=shown.map(id=>renderRow(id)).join('');
        status.textContent=result.count?`${page*Number(size.value)+1}–${Math.min((page+1)*Number(size.value),result.count)} of ${result.count}`:'0 results';
        previous.disabled=page===0;next.disabled=(page+1)*Number(size.value)>=result.count;empty.hidden=result.count!==0;
        headers.forEach(({header,button,key})=>{const active=sort.value===key;button.textContent=active?(order.value==='asc'?'▲':'▼'):'↕';if(active)header.setAttribute('aria-sort',order.value==='asc'?'ascending':'descending');else header.removeAttribute('aria-sort');});
        widget.dispatchEvent(new Event('table-render'));
      } catch(exc) { if(exc.name!=='AbortError' && version===sequence){error.textContent=exc.message;status.textContent='Unable to load results';lastParams=null;} }
      finally {if(version===sequence){widget.removeAttribute('aria-busy');delete widget.dataset.pendingQuery;}}
    }
    search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>{page=0;lastParams=null;refresh();},200);});
    [mode,syntax,evidence,size,sort,order].forEach(control=>control.addEventListener('change',()=>{clearTimeout(timer);page=0;lastParams=null;refresh();}));
    previous.addEventListener('click',()=>{page--;refresh();});next.addEventListener('click',()=>{page++;refresh();});
    const retry=document.createElement('button');retry.type='button';retry.textContent='Retry';retry.hidden=true;error.after(retry);
    new MutationObserver(()=>retry.hidden=!error.textContent).observe(error,{childList:true});
    retry.addEventListener('click',()=>{lastParams=null;refresh();});
    const exportButton=document.createElement('button');exportButton.type='button';exportButton.textContent='Export CSV';exportButton.title='Export all matching records in the current sort order';
    exportButton.addEventListener('click',()=>{const link=document.createElement('a');link.href=endpoint(payload,params('export'));link.download='snapshot.csv';link.click();});
    tools.append(exportButton);tools.hidden=false;
    window.workspaceTables?.(widget,{filters});
    return {refresh,invalidate(){page=0;lastParams=null;refresh();},unmount(){mounted=false;pending?.abort();sequence++;delete widget.dataset.pendingQuery;clearTimeout(timer);lastParams=null;tbody.replaceChildren();shown.forEach(id=>delete rowPool[id]);shown=[];}};
  };
})();

/* VM evidence is independent of saved report HTML, so older snapshots benefit too. */
(() => {
  const cache = new Map(); let cacheBytes = 0;
  const make = (tag, text) => { const node=document.createElement(tag); if(text!=null)node.textContent=text; return node; };
  async function load(id, section, page=0, path='') {
    const params={op:'vm-relationships',id,section,page,path}, key=JSON.stringify(params);
    if(cache.has(key))return cache.get(key).value;
    const value=await window.reportData(null,params), bytes=JSON.stringify(value).length*2;
    if(bytes<500000){while(cache.size && (cache.size>=30 || cacheBytes+bytes>2000000)){const first=cache.keys().next().value;cacheBytes-=cache.get(first).bytes;cache.delete(first);}cache.set(key,{value,bytes});cacheBytes+=bytes;}
    return value;
  }
  window.showVmRelationships = (body, payload, id, summary) => {
    body.replaceChildren();
    const header=make('p','Configuration relationships only; resolved membership and effective policy are not verified.');body.append(header);
    function paged(parent,section,label,path='') {
      const box=make('details'), title=make('summary',label), content=make('div');box.dataset.evidenceSection='relationships';box.append(title,content);parent.append(box);
      let loaded=false, version=0;
      async function render(page=0) {
        const current=++version;content.replaceChildren(make('p','Loading…'));
        try {
          const data=await load(id,section,page,path);
          if(current!==version || !body.isConnected)return;
          content.replaceChildren();loaded=true;
          if(!data.items.length)content.append(make('p','No relationships recorded.'));
          data.items.forEach(item=>{
            if(section==='related_rules'){
              const rule=make('details'), caption=make('summary',item.name || item.path), detail=make('div');rule.append(caption,detail);content.append(rule);
              let ready=false, busy=false;
              rule.addEventListener('toggle',async()=>{
                if(!rule.open || ready || busy)return;busy=true;detail.replaceChildren(make('p','Loading…'));
                try { const response=await load(id,'rule',0,item.path);const ruleData=response.details;detail.replaceChildren(make('code',ruleData.path),make('p','Action: '+(ruleData.action || 'Unknown')+' · '+(ruleData.disabled?'Disabled':'Enabled')),make('h3','Configured services'));const services=make('ul');(ruleData.services || []).forEach(service=>{services.append(make('li',typeof service==='string'?service:(service.name || service.path)));});detail.append(services);paged(detail,'via_groups','Related groups for this rule',item.path);ready=true; }
                catch(error){detail.replaceChildren(make('p',error.message+' Close and expand to retry.'));} finally {busy=false;}
              });
            } else {const itemNode=make('p'),link=make('a',typeof item==='string'?item:item.name || item.tag || item.path);link.href=section==='tags'?'#tags-all':'#all-groups';link.addEventListener('click',()=>body.closest('dialog')?.close());itemNode.append(link);if(typeof item==='object')itemNode.append(make('code',item.path || item.scope || ''));content.append(itemNode);}
          });
          const previous=make('button','Previous'), next=make('button','Next');previous.type=next.type='button';previous.disabled=page===0;next.disabled=!data.has_next;
          previous.onclick=()=>render(page-1);next.onclick=()=>render(page+1);content.append(previous,make('span',' Page '+(page+1)+' '),next);
        }catch(error){content.replaceChildren(make('p',error.message));const retry=make('button','Retry');retry.type='button';retry.onclick=()=>render(page);content.append(retry);}
      }
      box.addEventListener('toggle',()=>{if(box.open&&!loaded)render();});
    }
    paged(body,'tags','Assigned tags');paged(body,'related_groups','Related groups');paged(body,'related_rules','Related rules and services');
    const advanced=make('details'), title=make('summary','VM inventory details'), content=make('pre');advanced.dataset.evidenceSection='technical';advanced.append(title,content);body.append(advanced);
    let ready=false;advanced.addEventListener('toggle',async()=>{if(!advanced.open||ready)return;content.textContent='Loading…';try{const response=await load(id,'vm');content.textContent=JSON.stringify(response.details,null,2);ready=true;}catch(error){content.textContent=error.message+' Close and expand to retry.';}});
    window.workspaceEvidence?.(body,body.closest('dialog')?.querySelector('h2'),summary);
  };
  document.addEventListener('click',event=>{
    const button=event.target.closest('.detail-button[data-evidence-row]');
    if(!button?.closest('[data-server-table="all-vms"]'))return;
    event.preventDefault();event.stopImmediatePropagation();
    const dialog=document.getElementById('detail-dialog'),body=document.getElementById('detail-body');
    document.getElementById('detail-title').textContent=button.dataset.title;
    window.showVmRelationships(body,null,Number(button.dataset.evidenceRow),null);
    if(!dialog.open)dialog.showModal();
    dialog.addEventListener('close',()=>button.isConnected&&button.focus(),{once:true});
  },true);
})();

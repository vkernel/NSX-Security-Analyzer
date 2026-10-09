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
  window.showVmRelationships = async (body, payload, id, summary) => {
    const generation={};body.evidenceGeneration=generation;
    const current=()=>body.evidenceGeneration===generation && body.closest('dialog')?.open;
    if(!summary){
      window.evidenceState(body,'Loading VM summary…');
      try{summary=(await load(id,'summary')).data;}
      catch(error){if(current())window.evidenceState(body,error.message,()=>window.showVmRelationships(body,payload,id,null));return;}
      if(!current())return;
    }
    body.replaceChildren();
    function relationLink(item,section){
      const label=typeof item==='string'?item:item.name || item.tag || item.path;
      const button=make('button',label);button.type='button';button.className='evidence-object-link';
      const query=section==='tags'?{view:'tags',tag:item.tag,scope:item.scope || ''}:{path:typeof item==='string'?item:item.path};
      button.onclick=()=>window.openRelatedEvidence(body,query,label);
      return button;
    }
    function paged(parent,section,label,path='') {
      const box=make('details'), title=make('summary',label), content=make('div');box.dataset.evidenceSection='relationships';box.append(title,content);parent.append(box);
      let loaded=false, busy=false, version=0;
      async function render(page=0) {
        const sequence=++version;busy=true;window.evidenceState(content,'Loading relationships…');
        try {
          const data=await load(id,section,page,path);
          if(sequence!==version || body.evidenceGeneration!==generation)return;
          content.replaceChildren();loaded=true;
          if(!data.items.length)window.evidenceState(content,'No relationships recorded in this snapshot.');
          data.items.forEach(item=>{
            if(section==='related_rules'){
              const rule=make('details'), caption=make('summary',item.name || item.path), detail=make('div');rule.append(caption,detail);content.append(rule);
              let ready=false, loading=false;
              const show=async()=>{
                if(ready || loading)return;loading=true;window.evidenceState(detail,'Loading rule…');
                try {
                  const response=await load(id,'rule',0,item.path);if(body.evidenceGeneration!==generation)return;
                  const ruleData=response.details;
                  detail.replaceChildren(relationLink(item,'rules'),make('p','Action: '+(ruleData.action || 'Unknown')+' · '+(ruleData.disabled?'Disabled':'Enabled')),make('h3','Configured services'));
                  const services=make('ul');(ruleData.services || []).forEach(service=>{const li=make('li');if(service==='ANY' || service.path==='ANY')li.textContent='Any service';else li.append(relationLink(service,'services'));services.append(li);});
                  if(!services.children.length)services.append(make('li','No configured services recorded.'));
                  detail.append(services);paged(detail,'via_groups','Related groups for this rule',item.path);ready=true;
                }catch(error){window.evidenceState(detail,error.message,show);}finally{loading=false;}
              };
              rule.addEventListener('toggle',()=>{if(rule.open)show();});
            } else {const itemNode=make('p');itemNode.append(relationLink(item,section));if(typeof item==='object')itemNode.append(make('code',item.path || item.scope || ''));content.append(itemNode);}
          });
          if(page || data.has_next){const previous=make('button','Previous'), next=make('button','Next');previous.type=next.type='button';previous.disabled=page===0;next.disabled=!data.has_next;
          previous.onclick=()=>render(page-1);next.onclick=()=>render(page+1);content.append(previous,make('span',' Page '+(page+1)+' '),next);}
        }catch(error){if(body.evidenceGeneration===generation)window.evidenceState(content,error.message,()=>render(page));}finally{busy=false;}
      }
      box.addEventListener('toggle',()=>{if(box.open&&!loaded&&!busy)render();});
    }
    const caveat=make('p','Configuration relationships only. Group references use tag conditions; resolved membership and effective policy are not verified.');caveat.dataset.evidenceSection='relationships';caveat.className='evidence-note';body.append(caveat);
    paged(body,'tags','Assigned tags');paged(body,'related_groups','Groups referencing these tags');paged(body,'related_rules','Related rules and services');
    const advanced=make('details'), title=make('summary','VM inventory details'), content=make('div');advanced.dataset.evidenceSection='technical';advanced.append(title,content);body.append(advanced);
    let ready=false,busy=false;
    const technical=async()=>{if(ready||busy)return;busy=true;window.evidenceState(content,'Loading technical details…');try{const response=await load(id,'vm');if(body.evidenceGeneration!==generation)return;content.replaceChildren(window.technicalEvidence(response.details));ready=true;}catch(error){window.evidenceState(content,error.message,technical);}finally{busy=false;}};
    advanced.addEventListener('toggle',()=>{if(advanced.open)technical();});
    window.workspaceEvidence?.(body,body.closest('dialog')?.querySelector('h2'),{...summary,inventory_type:'Virtual machine'});
  };
  document.addEventListener('click',event=>{
    const button=event.target.closest('.detail-button[data-evidence-row]');
    if(!button?.closest('[data-server-table="all-vms"]'))return;
    event.preventDefault();event.stopImmediatePropagation();
    const dialog=document.getElementById('detail-dialog'),body=document.getElementById('detail-body');
    document.getElementById('detail-title').textContent=button.dataset.title;
    if(!dialog.open)dialog.showModal();
    window.showVmRelationships(body,null,Number(button.dataset.evidenceRow),null);
    dialog.addEventListener('close',()=>{body.evidenceGeneration=null;button.isConnected&&button.focus({preventScroll:true});},{once:true});
  },true);
})();

/* Tags/scopes use current paged evidence, even when their saved HTML is older. */
(() => {
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
  document.addEventListener('click',async event=>{
    const button=event.target.closest('.detail-button');
    if(!button || !(button.dataset.tagRow!==undefined || button.dataset.evidenceView==='scopes' || button.closest('[data-server-table="tags-scopes"]')))return;
    // Unindexed standalone reports retain their original evidence renderer.
    if(!document.querySelector('[data-server-table]'))return;
    event.preventDefault();event.stopImmediatePropagation();
    const dialog=document.getElementById('detail-dialog'),body=document.getElementById('detail-body');
    const token={},controller=new AbortController();if(!button.dataset.evidenceView)dialog.tagPending?.abort();dialog.tagPending=controller;
    dialog.reportRequest=token;body.evidenceGeneration=token;
    const id=Number(button.dataset.tagRow ?? button.dataset.evidenceRow);
    document.getElementById('detail-title').textContent=button.dataset.title || 'Relationships';
    if(!dialog.open)dialog.showModal();
    const current=()=>dialog.open && dialog.reportRequest===token && body.evidenceGeneration===token;
    dialog.addEventListener('close',()=>{controller.abort();body.evidenceGeneration=null;button.isConnected&&button.focus({preventScroll:true});},{once:true});
    const load=(section,page=0)=>window.reportData(null,{op:'tag-relationships',id,section,page},controller.signal);
    async function start(){
      window.evidenceState(body,'Loading summary…');
      try{
        const summary=await load('summary');if(!current())return;body.replaceChildren();
        const caveat=make('p','Visible configuration relationships only. Tags attached to groups are metadata, not proof of traffic matching. Search coverage and inventory gaps apply; absence of references does not establish non-use. Expand a section to load 25 records at a time.');caveat.dataset.evidenceSection='relationships';body.append(caveat);
        function paged(section,label,technical=false){
          const box=make('details'),content=make('div');box.dataset.evidenceSection=technical?'technical':'relationships';box.append(make('summary',label),content);body.append(box);
          let ready=false,busy=false,sequence=0;
          async function render(page=0){
            if(busy)return;busy=true;const version=++sequence;window.evidenceState(content,'Loading…');
            try{
              const result=await load(section,page);if(!current()||version!==sequence)return;
              content.replaceChildren();ready=true;
              for(const item of result.items){
                if(section==='notes'){content.append(make('p',item));continue;}
                if(technical){const detail=make('details');detail.append(make('summary','Condition definition'));detail.addEventListener('toggle',()=>{if(detail.open&&detail.childElementCount===1)detail.append(window.technicalEvidence(item));});content.append(detail);continue;}
                const entry=make('div');entry.className='relationship-entry';
                const name=item?.name || item?.path || 'Reference unavailable';
                if(section==='tags' || (item?.path?.startsWith('/infra/')&&section!=='vms')){
                  const link=make('button',name);link.type='button';link.className='evidence-object-link';
                  link.onclick=()=>window.openRelatedEvidence(body,section==='tags'?{view:'tags',tag:item.name,scope:summary.data.scope || ''}:{path:item.path},name);entry.append(link);
                }else entry.append(make('strong',name));
                if(item?.path)entry.append(make('p',item.path));
                if(section==='tags')entry.append(make('p',`${item.vm_count || 0} VMs · ${item.group_count || 0} groups · ${item.other_count || 0} other resources`));
                if(item?.via_group)entry.append(make('p','Via group: '+item.via_group+' · '+(item.tag_use==='condition'?'Membership condition':'Tag attached to group')));
                if(item?.disabled)entry.append(make('p','Disabled rule'));
                if(item?.resource_type)entry.append(make('p',item.resource_type));content.append(entry);
              }
              if(!result.items.length)content.append(make('p','No relationships recorded.'));
              const prev=make('button','Previous'),next=make('button','Next');prev.type=next.type='button';prev.disabled=page===0;next.disabled=!result.has_next;
              prev.onclick=()=>render(page-1);next.onclick=()=>render(page+1);content.append(prev,make('span',' Page '+(page+1)+' '),next);
            }catch(error){if(current()&&error.name!=='AbortError')window.evidenceState(content,error.message,()=>render(page));}finally{busy=false;}
          }
          box.addEventListener('toggle',()=>{if(box.open&&!ready&&!busy)render();});
        }
        if(summary.view==='scopes')paged('tags','Tags in this scope');
        else{
          paged('notes','Collection notes');paged('vms','VM assignments');paged('group_conditions','Group conditions');paged('group_assignments','Tags attached to groups');paged('other_assignments','Other resource assignments');paged('firewall_references','Firewall references');
          paged('condition_evidence','Matching condition definitions',true);paged('review_conditions','Conditions requiring review',true);
        }
        window.workspaceEvidence?.(body,document.getElementById('detail-title'),{...summary.data,inventory_type:summary.view==='scopes'?'Scope':'Tag',evidence_summary_only:true});

      }catch(error){if(current()&&error.name!=='AbortError')window.evidenceState(body,error.message,start);}
    }
    await start();
  },true);
})();

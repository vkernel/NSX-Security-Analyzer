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

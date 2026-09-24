(() => {
  'use strict';
  let state;
  const timers = new Map();
  const pending = new Map();
  const getState = () => state ||= JSON.parse(document.getElementById('interface-state')?.textContent || '{}');
  const csrf = () => document.querySelector('#interface-csrf input')?.value || '';
  async function post(url, data) {
    const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(data)});
    if (!response.ok || response.redirected) throw new Error('Unable to save preferences. Reload and sign in again if needed.');
    return response.json();
  }
  function save(key, value, status) {
    getState()[key] = value;
    pending.set(key,value);
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(async () => {
      pending.delete(key);
      try { await post('/api/preferences/interface/', {key,value}); if(status) status.textContent='Preferences saved.'; }
      catch (error) { if(status) status.textContent=error.message; }
    }, 400));
  }
  window.addEventListener('pagehide',()=> {
    pending.forEach((value,key)=> {
      clearTimeout(timers.get(key));
      fetch('/api/preferences/interface/', {method:'POST',keepalive:true,headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({key,value})}).catch(()=>{});
    });
    pending.clear();
  });
  function formatTime(value) {
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return value;
    const body=document.body, timeZone=body.dataset.timezone || 'UTC';
    const parts=new Intl.DateTimeFormat('en-GB',{timeZone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZoneName:'short'}).formatToParts(date);
    const p=Object.fromEntries(parts.map(p=>[p.type,p.value]));
    const readable=new Intl.DateTimeFormat('en-GB',{timeZone,day:'2-digit',month:'short',year:'numeric'}).format(date);
    const day=({'iso':`${p.year}-${p.month}-${p.day}`,'day-first':`${p.day}/${p.month}/${p.year}`,'month-first':`${p.month}/${p.day}/${p.year}`})[body.dataset.dateFormat] || readable;
    return `${day} ${p.hour}:${p.minute} ${p.timeZoneName}`;
  }
  window.workspaceTime = value => {
    const time=document.createElement('time'); time.dateTime=value;
    const stamp=new Date(value);
    time.title=Number.isFinite(stamp.getTime()) ? stamp.toISOString().replace('T',' ').replace('Z',' UTC') : '';
    time.textContent=formatTime(value); return time.outerHTML;
  };
  window.workspaceTables = (widget, api) => {
    const table=widget.querySelector('table'), tools=widget.querySelector('.table-tools');
    const key='table:'+(document.body.dataset.tableScope || 'workspace')+':'+widget.closest('[data-panel]').id;
    const remember=document.body.dataset.rememberTables==='true';
    const selectors={search:'input[type=search]',mode:'.search-mode',syntax:'.search-syntax',evidence:'.search-evidence',size:'.page-size',sort:'.sort-key',order:'.sort-order'};
    const controls=Object.fromEntries(Object.entries(selectors).map(([name,selector])=>[name,tools.querySelector(selector)]));
    const headers=Array.from(table.tHead.rows[0].cells);
    headers.forEach((cell,index)=>cell.dataset.columnIndex=index);
    let columns=headers.map((cell,index)=>({index,visible:true}));
    const saved=remember ? getState()[key] : null;
    if(saved) {
      Object.entries(saved.controls || {}).forEach(([name,value])=> {
        const control=controls[name]; if(!control) return;
        if(control.type==='checkbox') control.checked=Boolean(value);
        else if(control.tagName!=='SELECT' || Array.from(control.options).some(option=>option.value===value)) control.value=value;
      });
      (saved.filters || []).forEach(([index,filter])=> {if(index<headers.length) api.filters.set(index,filter);});
      if(saved.columns?.length===headers.length && new Set(saved.columns.map(c=>c.index)).size===headers.length && saved.columns.every(c=>Number.isInteger(c.index) && c.index>=0 && c.index<headers.length) && saved.columns.some(c=>c.visible)) columns=saved.columns;
    }
    const panel=document.createElement('details');panel.className='column-layout';
    const summary=document.createElement('summary');summary.textContent='Table columns';panel.append(summary);
    const list=document.createElement('div');panel.append(list);
    const status=document.createElement('small');status.setAttribute('role','status');panel.append(status);
    tools.after(panel);
    function capture() {
      if(!remember) return;
      save(key,{controls:Object.fromEntries(Object.entries(controls).map(([name,control])=>[name,control.type==='checkbox'?control.checked:control.value])),filters:Array.from(api.filters),columns},status);
    }
    function layout() {
      for(const row of [table.tHead.rows[0],...table.tBodies[0].rows]) {
        const cells=Array.from(row.cells);
        cells.forEach((cell,index)=> {if(cell.dataset.columnIndex===undefined) cell.dataset.columnIndex=index;});
        const indexed=new Map(cells.map(cell=>[Number(cell.dataset.columnIndex),cell]));
        columns.forEach(column=> {const cell=indexed.get(column.index);if(cell){cell.hidden=!column.visible;row.append(cell);}});
      }
    }
    function editor() {
      list.replaceChildren();
      columns.forEach((column,position)=> {
        const line=document.createElement('div');line.className='column-choice';
        const label=document.createElement('label'), check=document.createElement('input');check.type='checkbox';check.checked=column.visible;
        const caption=headers[column.index].querySelector('.header-filter')?.textContent.replace(/[●▾]/g,'').trim() || headers[column.index].textContent;
        check.disabled=column.visible && columns.filter(c=>c.visible).length===1;
        check.addEventListener('change',()=>{column.visible=check.checked;layout();editor();capture();});
        label.append(check,document.createTextNode(caption));line.append(label);
        for(const [delta,text] of [[-1,'Move up'],[1,'Move down']]) {
          const button=document.createElement('button');button.type='button';button.textContent=delta<0?'↑':'↓';button.setAttribute('aria-label',text+' '+caption);
          button.disabled=position+delta<0 || position+delta>=columns.length;
          button.addEventListener('click',()=>{[columns[position],columns[position+delta]]=[columns[position+delta],columns[position]];layout();editor();capture();list.children[position+delta].querySelector(delta<0?'button':'button:last-child').focus();});line.append(button);
        }
        list.append(line);
      });
    }
    const reset=document.createElement('button');reset.type='button';reset.textContent='Reset table preferences';
    reset.addEventListener('click',()=> {
      controls.search.value='';controls.mode.value='contains';controls.syntax.value='text';controls.evidence.checked=true;
      controls.size.value=document.body.dataset.reportPageSize || '25';controls.sort.value='name';controls.order.value='asc';api.filters.clear();
      columns=headers.map((_,index)=>({index,visible:true}));api.filters.notify();editor();layout();capture();
    });panel.append(reset);
    let ready=false;
    widget.addEventListener('table-render',()=>{layout();if(ready)capture();});
    api.filters.notify();editor();layout();ready=true;
  };
  document.addEventListener('DOMContentLoaded',()=> {
    if(!document.getElementById('interface-state')) return;
    if(document.body.dataset.rememberMenus==='true') {
      document.querySelectorAll('.workspace-sidebar details:not(.notification-center)').forEach(menu=> {
        const path=[];let parent=menu;
        while(parent?.tagName==='DETAILS' || parent?.closest('details')) {
          if(parent.tagName!=='DETAILS') parent=parent.closest('details');
          path.unshift(parent.querySelector(':scope > summary')?.textContent.trim());
          parent=parent.parentElement?.closest('details');
        }
        const key='menu:'+path.join('/').slice(0,150);
        if(!menu.querySelector('[aria-current]') && typeof getState()[key]==='boolean') menu.open=getState()[key];
        // Attach after restoration events have settled.
        setTimeout(()=>menu.addEventListener('toggle',()=>save(key,menu.open)),0);
      });
    }
    const center=document.querySelector('.notification-center'), list=document.getElementById('notification-list'), status=document.getElementById('notification-status');
    async function refresh() {
      if(document.hidden) return;
      try {
        const response=await fetch('/api/notifications/',{cache:'no-store'});
        if(!response.ok || response.redirected) throw new Error();
        const data=await response.json();document.getElementById('notification-count').textContent=data.unread ? String(data.unread) : '';
        center.querySelector('summary').setAttribute('aria-label',data.unread ? `Notifications, ${data.unread} unread` : 'Notifications');
        list.replaceChildren();
        data.items.forEach(item=> {
          const li=document.createElement('li'),link=document.createElement('a'),time=document.createElement('small');
          link.href=item.url;link.textContent=item.environment+' · '+item.label;time.textContent=formatTime(item.at);time.title=new Date(item.at).toISOString();
          if(item.unread) li.className='unread';li.append(link,time);list.append(li);
        });
        status.textContent=data.items.length?'Last 30 days · Up to 50 results':'No recent notifications.';
      } catch(_) {status.textContent='Notifications unavailable. Retrying shortly.';}
    }
    center.addEventListener('toggle',()=>{if(center.open)refresh();});
    document.getElementById('notifications-read').addEventListener('click',async()=>{try{await post('/api/notifications/read/',{});await refresh();}catch(error){status.textContent=error.message;}});
    refresh();setInterval(refresh,60000);
  });
})();

(() => {
  const make=(tag,text,className)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node;};
  const evidenceTabs=new Map();
  window.workspaceEvidence=(body,title,row)=> {
    const nodes=Array.from(body.children), groups=new Map([['Overview',[]],['Relationships',[]],['Technical details',[]]]);
    const summary=make('dl',null,'evidence-facts');
    if(row){
      if(!row.inventory_type && row.kind)summary.append(make('dt','Type'),make('dd',row.kind.replaceAll('_',' ')));
      for(const [key,label] of [['inventory_type','Type'],['usage','Usage'],['membership','Membership'],['hit_status','Activity'],['power_state','Power state'],['tag_count','Tags'],['group_count','Groups referencing tags'],['action','Action'],['disabled','Disabled'],['hit_count','Recorded hits'],['rule_count','Rules'],['statistics_checked_at','Counters checked'],['category','Category']]){
        if(row[key]!==undefined && row[key]!==null && row[key]!=='')summary.append(make('dt',label),make('dd',String(row[key]).replaceAll('_',' ')));
      }
      if(row.referenced_by)summary.append(make('dt','Configuration references'),make('dd',String(row.referenced_by.length)));
      if(row.membership_definition?.methods?.length)summary.append(make('dt','Membership method'),make('dd',row.membership_definition.methods.join(', ')));
      if(row.path){const identity=make('div',null,'evidence-identity');identity.append(make('code',row.path));const copy=make('button','Copy path');copy.type='button';copy.dataset.copyPath=row.path;identity.append(copy);groups.get('Technical details').push(identity);}
    }
    if(summary.children.length)groups.get('Overview').push(summary);
    for(const node of nodes){
      // Explicit sections are emitted by new reports. Keep older saved reports readable.
      const caption=node.querySelector(':scope > summary')?.textContent || '';
      const section=node.dataset.evidenceSection;
      let key=section==='relationships'?'Relationships':section==='technical'?'Technical details':section==='overview'?'Overview':/reference|assignment|firewall|via group/i.test(caption)?'Relationships':/definition|condition|inventory details/i.test(caption)?'Technical details':'Overview';
      if(node.matches('.code-block,pre'))key='Technical details';
      if(node.tagName==='DETAILS')node.open=key==='Overview';
      groups.get(key).push(node);
    }
    if(row && !groups.get('Technical details').some(node=>node.matches('details,pre,.code-block')) && row.power_state===undefined){
      const source=make('details');source.append(make('summary','Saved object data'));const content=make('div');source.append(content);
      source.addEventListener('toggle',()=>{if(source.open&&!content.children.length)content.append(window.technicalEvidence(row));});groups.get('Technical details').push(source);
    }
    // Object references remain inside the drawer; the table is never navigated or reset.
    for(const node of groups.get('Relationships'))node.querySelectorAll('code').forEach(code=>{
      const path=code.textContent.trim();if(!path.startsWith('/infra/')||path.includes(',')||code.closest('button'))return;
      const link=make('button',path,'evidence-object-link');link.type='button';link.onclick=()=>window.openRelatedEvidence(body,{path},path.split('/').pop());code.replaceWith(link);
    });
    window.prepareEvidenceDrawer?.(body);
    body.replaceChildren();const tabs=make('div',null,'evidence-tabs');tabs.setAttribute('role','tablist');body.append(tabs);
    const type=row?.inventory_type || row?.kind || (row?.power_state!==undefined?'vm':row?.action!==undefined?'rule':'object');
    let first=true;
    groups.forEach((items,label)=>{
      if(!items.length)items.push(make('p',label==='Relationships'?'No relationship details recorded for this object.':'No additional details recorded.','muted'));
      const button=make('button',label), panel=make('div',null,'evidence-panel');button.type='button';
      const id='evidence-'+label.toLowerCase().replaceAll(' ','-');panel.id=id;button.id=id+'-tab';button.setAttribute('role','tab');button.setAttribute('aria-controls',id);button.setAttribute('aria-selected',String(first));button.tabIndex=first?0:-1;
      panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);panel.tabIndex=0;panel.hidden=!first;first=false;panel.append(...items);tabs.append(button);body.append(panel);
      button.addEventListener('click',()=>{tabs.querySelectorAll('button').forEach(b=>{b.setAttribute('aria-selected',String(b===button));b.tabIndex=b===button?0:-1;});body.querySelectorAll(':scope > .evidence-panel').forEach(p=>p.hidden=p!==panel);evidenceTabs.set(type,label);if(label==='Technical details'){const first=panel.querySelector('details');if(first)first.open=true;}});
    });
    const remembered=evidenceTabs.get(type);if(remembered)Array.from(tabs.children).find(b=>b.textContent===remembered)?.click();
    window.enhanceTechnicalEvidence?.(body);
    tabs.addEventListener('keydown',event=>{const buttons=Array.from(tabs.children),index=buttons.indexOf(document.activeElement);if(index<0)return;let target;if(event.key==='ArrowRight')target=(index+1)%buttons.length;if(event.key==='ArrowLeft')target=(index+buttons.length-1)%buttons.length;if(event.key==='Home')target=0;if(event.key==='End')target=buttons.length-1;if(target!==undefined){event.preventDefault();buttons[target].click();buttons[target].focus();}});
  };
  window.prepareEvidenceDrawer = body => {
    const dialog=body.closest('dialog');if(!dialog)return;
    const heading=dialog.querySelector('.dialog-heading');
    if(!heading.querySelector('.evidence-expand')){
      const expand=make('button','Expand','evidence-expand');expand.type='button';expand.setAttribute('aria-pressed','false');
      expand.addEventListener('click',()=>{const expanded=dialog.classList.toggle('evidence-expanded');expand.textContent=expanded?'Reduce':'Expand';expand.setAttribute('aria-pressed',String(expanded));});
      heading.insertBefore(expand,heading.lastElementChild);
    }
    dialog.querySelector('.evidence-context')?.remove();
    const context=make('p',null,'evidence-context');
    const environment=document.querySelector('#environment-switcher option:checked')?.textContent;
    const snapshot=document.querySelector('#snapshot-switcher option:checked')?.textContent;
    context.textContent=[environment,snapshot].filter(Boolean).join(' · ');
    if(context.textContent)heading.after(context);
    dialog.scrollTop=0;
  };
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('form[data-filter-toolbar]').forEach(form=>{
      const chips=make('div',null,'filter-chips');chips.setAttribute('aria-label','Applied filters');form.after(chips);
      const params=new URLSearchParams(location.search);
      for(const name of ['q','kind','review','owner']){
        if(!params.get(name))continue;
        const field=form.elements.namedItem(name);if(!field)continue;
        const value=field.tagName==='SELECT'?field.selectedOptions[0]?.textContent:field.value;
        const url=new URL(location.href);url.searchParams.delete(name);url.searchParams.delete('page');
        const chip=make('a',value+' ×');chip.href=url.href;chip.setAttribute('aria-label','Remove filter: '+value);chips.append(chip);
      }
    });

    const toggle=document.querySelector('.mobile-nav-toggle'), sidebar=document.querySelector('.workspace-sidebar'),backdrop=document.querySelector('.nav-backdrop');
    const mobile=matchMedia('(max-width: 800px)');
    function menu(open){if(!sidebar)return;document.body.classList.toggle('navigation-open',open);toggle?.setAttribute('aria-expanded',String(open));backdrop.hidden=!open;sidebar.inert=mobile.matches&&!open;if(open)sidebar.querySelector('a').focus();else if(mobile.matches)toggle?.focus();}
    if(sidebar){sidebar.inert=mobile.matches;toggle?.addEventListener('click',()=>menu(!document.body.classList.contains('navigation-open')));backdrop.addEventListener('click',()=>menu(false));document.querySelector('.mobile-nav-close').addEventListener('click',()=>menu(false));mobile.addEventListener('change',()=>{menu(false);sidebar.inert=mobile.matches;});}
    document.addEventListener('keydown',event=>{if(event.key==='Escape'){if(document.body.classList.contains('navigation-open'))menu(false);document.querySelectorAll('.topbar-actions details[open],.search-options[open],.column-layout[open],.table-filter-menu[open],.filter-control[open]').forEach(details=>{details.open=false;details.querySelector('summary').focus();});}if(event.key==='Tab'&&mobile.matches&&document.body.classList.contains('navigation-open')){const focusable=Array.from(sidebar.querySelectorAll('a,button')),first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
    document.addEventListener('click',event=>{document.querySelectorAll('.topbar-actions details[open],.search-options[open],.column-layout[open],.table-filter-menu[open],.filter-control[open]').forEach(details=>{if(!event.composedPath().includes(details))details.open=false;});});
    const switcher=document.getElementById('environment-switcher');
    switcher?.addEventListener('change',()=>{
      if(!switcher.value)return;const analysisPage=location.pathname.match(/^\/environments\/\d+\/(compare|coverage|findings)(?:\/|$)/);if(analysisPage){location.href='/environments/'+encodeURIComponent(switcher.value)+'/'+analysisPage[1]+'/';return;}let section='environment';const hash=location.hash.slice(1);
      if(document.body.classList.contains('snapshot-page'))section=/polic|rule|statistics|dfw/.test(hash)?'rules':/service/.test(hash)?'services':/tag/.test(hash)?'tags':/guide|coverage/.test(hash)?'help':'inventory';
      else if(location.pathname.includes('rule-history'))section='activity';else if(location.pathname.includes('collections'))section='collections';
      location.href='/workspace/'+section+'/?environment='+encodeURIComponent(switcher.value)+(hash?'&panel='+encodeURIComponent(hash):'');
    });
    const snapshot=document.getElementById('snapshot-switcher');if(snapshot){const label=snapshot.closest('.snapshot-select'),home=label.parentElement;const place=()=>{if(mobile.matches)home.append(label);else document.getElementById('snapshot-topbar-slot').append(label);};place();mobile.addEventListener('change',place);}
    const tabs=document.querySelector('.report-section-tabs');
    const inventory=[['VMs','all-vms'],['Groups','all-groups'],['Services','all-services'],['Tags','tags-all'],['Scopes','tags-scopes']];
    const firewall=[['Overview','dfw-overview'],['Policies','dfw-policies'],['Rules','dfw-rules']];
    const filters={
      groups:[['All groups','all-groups'],['Unused candidates','unused-groups'],['Empty groups','empty-groups'],['Incomplete membership checks','unknown-membership']],
      services:[['All services','all-services'],['Unused custom services','unused-services']],
      tags:[['All tags','tags-all'],['VMs and groups','tags-both'],['VM use','tags-vm_only'],['Group use','tags-group_only'],['Other resource use','tags-other_only'],['Needs review','tags-unknown']],
      policies:[['All policies','dfw-policies'],['Empty policies','empty-policies']],
      rules:[['All rules','dfw-rules'],['Zero recorded hits','zero-hit-rules'],['Disabled rules','disabled-rules'],['Incomplete statistics','unknown-statistics'],['Empty group references','empty-group-rules'],['Applied to DFW','dfw-scope-rules']]
    };
    function reportNavigation(){
      if(!tabs)return;const id=location.hash.slice(1)||'overview';let kind=Object.keys(filters).find(key=>filters[key].some(([,anchor])=>anchor===id));
      const isFirewall=['rules','policies'].includes(kind)||id==='dfw-overview',isHelp=['feature-guide','coverage','tags-coverage'].includes(id);
      const items=isHelp?[['User guide','feature-guide'],['Audit coverage','coverage'],['Tag coverage','tags-coverage']]:isFirewall?firewall:[['Snapshot overview','overview'],...inventory];
      tabs.replaceChildren();for(const [label,anchor] of items){const link=make('a',label);link.href='#'+anchor;if(id===anchor || (kind && filters[kind]?.some(([,value])=>value===id) && filters[kind][0][1]===anchor))link.setAttribute('aria-current','page');tabs.append(link);}
      if(isFirewall){const link=make('a','Historical activity');link.href='/environments/'+document.body.dataset.tableScope+'/rule-history/';tabs.append(link);}
      document.querySelectorAll('[data-primary]').forEach(link=>{if(link.dataset.primary===(isFirewall?'firewall':isHelp?'help':'inventory'))link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
      const selected=document.getElementById(id),heading=selected?.querySelector(':scope > h2');
      const pageTitle=document.querySelector('.report-toolbar h1');
      if(pageTitle){const copy=heading?.cloneNode(true),count=copy?.querySelector('.count');count?.remove();pageTitle.textContent=copy?.textContent.trim() || (id==='overview'?'Snapshot overview':'Inventory');if(count){pageTitle.append(document.createTextNode(' '),count);}if(heading)heading.hidden=true;}
      const slot=document.querySelector('.report-view-selector');tabs.after(slot);slot.replaceChildren();if(kind){const label=make('label','View'),select=make('select');select.setAttribute('aria-label','Filter '+kind);for(const [text,value] of filters[kind]){const option=new Option(text,value);option.selected=value===id;select.add(option);}select.addEventListener('change',()=>location.hash=select.value);label.append(select);slot.append(label);const tools=selected?.querySelector('.table-tools');if(tools)tools.append(slot);}
    }
    reportNavigation();window.addEventListener('hashchange',reportNavigation);
    const performance=document.querySelector('.report-content > p.muted');
    if(performance){const info=make('details',null,'snapshot-information');info.append(make('summary','Collection details'),performance);document.querySelector('.report-toolbar')?.after(info);}
    document.querySelectorAll('#dfw-rules > p.muted,#zero-hit-rules > p.muted').forEach(note=>{const details=make('details',null,'snapshot-information');note.before(details);details.append(make('summary','How to interpret rule counters'),note);});

    function rememberMenu(details,key){
      if(document.body.dataset.rememberMenus!=='true')return;
      const saved=JSON.parse(document.getElementById('interface-state')?.textContent||'{}');
      if(typeof saved[key]==='boolean')details.open=saved[key];
      setTimeout(()=>details.addEventListener('toggle',()=>fetch('/api/preferences/interface/',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('#interface-csrf input').value},body:JSON.stringify({key,value:details.open})}).catch(()=>{})),0);
    }
    const narrow=matchMedia('(max-width:600px)');
    function streamline(widget){
      if(widget.dataset.streamlined || widget.querySelector('.table-tools')?.hidden)return;widget.dataset.streamlined='true';
      const tools=widget.querySelector('.table-tools'),advanced=make('details',null,'search-options'),summary=make('summary','Search options');advanced.append(summary);const advancedBody=make('div',null,'search-options-body');advanced.append(advancedBody);
      ['.search-mode','.search-syntax','.search-evidence'].forEach(selector=>{const control=tools.querySelector(selector);if(control)advancedBody.append(control.closest('label'));});tools.append(advanced);
      const sorting=make('details',null,'filter-control table-sort');sorting.append(make('summary','Sort'));const sortFields=make('div',null,'filter-fields');sorting.append(sortFields);
      ['.sort-key','.sort-order'].forEach(selector=>{const label=tools.querySelector(selector)?.closest('label');if(label){label.hidden=false;sortFields.append(label);}});tools.append(sorting);
      const footer=make('div',null,'table-pagination');['.page-status','.page-size','.previous','.next'].forEach(selector=>{const control=tools.querySelector(selector);if(control)footer.append(selector==='.page-size'?control.closest('label'):control);});widget.append(footer);
      const columns=widget.querySelector('.column-layout');if(columns){columns.querySelector('summary').textContent='Columns';const list=columns.querySelector(':scope > div'),body=make('div',null,'column-popup');list.before(body);body.append(list);columns.querySelectorAll(':scope > small,:scope > button').forEach(node=>body.append(node));tools.append(columns);}
      const filter=make('details',null,'table-filter-menu');filter.append(make('summary','Filters'));
      const choices=make('div',null,'filter-picker');widget.querySelectorAll('.header-filter').forEach(header=>{const button=make('button',header.textContent.replace(/[●▾]/g,'').trim());button.type='button';button.addEventListener('click',()=>{filter.open=false;header.click();});choices.append(button);});filter.append(choices);tools.append(filter);
      const menuPrefix='menu:'+document.body.dataset.tableScope+':'+widget.closest('[data-panel]').id;rememberMenu(advanced,menuPrefix+':search');if(columns)rememberMenu(columns,menuPrefix+':columns');
      const exportButton=Array.from(tools.querySelectorAll(':scope > button')).find(button=>button.textContent==='Export CSV');if(exportButton){exportButton.textContent='Export';exportButton.setAttribute('aria-label','Export CSV');tools.append(exportButton);}
      const mobileOptions=make('details',null,'table-options'),mobileBody=make('div',null,'table-options-body');
      mobileOptions.append(make('summary','Table options'),mobileBody);tools.after(mobileOptions);
      const actions=[filter,sorting,columns,advanced,exportButton].filter(Boolean);
      const placeOptions=()=>{actions.forEach(control=>(narrow.matches?mobileBody:tools).append(control));if(!narrow.matches)mobileOptions.open=false;};
      narrow.addEventListener('change',placeOptions);placeOptions();
    }
    function enhanceRows(widget){widget.querySelectorAll('.previous,.next').forEach(button=>button.title=button.disabled?(button.classList.contains('previous')?'You are on the first page.':'There are no more results.'):'');widget.querySelectorAll('code.path').forEach(path=>path.title=path.textContent);widget.querySelectorAll('tbody tr').forEach(row=>{const cell=row.querySelector('td[data-column-index="0"]')||row.cells[0],trigger=row.querySelector('.detail-button');if(!cell||!trigger||cell.querySelector('.row-open'))return;const name=cell.querySelector('strong')||cell.firstChild;if(!name)return;const button=make('button',name.textContent,'row-open');button.type='button';button.setAttribute('aria-label','Inspect '+name.textContent);button.addEventListener('click',()=>trigger.click());name.replaceWith(button);});}
    function bindReportTables(){document.querySelectorAll('.table-widget').forEach(widget=>{if(widget.dataset.boundReport)return;widget.dataset.boundReport='true';widget.addEventListener('table-render',()=>{streamline(widget);enhanceRows(widget);});streamline(widget);enhanceRows(widget);});}
    bindReportTables();
    document.addEventListener('report-section-loaded',()=>{reportNavigation();bindReportTables();});
    document.querySelectorAll('[data-readable-evidence]').forEach(raw=>{
      let data;try{data=JSON.parse(raw.textContent);}catch{return;}
      if(!data || Array.isArray(data) || typeof data!=='object')return;
      const overview=make('dl',null,'evidence-facts'), structured=make('div',null,'structured-evidence');
      for(const [key,value] of Object.entries(data)){
        const label=key.replaceAll('_',' ');
        if(value!==null && typeof value==='object'){
          const section=make('details');section.append(make('summary',label),make('pre',JSON.stringify(value,null,2)));structured.append(section);
        }else overview.append(make('dt',label),make('dd',value===null?'Not recorded':String(value)));
      }
      const source=make('details');source.append(make('summary','Raw evidence'));raw.before(overview,structured,source);source.append(raw);
    });
    const theme=document.querySelector('[data-settings-form] select[name=theme]');
    if(theme){const choices=make('div',null,'theme-choices');choices.setAttribute('role','group');choices.setAttribute('aria-label','Color theme');
      for(const option of theme.options){const button=make('button',option.textContent);button.type='button';button.setAttribute('aria-pressed',String(theme.value===option.value));button.addEventListener('click',()=>{theme.value=option.value;theme.dispatchEvent(new Event('change',{bubbles:true}));});choices.append(button);}
      theme.after(choices);theme.hidden=true;theme.addEventListener('change',()=>Array.from(choices.children).forEach((button,index)=>button.setAttribute('aria-pressed',String(theme.value===theme.options[index].value))));
    }
    document.querySelectorAll('form[method=post]:not(#interface-csrf)').forEach(form=>{
      form.addEventListener('submit',event=>{if(event.defaultPrevented)return;if(form.dataset.submitting){event.preventDefault();return;}form.dataset.submitting='true';
        const button=event.submitter;if(button){button.dataset.originalLabel=button.textContent;button.textContent=/collect|sync/i.test(button.textContent)?'Queuing collection…':/sign in/i.test(button.textContent)?'Signing in…':/save/i.test(button.textContent)?'Saving…':'Working…';button.setAttribute('aria-disabled','true');button.setAttribute('aria-busy','true');}
      });
    });
    window.addEventListener('pageshow',()=>{document.querySelectorAll('form[data-submitting]').forEach(form=>delete form.dataset.submitting);document.querySelectorAll('[data-original-label]').forEach(button=>{button.textContent=button.dataset.originalLabel;button.removeAttribute('aria-disabled');button.removeAttribute('aria-busy');});});
    const form=document.querySelector('[data-settings-form]');
    if(form){let dirty=false;const status=form.querySelector('.unsaved-status');form.addEventListener('change',()=>{dirty=true;status.textContent='Unsaved changes';
      for(const name of ['theme','timezone','date_format']){const control=form.elements.namedItem(name);if(control)document.body.dataset[name==='date_format'?'dateFormat':name]=control.value;}
      for(const [name,prefix] of [['density','display-'],['text_size','text-']]){const control=form.elements.namedItem(name);if(control){Array.from(document.body.classList).filter(c=>c.startsWith(prefix)).forEach(c=>document.body.classList.remove(c));document.body.classList.add(prefix+control.value);}}
      for(const [name,cls] of [['high_contrast','high-contrast'],['reduced_motion','reduced-motion']]){const control=form.elements.namedItem(name);if(control)document.body.classList.toggle(cls,control.checked);}
    });form.addEventListener('submit',()=>dirty=false);window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});}
  });
})();

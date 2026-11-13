(() => {
  const make=(tag,text,className)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node;};
  const evidenceTabs=new Map();
  window.workspaceEvidence=(body,title,row)=> {
    body.closest('dialog')?.classList.remove('evidence-firewall-wide');
    window.objectReferenceTables?.(body,row);
    window.objectMembershipTables?.(body,row);
    window.referenceListTables?.(body);
    const nodes=Array.from(body.children), groups=new Map([['Summary',[]],['Members',[]],['References',[]],['History',[]],['Technical details',[]]]);
    const summary=make('dl',null,'evidence-facts');
    if(row){
      if(!row.inventory_type && row.kind)summary.append(make('dt','Type'),make('dd',row.kind.replaceAll('_',' ')));
      for(const [key,label] of [['inventory_type','Type'],['scope','Scope'],['status','Assessment'],['other_count','Other resources'],['tagged_objects','NSX catalog assignments'],['usage','Usage'],['membership','Membership'],['hit_status','Activity'],['power_state','Power state'],['tag_count','Tags'],['group_count','Groups referencing tags'],['action','Action'],['disabled','Disabled'],['hit_count','Recorded hits'],['rule_count','Rules'],['category','Category']]){
        if(key==='group_count'&&row.inventory_type==='Virtual machine')continue;
        if(row[key]!==undefined && row[key]!==null && row[key]!=='')summary.append(make('dt',label),make('dd',String(row[key]).replaceAll('_',' ')));
      }
      if(row.referenced_by)summary.append(make('dt','Configuration references'),make('dd',String(row.referenced_by.length)));
      if(row.resolved_membership_status){
        const state=row.resolved_membership_status.replaceAll('_',' '),count=row.resolved_group_count;
        summary.append(make('dt','Saved VM groups'),make('dd',count==null?state:(row.resolved_membership_status==='partial'?count+' confirmed; total unknown':count)),make('dt','Saved membership coverage'),make('dd',state));
      }
      if(row.membership_definition?.methods?.length)groups.get('Technical details').push(make('p','Membership method: '+row.membership_definition.methods.join(', ')));
      if(row.path){const identity=make('div',null,'evidence-identity');identity.append(make('code',row.path));const copy=make('button','Copy path');copy.type='button';copy.dataset.copyPath=row.path;identity.append(copy);groups.get('Technical details').push(identity);}
    }
    if(row?.statistics_checked_at){const timing=make('p','Counters checked: '),time=make('time');time.dateTime=row.statistics_checked_at;time.textContent=new Date(row.statistics_checked_at).toString();if(window.workspaceTime){const template=document.createElement('template');template.innerHTML=window.workspaceTime(row.statistics_checked_at);timing.append(template.content);}else timing.append(time);groups.get('Technical details').push(timing);}
    const impactBase=document.querySelector('[data-impact-base]')?.dataset.impactBase;
    if(impactBase && row?.path && (row.kind==='group' || row.inventory_type==='Group' || row.policy_path || row.rule_count!==undefined)){
      const link=make('a','Impact preview','button');link.href=impactBase+'?'+new URLSearchParams({path:row.path});groups.get('Summary').push(link);
    }
    if(impactBase && (row?.inventory_type==='Virtual machine' || row?.inventory_type==='Tag')){
      const params=row.inventory_type==='Tag'?{action:'remove-tag',scope:row.scope||'',tag:row.name}:{action:'delete-vm',path:row.path};
      const link=make('a','Impact preview','button');link.href=impactBase+'?'+new URLSearchParams(params);groups.get('Summary').push(link);
    }
    if(summary.children.length)groups.get('Summary').push(summary);
    for(const node of nodes){
      // Explicit sections are emitted by new reports. Keep older saved reports readable.
      const caption=node.querySelector(':scope > summary')?.textContent || '';
      const section=node.dataset.evidenceSection;
      let key=section==='relationships'?'References':section==='technical'?'Technical details':section==='overview'?'Summary':/reference|assignment|firewall|via group/i.test(caption)?'References':/definition|condition|inventory details/i.test(caption)?'Technical details':'Summary';
      if(section==='members'||/^(resolved |explicit |group )?members\b/i.test(caption))key='Members';
      if(section==='history'||/observation history|review history|changes since/i.test(caption))key='History';
      if(node.matches('.code-block,pre'))key='Technical details';
      if(node.tagName==='DETAILS')node.open=key==='Summary'&&node.dataset.evidenceLazy!== 'true';
      groups.get(key).push(node);
    }
    if(row && !row.evidence_summary_only && !groups.get('Technical details').some(node=>node.matches('details,pre,.code-block')) && row.power_state===undefined){
      const source=make('details');source.append(make('summary','Saved object data'));const content=make('div');source.append(content);
      source.addEventListener('toggle',()=>{if(source.open&&!content.children.length)content.append(window.technicalEvidence(row));});groups.get('Technical details').push(source);
    }
    // Object references remain inside the drawer; the table is never navigated or reset.
    for(const node of groups.get('References'))node.querySelectorAll('code').forEach(code=>{
      const path=code.textContent.trim();if(!path.startsWith('/infra/')||path.includes(',')||code.closest('button'))return;
      const link=make('button',path,'evidence-object-link');link.type='button';link.onclick=()=>window.openRelatedEvidence(body,{path},path.split('/').pop());code.replaceWith(link);
    });
    window.prepareEvidenceDrawer?.(body);
    body.replaceChildren();const tabs=make('div',null,'evidence-tabs');tabs.setAttribute('role','tablist');body.append(tabs);
    const type=row?.inventory_type || row?.kind || (row?.power_state!==undefined?'vm':row?.action!==undefined?'rule':'object');
    let first=true;
    groups.forEach((items,label)=>{
      if(!items.length&&label==='Members'&&!(row?.kind==='group'||row?.inventory_type==='Group'))return;
      if(!items.length&&label==='History')items.push(make('p','This snapshot contains point-in-time evidence. Open Reviews for decision and observation history.','muted'));
      if(!items.length)items.push(make('p',label==='References'?'No relationship details recorded for this object.':'No additional details recorded.','muted'));
      const button=make('button',label), panel=make('div',null,'evidence-panel');button.type='button';
      const id='evidence-'+label.toLowerCase().replaceAll(' ','-');panel.id=id;button.id=id+'-tab';button.setAttribute('role','tab');button.setAttribute('aria-controls',id);button.setAttribute('aria-selected',String(first));button.tabIndex=first?0:-1;
      panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);panel.tabIndex=0;panel.hidden=!first;first=false;panel.append(...items);tabs.append(button);body.append(panel);
      button.addEventListener('click',()=>{tabs.querySelectorAll('button').forEach(b=>{b.setAttribute('aria-selected',String(b===button));b.tabIndex=b===button?0:-1;});body.querySelectorAll(':scope > .evidence-panel').forEach(p=>p.hidden=p!==panel);evidenceTabs.set(type,label);body.closest('dialog')?.classList.remove('evidence-firewall-wide');panel.querySelectorAll('[data-evidence-activate]').forEach(node=>node.dispatchEvent(new Event('evidence-visible')));if(label==='Technical details'){const first=panel.querySelector('details');if(first)first.open=true;}});
    });
    const remembered=evidenceTabs.get(type);if(remembered)Array.from(tabs.children).find(b=>b.textContent===remembered)?.click();
    window.enhanceTechnicalEvidence?.(body);
    tabs.addEventListener('keydown',event=>{const buttons=Array.from(tabs.children),index=buttons.indexOf(document.activeElement);if(index<0)return;let target;if(event.key==='ArrowRight')target=(index+1)%buttons.length;if(event.key==='ArrowLeft')target=(index+buttons.length-1)%buttons.length;if(event.key==='Home')target=0;if(event.key==='End')target=buttons.length-1;if(target!==undefined){event.preventDefault();buttons[target].click();buttons[target].focus();}});
  };
  window.prepareEvidenceDrawer = body => {
    const dialog=body.closest('dialog');if(!dialog)return;
    if(!dialog.dataset.firewallWidthBound){dialog.dataset.firewallWidthBound='true';dialog.addEventListener('close',()=>dialog.classList.remove('evidence-firewall-wide'));}
    const heading=dialog.querySelector('.dialog-heading');
    if(!heading.querySelector('.evidence-expand')){
      const expand=make('button','Expand','evidence-expand');expand.type='button';expand.setAttribute('aria-pressed','false');
      expand.addEventListener('click',()=>{const expanded=dialog.classList.toggle('evidence-expanded');expand.textContent=expanded?'Reduce':'Expand';expand.setAttribute('aria-pressed',String(expanded));});
      heading.insertBefore(expand,heading.lastElementChild);
    }
    dialog.querySelector('.evidence-context')?.remove();
    const context=make('p',null,'evidence-context');
    const environment=document.querySelector('#environment-switcher option:checked')?.textContent || document.querySelector('.environment-fixed a')?.textContent;
    const snapshotControl=document.querySelector('[data-snapshot-open]') || document.querySelector('.snapshot-context');
    const snapshot=snapshotControl?.getAttribute('aria-label') || snapshotControl?.textContent;
    context.textContent=[environment,snapshot].filter(Boolean).join(' · ');
    if(context.textContent)heading.after(context);
    dialog.scrollTop=0;
  };
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('form[data-filter-toolbar]').forEach(form=>{
      const chips=make('div',null,'filter-chips');chips.setAttribute('role','group');chips.setAttribute('aria-label','Applied filters');form.after(chips);
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
    function menu(open){if(!sidebar)return;document.body.classList.toggle('navigation-open',open);toggle?.setAttribute('aria-expanded',String(open));backdrop.hidden=!open;sidebar.inert=mobile.matches&&!open;if(open)sidebar.querySelector('a').focus();else if(mobile.matches)toggle?.focus();if(open&&mobile.matches){sidebar.setAttribute('role','dialog');sidebar.setAttribute('aria-modal','true');}else{sidebar.removeAttribute('role');sidebar.removeAttribute('aria-modal');}}
    if(sidebar){sidebar.inert=mobile.matches;toggle?.addEventListener('click',()=>menu(!document.body.classList.contains('navigation-open')));backdrop.addEventListener('click',()=>menu(false));document.querySelector('.mobile-nav-close').addEventListener('click',()=>menu(false));mobile.addEventListener('change',()=>{menu(false);sidebar.inert=mobile.matches;});}
    document.addEventListener('keydown',event=>{if(event.key==='Escape'){if(document.body.classList.contains('navigation-open'))menu(false);document.querySelectorAll('.topbar-actions details[open],.search-options[open],.column-layout[open],.table-filter-menu[open],.filter-control[open]').forEach(details=>{details.open=false;details.querySelector('summary').focus();});}if(event.key==='Tab'&&mobile.matches&&document.body.classList.contains('navigation-open')){const focusable=Array.from(sidebar.querySelectorAll('a,button')).filter(node=>node.getClientRects().length&&!node.disabled),first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
    document.addEventListener('click',event=>{document.querySelectorAll('.topbar-actions details[open],.search-options[open],.column-layout[open],.table-filter-menu[open],.filter-control[open]').forEach(details=>{if(!event.composedPath().includes(details))details.open=false;});});
    const tabs=document.querySelector('.report-section-tabs');
    const inventory=[['VMs','all-vms'],['Groups','all-groups'],['Services','all-services'],['Tags','tags-all'],['Scopes','tags-scopes']];
    const firewall=[['Summary','dfw-overview'],['Policies','dfw-policies'],['Rules','dfw-rules']];
    const filters={
      groups:[['All groups','all-groups'],['Unused groups','unused-groups'],['Empty groups','empty-groups'],['Membership unknown','unknown-membership']],
      services:[['All services','all-services'],['Unused services','unused-services']],
      tags:[['All tags','tags-all'],['VMs and groups','tags-both'],['VM use','tags-vm_only'],['Group use','tags-group_only'],['Other resource use','tags-other_only'],['Needs review','tags-unknown']],
      policies:[['All policies','dfw-policies'],['Empty policies','empty-policies']],
      rules:[['All rules','dfw-rules'],['Zero recorded hits','zero-hit-rules'],['Disabled rules','disabled-rules'],['Incomplete statistics','unknown-statistics'],['Empty group references','empty-group-rules'],['Applied to DFW','dfw-scope-rules']]
    };
    function reportNavigation(){
      if(!tabs)return;const id=location.hash.slice(1)||'overview';let kind=Object.keys(filters).find(key=>filters[key].some(([,anchor])=>anchor===id));
      const isFirewall=['rules','policies'].includes(kind)||id==='dfw-overview',isHelp=['feature-guide','coverage','tags-coverage'].includes(id);
      const items=isHelp?[['User guide','feature-guide'],['Coverage & definitions','coverage'],['Tag coverage','tags-coverage']]:isFirewall?firewall:[['Overview','overview'],...inventory];
      tabs.replaceChildren();for(const [label,anchor] of items){const link=make('a',label);link.href='#'+anchor;if(id===anchor || (kind && filters[kind]?.some(([,value])=>value===id) && filters[kind][0][1]===anchor))link.setAttribute('aria-current','page');tabs.append(link);}
      if(isFirewall){const link=make('a','Historical activity');link.href='/environments/'+document.body.dataset.tableScope+'/rule-history/';tabs.append(link);}
      document.querySelectorAll('[data-primary]').forEach(link=>{if(link.dataset.primary===(isFirewall?'firewall':isHelp?'help':'inventory'))link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
      const selected=document.getElementById(id),heading=selected?.querySelector(':scope > h2');
      const pageTitle=document.querySelector('.report-toolbar h1');
      if(pageTitle){const copy=heading?.cloneNode(true),count=copy?.querySelector('.count');count?.remove();pageTitle.textContent=id==='overview'?'Inventory overview':copy?.textContent.trim() || 'Inventory';if(count){pageTitle.append(document.createTextNode(' '),count);}if(heading)heading.hidden=true;}
      const slot=document.querySelector('.report-view-selector');tabs.after(slot);slot.replaceChildren();if(kind){const label=make('label','View'),select=make('select');select.setAttribute('aria-label','Filter '+kind);for(const [text,value] of filters[kind]){const option=new Option(text,value);option.selected=value===id;select.add(option);}select.addEventListener('change',()=>location.hash=select.value);label.append(select);slot.append(label);const tools=selected?.querySelector('.table-tools');if(tools)tools.append(slot);}
      document.dispatchEvent(new CustomEvent('report-navigation-ready'));
    }
    reportNavigation();window.addEventListener('hashchange',reportNavigation);
    const collectionTemplate=document.getElementById('collection-details-template');
    if(collectionTemplate){
      const metadata=collectionTemplate.content.firstElementChild.cloneNode(true);
      const diagnostics=metadata.querySelector('[data-collection-diagnostics]');
      document.querySelectorAll('.report-content > .report-performance,.report-content > .detail-popup,#overview > .overview > .detail-popup').forEach(source=>{
        const label=source.querySelector('summary,.detail-button')?.textContent;
        const title=source.querySelector('.detail-button')?.dataset.title || label || 'Run diagnostics';
        const section=make('details');section.append(make('summary',title));
        const content=source.querySelector('.detail-content');if(content)section.append(...content.childNodes);else section.append(...[...source.childNodes].filter(n=>n.tagName!=='SUMMARY'));
        diagnostics.append(section);source.remove();
      });
      document.querySelector('[data-collection-action]').append(metadata);
      metadata.querySelector('[data-collection-section]')?.addEventListener('click',()=>document.querySelector('dialog.workspace-panel[open]')?.close());
    }
    document.querySelectorAll('.report-content > p.muted').forEach(node=>{
      if(/^(Fresh retrieval:|Saved audit retrieval:|Previously imported snapshot:|TESTING SAMPLE)/.test(node.textContent.trim()))node.remove();
    });
    // Adapt retained report markup once. Counts and evidence remain the report's own values.
    const overview=document.querySelector('.report-content #overview'),charts=overview?.querySelector('.overview-charts'),cards=overview?.querySelector('nav.cards');
    if(charts&&cards){
      charts.querySelectorAll('.snapshot-chart').forEach(chart=>{
        const donut=chart.querySelector('.donut-chart');if(!donut)return;
        const bar=make('div',null,'usage-share');bar.setAttribute('role','img');bar.setAttribute('aria-label',donut.getAttribute('aria-label')||chart.querySelector('h3').textContent);
        chart.querySelectorAll('.chart-legend a').forEach(link=>{const percent=parseFloat(link.querySelector('small')?.textContent)||0,label=link.querySelectorAll('span')[1]?.textContent||'',segment=make('span');segment.style.width=percent+'%';segment.className=/unused|zero/i.test(label)?'usage-candidate':/unknown/i.test(label)?'usage-unknown':/not assessed|disabled|not supported/i.test(label)?'usage-unassessed':'usage-referenced';const key=link.querySelector('.chart-key');if(key)key.style.background=segment.className==='usage-referenced'?'var(--app-accent)':segment.className==='usage-candidate'?'#B56B12':segment.className==='usage-unknown'?'var(--muted)':'#8798A6';bar.append(segment);});
        donut.replaceWith(bar);chart.classList.add('usage-summary');
      });
      const ranked=[...cards.querySelectorAll(':scope > a')].sort((a,b)=>Number(b.querySelector('b')?.textContent.replaceAll(',','')||0)-Number(a.querySelector('b')?.textContent.replaceAll(',','')||0));
      const other=make('details',null,'overview-other'),otherLinks=make('div',null,'overview-other-links');other.append(make('summary','Other finding categories'),otherLinks);
      ranked.forEach(card=>{const number=Number(card.querySelector('b')?.textContent.replaceAll(',','')||0);card.querySelector('small')?.remove();card.setAttribute('aria-label','Review '+card.querySelector('span')?.textContent+', '+number+' objects');if(number)cards.append(card);else otherLinks.append(card);});
      const list=make('section',null,'overview-findings-list');list.append(make('h2','Findings to review'),cards);if(otherLinks.children.length)list.append(other);list.append(make('p','Categories can overlap. Unknown or unassessed checks are not empty.','footnote'));
      overview.querySelectorAll(':scope > h2').forEach(h=>{if(h.textContent.trim()==='Findings & review')h.remove();});const unique=overview.querySelector(':scope > .overview > p');if(unique&&/unique object/.test(unique.textContent)){const owner=unique.parentElement;unique.classList.add('footnote');list.append(unique);if(!owner.children.length)owner.remove();}
      const composition=make('div',null,'overview-composition');charts.before(composition);composition.append(charts,list);
      const note=overview.querySelector('.chart-note');if(note)composition.after(note);
      overview.querySelector('.inventory-shortcuts')?.classList.add('compact-inventory-totals');
    }
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
      const more=make('details',null,'filter-control table-more'), moreBody=make('div',null,'table-more-body');
      more.append(make('summary','More options'),moreBody);tools.append(more);
      [columns,advanced,exportButton].filter(Boolean).forEach(control=>moreBody.append(control));

    }
    function enhanceRows(widget){
      widget.querySelectorAll('.header-filter').forEach(b=>{const text=b.textContent.replace(/ ▾$/, '');if(b.textContent!==text)b.textContent=text;});
      widget.querySelectorAll('.previous,.next').forEach(button=>button.title=button.disabled?(button.classList.contains('previous')?'You are on the first page.':'There are no more results.'):'');
      widget.querySelectorAll('code.path').forEach(path=>path.title=path.textContent);
      const vm=widget.closest('[data-panel]')?.id==='all-vms',names=new Map();widget.querySelectorAll('tbody tr').forEach(row=>{const name=row.querySelector('.row-open,strong')?.textContent;if(name)names.set(name,(names.get(name)||0)+1);});
      widget.querySelectorAll('tbody tr').forEach(row=>{
        const cell=row.querySelector('td[data-column-index="0"]')||row.cells[0],trigger=row.querySelector('.detail-button');if(!cell||!trigger)return;
        if(!cell.querySelector('.row-open')){const name=cell.querySelector('strong')||cell.firstChild;if(name){const button=make('button',name.textContent,'row-open');button.type='button';button.setAttribute('aria-label','Inspect '+name.textContent);button.addEventListener('click',()=>trigger.click());name.replaceWith(button);}}
        // Retained reports emitted qualification paragraphs before the name.
        // Keep exact evidence, but group it under Usage/Status across old and new snapshots.
        if(widget.matches('.view-inventory,.view-dfw')){
          const status=row.querySelector('td[data-column-index="2"]')||row.cells[2];
          if(status)cell.querySelectorAll(':scope > p').forEach(p=>{
            if(p.querySelector('.badge') && p.querySelector('small') && /observations/.test(p.textContent)){
              p.classList.add('row-qualification');status.append(p);
            }
          });
        }
        const nameButton=cell.querySelector('.row-open');if(nameButton)cell.prepend(nameButton);
        trigger.textContent='Inspect';trigger.classList.add('row-inspect');trigger.setAttribute('aria-label','Inspect '+(cell.querySelector('.row-open')?.textContent||'object'));
        if(vm){const identity=cell.querySelector('code.path');if(identity){identity.classList.toggle('row-identity',(names.get(cell.querySelector('.row-open')?.textContent)||0)<2);cell.querySelector('.row-open')?.setAttribute('title',identity.textContent);}
          [...row.cells].forEach(td=>{const labels={VM_RUNNING:'Running',VM_STOPPED:'Powered off',VM_SUSPENDED:'Suspended',VM_UNKNOWN:'Unknown'};if(labels[td.textContent.trim()]){td.dataset.rawValue=td.textContent.trim();td.textContent=labels[td.dataset.rawValue];}});
        }
      });
    }
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
    if(form){form.addEventListener('change',()=>{
      for(const name of ['theme','timezone','date_format']){const control=form.elements.namedItem(name);if(control)document.body.dataset[name==='date_format'?'dateFormat':name]=control.value;}
      for(const [name,prefix] of [['density','display-'],['text_size','text-']]){const control=form.elements.namedItem(name);if(control){Array.from(document.body.classList).filter(c=>c.startsWith(prefix)).forEach(c=>document.body.classList.remove(c));document.body.classList.add(prefix+control.value);}}
      for(const [name,cls] of [['high_contrast','high-contrast'],['reduced_motion','reduced-motion']]){const control=form.elements.namedItem(name);if(control)document.body.classList.toggle(cls,control.checked);}
    });}
  });
})();

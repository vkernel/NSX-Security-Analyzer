(() => {
  const make=(tag,text,className)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node;};
  window.workspaceEvidence=(body,title,row)=> {
    const nodes=Array.from(body.children), groups=new Map([['Summary',[]],['References',[]],['Definition',[]],['Statistics',[]],['Advanced',[]]]);
    const summary=make('div',null,'evidence-object');
    if(row){summary.append(make('strong',row.name));if(row.path){const path=make('code',row.path);summary.append(path);const copy=make('button','Copy path');copy.type='button';copy.dataset.copyPath=row.path;summary.append(copy);}}
    if(row){for(const [key,label] of [['inventory_type','Type'],['usage','Usage'],['membership','Membership'],['hit_status','Activity']]){if(row[key])summary.append(make('p',label+': '+String(row[key]).replaceAll('_',' ')));}}
    groups.get('Summary').push(summary);
    if(row?.policy_rule_id!==undefined){const definition=make('div');for(const [key,label] of [['source_groups','Sources'],['destination_groups','Destinations'],['services','Services'],['scope','Applied to']]){definition.append(make('h3',label),make('p',(row[key]||[]).join(', ')||'Not specified'));}groups.get('Definition').push(definition);}

    for(const node of nodes){
      const caption=node.querySelector(':scope > summary')?.textContent || node.textContent.slice(0,100);
      let key=/counter|statistics|last observed|last positive/i.test(caption)?'Statistics':/reference|assignment|firewall|via group/i.test(caption)?'References':/definition|condition|membership/i.test(caption)?'Definition':'Summary';
      if(row?.policy_rule_id!==undefined && key==='Statistics')node.querySelectorAll('p').forEach(p=>{if(['Sources','Destinations','Services','Applied to'].includes(p.querySelector('strong')?.textContent))p.remove();});
      const blocks=Array.from(node.querySelectorAll('.code-block'));
      if(node.classList.contains('code-block')){groups.get('Advanced').push(node);continue;}
      blocks.forEach(block=>{const wrapper=make('div');wrapper.append(make('h3',caption.slice(0,120)),block);groups.get('Advanced').push(wrapper);});
      if(node.tagName==='DETAILS') node.open=true;
      groups.get(key).push(node);
    }
    if(row?.policy_rule_id!==undefined){
      const stats=make('div'), values=make('dl',null,'statistic-list');
      const item=(label,value)=>values.append(make('dt',label),make('dd',String(value ?? 'Unavailable')));
      item('Checked',row.statistics_checked_at);
      (row.statistics||[]).forEach(sample=>{item('Enforcement point',sample.enforcement_point);for(const key of ['hit_count','packet_count','byte_count','session_count'])if(sample[key]!=null)item(key.replaceAll('_',' '),new Intl.NumberFormat().format(sample[key]));});
      item('Last positive observation',row.last_positive_observation ? row.last_positive_observation.observed_at+' · '+row.last_positive_observation.hit_count+' hits' : 'No saved positive snapshot');
      stats.append(values,make('p','Saved counter observations do not establish the time of the last packet. Traffic and resets between checks can be missed.','muted'));
      if(row.notes?.length){const notes=make('ul',null,'notes');row.notes.forEach(note=>notes.append(make('li',note)));stats.append(notes);}
      groups.set('Statistics',[stats]);
    }
    body.replaceChildren();const tabs=make('div',null,'evidence-tabs');tabs.setAttribute('role','tablist');body.append(tabs);
    let first=true;
    groups.forEach((items,label)=>{
      if(!items.length)return;
      const button=make('button',label), panel=make('div',null,'evidence-panel');button.type='button';
      const id='evidence-'+label.toLowerCase();panel.id=id;button.id=id+'-tab';button.setAttribute('role','tab');button.setAttribute('aria-controls',id);button.setAttribute('aria-selected',String(first));button.tabIndex=first?0:-1;
      panel.setAttribute('role','tabpanel');panel.setAttribute('aria-labelledby',button.id);panel.tabIndex=0;panel.hidden=!first;first=false;panel.append(...items);tabs.append(button);body.append(panel);
      button.addEventListener('click',()=>{tabs.querySelectorAll('button').forEach(b=>{b.setAttribute('aria-selected',String(b===button));b.tabIndex=b===button?0:-1;});body.querySelectorAll(':scope > .evidence-panel').forEach(p=>p.hidden=p!==panel);});
    });
    tabs.addEventListener('keydown',event=>{const buttons=Array.from(tabs.children),index=buttons.indexOf(document.activeElement);if(index<0)return;let target;if(event.key==='ArrowRight')target=(index+1)%buttons.length;if(event.key==='ArrowLeft')target=(index+buttons.length-1)%buttons.length;if(event.key==='Home')target=0;if(event.key==='End')target=buttons.length-1;if(target!==undefined){event.preventDefault();buttons[target].click();buttons[target].focus();}});
  };
  document.addEventListener('DOMContentLoaded',()=>{
    const toggle=document.querySelector('.mobile-nav-toggle'), sidebar=document.querySelector('.workspace-sidebar'),backdrop=document.querySelector('.nav-backdrop');
    const mobile=matchMedia('(max-width: 800px)');
    function menu(open){if(!sidebar)return;document.body.classList.toggle('navigation-open',open);toggle?.setAttribute('aria-expanded',String(open));backdrop.hidden=!open;sidebar.inert=mobile.matches&&!open;if(open)sidebar.querySelector('a').focus();else if(mobile.matches)toggle?.focus();}
    if(sidebar){sidebar.inert=mobile.matches;toggle?.addEventListener('click',()=>menu(!document.body.classList.contains('navigation-open')));backdrop.addEventListener('click',()=>menu(false));document.querySelector('.mobile-nav-close').addEventListener('click',()=>menu(false));mobile.addEventListener('change',()=>{menu(false);sidebar.inert=mobile.matches;});}
    document.addEventListener('keydown',event=>{if(event.key==='Escape'){if(document.body.classList.contains('navigation-open'))menu(false);document.querySelectorAll('.topbar-actions details[open],.search-options[open],.column-layout[open],.table-filter-menu[open]').forEach(details=>{details.open=false;details.querySelector('summary').focus();});}if(event.key==='Tab'&&mobile.matches&&document.body.classList.contains('navigation-open')){const focusable=Array.from(sidebar.querySelectorAll('a,button')),first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
    document.addEventListener('click',event=>{document.querySelectorAll('.topbar-actions details[open],.search-options[open],.column-layout[open],.table-filter-menu[open]').forEach(details=>{if(!event.composedPath().includes(details))details.open=false;});});
    const switcher=document.getElementById('environment-switcher');
    switcher?.addEventListener('change',()=>{
      if(!switcher.value)return;const analysisPage=location.pathname.match(/^\/environments\/\d+\/(compare|coverage|findings)(?:\/|$)/);if(analysisPage){location.href='/environments/'+encodeURIComponent(switcher.value)+'/'+analysisPage[1]+'/';return;}let section='environment';const hash=location.hash.slice(1);
      if(document.body.classList.contains('snapshot-page'))section=/polic|rule|statistics|dfw/.test(hash)?'rules':/service/.test(hash)?'services':/tag/.test(hash)?'tags':/guide|coverage/.test(hash)?'help':'inventory';
      else if(location.pathname.includes('rule-history'))section='activity';else if(location.pathname.includes('collections'))section='collections';
      location.href='/workspace/'+section+'/?environment='+encodeURIComponent(switcher.value)+(hash?'&panel='+encodeURIComponent(hash):'');
    });
    const snapshot=document.getElementById('snapshot-switcher');if(snapshot){const label=snapshot.closest('.snapshot-select'),home=label.parentElement;const place=()=>{if(mobile.matches)home.append(label);else document.getElementById('snapshot-topbar-slot').append(label);};place();mobile.addEventListener('change',place);}
    const tabs=document.querySelector('.report-section-tabs');
    const inventory=[['Groups','all-groups'],['Services','all-services'],['Tags','tags-all'],['Scopes','tags-scopes']];
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
      ['.sort-key','.sort-order'].forEach(selector=>{const label=tools.querySelector(selector)?.closest('label');if(label)label.hidden=true;});
      const footer=make('div',null,'table-pagination');['.page-status','.page-size','.previous','.next'].forEach(selector=>{const control=tools.querySelector(selector);if(control)footer.append(selector==='.page-size'?control.closest('label'):control);});widget.append(footer);
      const columns=widget.querySelector('.column-layout');if(columns){columns.querySelector('summary').textContent='Columns';const list=columns.querySelector(':scope > div'),body=make('div',null,'column-popup');list.before(body);body.append(list);columns.querySelectorAll(':scope > small,:scope > button').forEach(node=>body.append(node));tools.append(columns);}
      const filter=make('details',null,'table-filter-menu');filter.append(make('summary','Filters'));
      const choices=make('div',null,'filter-picker');widget.querySelectorAll('.header-filter').forEach(header=>{const button=make('button',header.textContent.replace(/[●▾]/g,'').trim());button.type='button';button.addEventListener('click',()=>{filter.open=false;header.click();});choices.append(button);});filter.append(choices);tools.append(filter);
      const menuPrefix='menu:'+document.body.dataset.tableScope+':'+widget.closest('[data-panel]').id;rememberMenu(advanced,menuPrefix+':search');if(columns)rememberMenu(columns,menuPrefix+':columns');
      const exportButton=Array.from(tools.querySelectorAll(':scope > button')).find(button=>button.textContent==='Export CSV');if(exportButton){exportButton.textContent='Export';exportButton.setAttribute('aria-label','Export CSV');tools.append(exportButton);}
      const mobileOptions=make('details',null,'table-options'),mobileBody=make('div',null,'table-options-body');
      mobileOptions.append(make('summary','Table options'),mobileBody);tools.after(mobileOptions);
      const actions=[advanced,columns,filter,exportButton].filter(Boolean);
      const placeOptions=()=>{actions.forEach(control=>(narrow.matches?mobileBody:tools).append(control));if(!narrow.matches)mobileOptions.open=false;};
      narrow.addEventListener('change',placeOptions);placeOptions();
    }
    function enhanceRows(widget){widget.querySelectorAll('.previous,.next').forEach(button=>button.title=button.disabled?(button.classList.contains('previous')?'You are on the first page.':'There are no more results.'):'');widget.querySelectorAll('code.path').forEach(path=>path.title=path.textContent);widget.querySelectorAll('tbody tr').forEach(row=>{const cell=row.querySelector('td[data-column-index="0"]')||row.cells[0],trigger=row.querySelector('.detail-button');if(!cell||!trigger||cell.querySelector('.row-open'))return;const name=cell.querySelector('strong')||cell.firstChild;if(!name)return;const button=make('button',name.textContent,'row-open');button.type='button';button.setAttribute('aria-label','Inspect '+name.textContent);button.addEventListener('click',()=>trigger.click());name.replaceWith(button);});}
    document.querySelectorAll('.table-widget').forEach(widget=>{widget.addEventListener('table-render',()=>{streamline(widget);enhanceRows(widget);});streamline(widget);enhanceRows(widget);});
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

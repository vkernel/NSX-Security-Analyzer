(() => {
  const make=(tag,text,className)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(className)node.className=className;return node;};
  const evidenceTabs=new Map();
  /* Drawer Summary: state badges and an evidence strip lead (how complete each evidence
     kind is), then count tiles and a details card in the page components. Values are
     labels, never raw stored strings; unknown and partial evidence never look empty. */
  const STATES={
    power_state:{VM_RUNNING:['Running','ok'],VM_STOPPED:['Powered off','neutral'],VM_SUSPENDED:['Suspended','neutral'],UNKNOWN:['Power state unknown','warn']},
    membership:{nonempty:['Has members','ok'],empty:['Empty','neutral'],unknown:['Membership unknown','warn'],not_assessed:['Membership not assessed','neutral']},
    usage:{referenced:['Referenced','ok'],unused_candidate:['No references found','warn'],unknown:['Usage unknown','warn'],not_assessed:['Usage not assessed','neutral']},
    hit_status:{traffic_recorded:['Traffic recorded','ok'],zero_hits:['Zero recorded hits','warn'],not_supported:['Counters not supported','neutral'],unknown:['Activity unknown','warn']},
    action:{ALLOW:['Allow','ok'],DROP:['Drop','bad'],REJECT:['Reject','bad'],JUMP_TO_APPLICATION:['Jump to application','neutral']},
    disabled:{true:['Disabled','neutral'],false:['Enabled','ok']},
    policy_status:{has_rules:['Has rules','ok'],empty:['Empty policy','warn'],unknown:['Rules unknown','warn']},
    tag_status:{both:['VMs and groups','ok'],vm_only:['VM use','ok'],group_only:['Group use','ok'],other_only:['Other resource use','ok'],unknown:['Needs review','warn']},
  };
  const COVERAGE={complete:['Complete','ok'],partial:['Partial','warn'],unknown:['Unknown','warn'],unavailable:['Unavailable','warn'],not_collected:['Not collected','neutral'],not_applicable:['Not applicable','neutral']};
  const humanize=value=>{const text=String(value).replaceAll('_',' ').trim().toLowerCase();return text.charAt(0).toUpperCase()+text.slice(1);};
  const typeLabel=row=>row.inventory_type||(row.kind==='custom_service'?'Custom service':row.kind?humanize(row.kind):row.power_state!==undefined?'Virtual machine':row.action!==undefined?'Firewall rule':row.rule_count!==undefined?'Firewall policy':'');
  const pill=(text,tone)=>make('span',text,'pill evidence-pill '+(tone||'neutral'));
  function stateFor(row,key){
    const value=row[key];if(value===undefined||value===null||value==='')return null;
    const table=key==='status'?(row.rule_count!==undefined?STATES.policy_status:row.inventory_type==='Tag'?STATES.tag_status:null):STATES[key];
    if(!table)return null;
    const found=table[String(value)];return found||[humanize(value),'neutral'];
  }
  function evidenceSummary(row){
    const nodes=[],badges=make('div',null,'evidence-badges');
    for(const key of ['power_state','action','disabled','hit_status','membership','usage','status']){
      if(key==='membership'&&row.membership==='not_applicable')continue;
      const state=stateFor(row,key);if(state)badges.append(pill(...state));
    }
    if(Array.isArray(row.finding_assessments))for(const label of new Set(row.finding_assessments.map(item=>item?.label).filter(Boolean)))badges.append(pill(label,/eligible/i.test(label)?'info':'neutral'));
    if(badges.children.length)nodes.push(badges);
    // Evidence strip: one tile per saved evidence kind, with its own completeness.
    const strip=make('div',null,'evidence-strip');strip.setAttribute('role','group');strip.setAttribute('aria-label','Membership evidence');
    for(const [count,status,label] of [['direct_group_count','direct_membership_status','Direct VM groups'],['network_group_count','network_membership_status','Interface groups'],['resolved_group_count','resolved_membership_status','All saved groups']]){
      if(row[status]==null&&row[count]==null)continue;
      const tile=make('div'),state=COVERAGE[row[status]]||(row[status]?[humanize(row[status]),'neutral']:['Not recorded','neutral']);
      const known=row[count]!=null&&!['unknown','unavailable','not_collected'].includes(row[status]);
      tile.append(make('span',label),make('strong',known?String(row[count]):'—'));
      const note=pill(row[status]==='partial'&&known?'Partial · confirmed only':state[0],state[1]);tile.append(note);strip.append(tile);
    }
    if(strip.children.length)nodes.push(strip);
    // Count tiles for the object's main quantities.
    const tiles=make('div',null,'evidence-tiles');
    const references=Array.isArray(row.referenced_by)?row.referenced_by.length:null;
    for(const [value,label,note] of [[row.tag_count,'Tags',row.inventory_type==='Scope'?'in this scope':'assigned'],[references,'Configuration references','direct, saved'],[row.rule_count,'Rules','in this policy'],[row.hit_count,'Recorded hits','current counters'],
      [row.inventory_type==='Virtual machine'?null:row.group_count,'Groups','referencing this tag'],[row.other_count,'Other resources','assignments'],[row.tagged_objects,'Catalog assignments','NSX catalog']]){
      if(value===undefined||value===null||value==='')continue;
      const tile=make('div');tile.append(make('span',label),make('strong',String(value)),make('small',note));tiles.append(tile);
    }
    if(tiles.children.length)nodes.push(tiles);
    // Remaining descriptive facts as labelled rows.
    const card=make('div',null,'evidence-card'),head=make('div',null,'evidence-card-head'),rows=make('dl',null,'evidence-rows');
    head.append(make('h3','Details'));card.append(head,rows);
    const fact=(label,value)=>{if(value===undefined||value===null||value==='')return;rows.append(make('dt',label),make('dd',String(value)));};
    fact('Type',typeLabel(row));
    const scope=Array.isArray(row.scope)?row.scope:row.scope==null||row.scope===''?[]:[row.scope];
    // A rule's own Applied To; a policy's Applied To can take precedence and is shown in the rule fields.
    const scopeLabel=row.inventory_type==='Tag'||row.inventory_type==='Scope'?'Scope':row.action!==undefined?'Rule Applied to':'Applied to';
    if(scope.length&&!(row.action!==undefined&&Array.isArray(row.source_groups)))fact(scopeLabel,scope.map(value=>String(value).toUpperCase()==='ANY'?'Any':scopeLabel==='Scope'?String(value):String(value).split('/').pop()).join(', '));
    fact('Category',row.category);
    if(row.policy_name)fact('Policy',row.policy_name);
    if(row.resolved_membership_scope)fact('Evidence scope',row.resolved_membership_scope);
    const checked=row.resolved_membership_checked_at||row.network_membership_checked_at;
    if(checked){const dd=make('dd');if(window.workspaceTime){const template=document.createElement('template');template.innerHTML=window.workspaceTime(checked);dd.append(template.content);}else dd.textContent=checked;rows.append(make('dt','Membership checked'),dd);}
    if(rows.children.length)nodes.push(card);
    return nodes;
  }
  // Older report markup inside the Summary tab: per-finding qualification lines and
  // note lists become cards; the wording is kept, only "33 / 21 days" is spelled out.
  function summaryCards(items,hasReferenceTile){
    const result=[],readiness=[],notes=[];
    for(const node of items){
      if(node.matches?.('p.row-qualification')){readiness.push(node);continue;}
      if(node.matches?.('ul.notes')){notes.push(...node.children);continue;}
      if(hasReferenceTile&&node.matches?.('span.muted')&&/^No configuration references found\.?$/.test(node.textContent.trim()))continue;
      result.push(node);
    }
    const card=(title,rows)=>{const box=make('div',null,'evidence-card'),head=make('div',null,'evidence-card-head'),list=make('ul',null,'evidence-list');head.append(make('h3',title));list.append(...rows);box.append(head,list);return box;};
    const cards=[];
    if(readiness.length)cards.push(card('Review readiness',readiness.map(node=>{
      const badge=node.querySelector('.badge')?.textContent.trim()||'',detail=node.querySelector('small')?.textContent.trim()||'';
      const [kind,state]=badge.includes(':')?badge.split(/:\s*/,2):['',badge];const item=make('li'),text=make('div');
      text.append(make('strong',kind?humanize(kind):'Finding'));
      if(detail)text.append(make('small',detail.replace(/^(\d+) \/ (\d+) days/,'$1 days observed, $2 required')));
      item.append(text,pill(state||'Not assessed',/eligible/i.test(state)?'info':'neutral'));return item;})));
    if(notes.length)cards.push(card('Notes',notes.map(note=>{const item=make('li');item.append(make('span',note.textContent.trim()));return item;})));
    return [...cards,...result];
  }
  // Firewall rules: saved rule fields, counters and empty-group references as cards. Excluded
  // Source/Destination fields are negated (NOT); a policy Applied To takes precedence.
  function ruleCards(row,body){
    const rule=row.configuration?.rule||{},policy=row.configuration?.policy||{},cards=[];
    const card=(title,content,note)=>{const box=make('div',null,'evidence-card'),head=make('div',null,'evidence-card-head');head.append(make('h3',title));box.append(head,content);if(note)box.append(make('p',note,'evidence-card-note'));return box;};
    const values=list=>{
      const cell=make('dd',null,'evidence-values');const items=Array.isArray(list)?list:[];
      if(!items.length){cell.append(make('span','Not recorded','muted'));return cell;}
      for(const value of items){
        const text=String(value);
        if(text.toUpperCase()==='ANY'){cell.append(make('span','Any','evidence-value'));continue;}
        const name=text.split('/').pop();
        if(text.startsWith('/infra/')&&window.openRelatedEvidence){const link=make('button',name,'evidence-object-link evidence-value');link.type='button';link.title=text;link.onclick=()=>window.openRelatedEvidence(body,{path:text},name);cell.append(link);}
        else{const span=make('span',name,'evidence-value');span.title=text;cell.append(span);}
      }
      return cell;
    };
    const fields=make('dl',null,'evidence-rows rule-field-rows');
    const field=(label,list,negated,origin)=>{const dt=make('dt',label),dd=values(list);if(negated)dd.prepend(make('span','NOT','evidence-not'));if(origin)dd.prepend(make('span',origin,'evidence-origin'));fields.append(dt,dd);};
    field('Sources',row.source_groups,rule.sources_excluded===true);
    field('Destinations',row.destination_groups,rule.destinations_excluded===true);
    field('Services',row.services);
    const policyScope=Array.isArray(policy.scope)&&policy.scope.length&&!policy.scope.every(value=>String(value).toUpperCase()==='ANY')?policy.scope:null;
    field('Applied to',policyScope||row.scope,false,policyScope?'From policy':'');
    if(rule.direction)fields.append(make('dt','Direction'),make('dd',humanize(rule.direction).replace('In out','In and out')));
    if(rule.logged!==undefined)fields.append(make('dt','Logging'),make('dd',rule.logged?'On':'Off'));
    cards.push(card('Rule fields',fields,'Saved configuration, not effective policy or observed traffic.'));
    const counters=make('dl',null,'evidence-rows');
    if(row.statistics_checked_at){const dd=make('dd');if(window.workspaceTime){const template=document.createElement('template');template.innerHTML=window.workspaceTime(row.statistics_checked_at);dd.append(template.content);}else dd.textContent=row.statistics_checked_at;counters.append(make('dt','Checked'),dd);}
    for(const sample of Array.isArray(row.statistics)?row.statistics:[]){
      const point=String(sample.enforcement_point||'Enforcement point').split('/').pop(),parts=[];
      for(const [key,label] of [['hit_count','hits'],['packet_count','packets'],['byte_count','bytes'],['session_count','sessions']])if(sample[key]!=null)parts.push(Number(sample[key]).toLocaleString()+' '+label);
      const dt=make('dt','Enforcement point '+point);dt.title=sample.enforcement_point||'';counters.append(dt,make('dd',parts.join(' · ')||'No counter values'));
    }
    if(row.statistics_retry)counters.append(make('dt','Timeout recovery'),make('dd','One sequential recovery attempt after the first pass'+(row.statistics_retry.initial_checked_at?'; initial check '+row.statistics_retry.initial_checked_at:'')));
    {const history=row.last_positive_observation,dd=make('dd');
     if(history&&history.observed_at){dd.append(Number(history.hit_count||0).toLocaleString()+' hit(s) at ');if(window.workspaceTime){const template=document.createElement('template');template.innerHTML=window.workspaceTime(history.observed_at);dd.append(template.content);}else dd.append(history.observed_at);}
     else dd.textContent='Unavailable \u2014 no saved positive snapshot';
     counters.append(make('dt','Last positive count'),dd);}
    if(counters.children.length)cards.push(card('Counters',counters,'Saved audit observations, not the last packet time. Traffic and resets between collections may be missed.'));
    if(Array.isArray(row.empty_group_references)&&row.empty_group_references.length){
      const list=make('ul',null,'evidence-list');
      for(const item of row.empty_group_references){const li=make('li'),text=make('div');text.append(make('strong',item.name||String(item.path||'').split('/').pop()),make('small',(item.field||'Field not recorded')+' field'));li.append(text,pill('Confirmed empty','warn'));list.append(li);}
      cards.push(card('Empty group references',list));
    }
    return cards;
  }
  function isLegacyRuleNode(node){
    if(node.matches?.('details[data-evidence-section="overview"]')&&/Counter\s*&\s*rule details/i.test(node.querySelector(':scope > summary')?.textContent||''))return true;
    if(node.matches?.('h3')&&/Confirmed empty group references/i.test(node.textContent))return true;
    if(node.matches?.('p')&&(/^Last observed positive count:/i.test(node.textContent.trim())||/^Saved audit observation, not the last packet time/i.test(node.textContent.trim())))return true;
    return false;
  }
  function placeImpact(nodes,link,groups){
    const head=nodes.find(node=>node.classList?.contains('evidence-card'))?.querySelector('.evidence-card-head');
    if(head){link.className='evidence-card-action';head.append(link);}else groups.get('Summary').push(link);
  }
  window.workspaceEvidence=(body,title,row)=> {
    body.closest('dialog')?.classList.remove('evidence-firewall-wide');
    window.objectReferenceTables?.(body,row);
    window.objectMembershipTables?.(body,row);
    window.referenceListTables?.(body);
    const nodes=Array.from(body.children), groups=new Map([['Summary',[]],['Members',[]],['References',[]],['History',[]],['Technical details',[]]]);
    const summaryNodes=row?evidenceSummary(row):[];
    if(row){
      // Identity card: the exact saved path (with copy), membership method and NSX unique ID.
      const identityRows=make('dl',null,'evidence-rows');
      if(row.path){const dd=make('dd',null,'evidence-identity');dd.append(make('code',row.path));const copy=make('button','Copy path');copy.type='button';copy.dataset.copyPath=row.path;dd.append(copy);identityRows.append(make('dt','Path'),dd);}
      if(row.membership_definition?.methods?.length)identityRows.append(make('dt','Membership method'),make('dd',row.membership_definition.methods.join(', ')));
      if(row.unique_id)identityRows.append(make('dt','Unique ID'),make('dd',String(row.unique_id)));
      if(row.rule_id!=null)identityRows.append(make('dt','Rule ID'),make('dd',String(row.rule_id)));
      if(identityRows.children.length){const card=make('div',null,'evidence-card'),head=make('div',null,'evidence-card-head');head.append(make('h3','Identity'));card.append(head,identityRows);groups.get('Technical details').push(card);}
    }
    if(row?.statistics_checked_at){const timing=make('p','Counters checked: '),time=make('time');time.dateTime=row.statistics_checked_at;time.textContent=new Date(row.statistics_checked_at).toString();if(window.workspaceTime){const template=document.createElement('template');template.innerHTML=window.workspaceTime(row.statistics_checked_at);timing.append(template.content);}else timing.append(time);groups.get('Technical details').push(timing);}
    const impactBase=document.querySelector('[data-impact-base]')?.dataset.impactBase;
    if(impactBase && row?.path && (row.kind==='group' || row.inventory_type==='Group' || row.policy_path || row.rule_count!==undefined)){
      const link=make('a','Impact preview','button');link.href=impactBase+'?'+new URLSearchParams({path:row.path});placeImpact(summaryNodes,link,groups);
    }
    if(impactBase && (row?.inventory_type==='Virtual machine' || row?.inventory_type==='Tag')){
      const params=row.inventory_type==='Tag'?{action:'remove-tag',scope:row.scope||'',tag:row.name}:{action:'delete-vm',path:row.path};
      const link=make('a','Impact preview','button');link.href=impactBase+'?'+new URLSearchParams(params);placeImpact(summaryNodes,link,groups);
    }
    groups.get('Summary').unshift(...summaryNodes);
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
    {const lead=groups.get('Summary').slice(0,summaryNodes.length);let rest=groups.get('Summary').slice(summaryNodes.length);
     if(row&&row.action!==undefined&&Array.isArray(row.source_groups)){
       // The old rule block is replaced; its empty-group list (an h3 followed by a list) goes with it.
       rest=rest.filter((node,index)=>!isLegacyRuleNode(node)&&!(node.matches?.('ul')&&rest[index-1]&&isLegacyRuleNode(rest[index-1])&&/empty group/i.test(rest[index-1].textContent)));
       lead.push(...ruleCards(row,body));
     }
     groups.set('Summary',[...lead,...summaryCards(rest,summaryNodes.some(node=>node.classList?.contains('evidence-tiles')&&/Configuration references/.test(node.textContent)))]);}
    if(row && !row.evidence_summary_only && !groups.get('Technical details').some(node=>node.matches('details,pre,.code-block')) && row.power_state===undefined){
      const source=make('details');source.append(make('summary','Saved object data'));const content=make('div');source.append(content);
      source.addEventListener('toggle',()=>{if(source.open&&!content.children.length)content.append(window.technicalEvidence(row));});groups.get('Technical details').push(source);
    }
    // Object references remain inside the drawer; the table is never navigated or reset.
    for(const node of groups.get('References'))node.querySelectorAll('code').forEach(code=>{
      const path=code.textContent.trim();if(!path.startsWith('/infra/')||path.includes(',')||code.closest('button'))return;
      const link=make('button',path,'evidence-object-link');link.type='button';link.onclick=()=>window.openRelatedEvidence(body,{path},path.split('/').pop());code.replaceWith(link);
    });
    body.dataset.evidenceType=row?typeLabel(row):'';
    window.prepareEvidenceDrawer?.(body);
    // The tabs already name the view, so the title keeps only the object's name.
    const heading=title&&title.nodeType===1?title:body.closest('dialog')?.querySelector('.dialog-heading h2');
    if(heading&&row)heading.textContent=heading.textContent.replace(/\s+\u2014\s+[^\u2014]+$/,'');
    body.replaceChildren();const tabs=make('div',null,'evidence-tabs');tabs.setAttribute('role','tablist');body.append(tabs);
    const type=row?.inventory_type || row?.kind || (row?.power_state!==undefined?'vm':row?.action!==undefined?'rule':'object');
    let first=true;
    groups.forEach((items,label)=>{
      if(!items.length&&label==='Members'&&!(row?.kind==='group'||row?.inventory_type==='Group'))return;
      if(!items.length&&label==='History'){const empty=make('div',null,'evidence-empty');empty.append(make('strong','No history in this snapshot'),make('p','This snapshot contains point-in-time evidence. Open Reviews for decision and observation history.','muted'));items.push(empty);}
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
    // The object's type leads the context line (set by workspaceEvidence for this drawer).
    context.textContent=[body.dataset.evidenceType,environment,snapshot].filter(Boolean).join(' · ');
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
    const firewall=[['Summary','dfw-overview'],['Policies','dfw-policies'],['Rules','dfw-rules'],['References','object-references']];
    const filters={
      groups:[['All groups','all-groups'],['Unused groups','unused-groups'],['Empty groups','empty-groups'],['Membership unknown','unknown-membership']],
      services:[['All services','all-services'],['Unused services','unused-services']],
      tags:[['All tags','tags-all'],['VMs and groups','tags-both'],['VM use','tags-vm_only'],['Group use','tags-group_only'],['Other resource use','tags-other_only'],['Needs review','tags-unknown']],
      policies:[['All policies','dfw-policies'],['Empty policies','empty-policies']],
      rules:[['All rules','dfw-rules'],['Zero recorded hits','zero-hit-rules'],['Disabled rules','disabled-rules'],['Incomplete statistics','unknown-statistics'],['Empty group references','empty-group-rules'],['Applied to DFW','dfw-scope-rules']]
    };
    function reportNavigation(){
      if(!tabs)return;const id=location.hash.slice(1)||'overview';let kind=Object.keys(filters).find(key=>filters[key].some(([,anchor])=>anchor===id));
      const isFirewall=['rules','policies'].includes(kind)||id==='dfw-overview'||id==='object-references',isHelp=['feature-guide','coverage','tags-coverage'].includes(id);
      const items=isHelp?[['User guide','feature-guide'],['Coverage & definitions','coverage'],['Tag coverage','tags-coverage']]:isFirewall?firewall:[['Overview','overview'],...inventory];
      tabs.replaceChildren();for(const [label,anchor] of items){const link=make('a',label);link.href='#'+anchor;if(id===anchor || (kind && filters[kind]?.some(([,value])=>value===id) && filters[kind][0][1]===anchor))link.setAttribute('aria-current','page');tabs.append(link);}
      if(isFirewall){const link=make('a','Historical activity');link.href='/environments/'+document.body.dataset.tableScope+'/rule-history/';tabs.append(link);}
      document.querySelectorAll('[data-primary]').forEach(link=>{if(link.dataset.primary===(isFirewall?'firewall':isHelp?'help':'inventory'))link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
      const selected=document.getElementById(id),heading=selected?.querySelector(':scope > h2');
      const pageTitle=document.querySelector('.report-toolbar h1');
      if(pageTitle){const copy=heading?.cloneNode(true),count=copy?.querySelector('.count');count?.remove();pageTitle.textContent=id==='overview'?'Inventory overview':copy?.textContent.trim() || 'Inventory';if(count){pageTitle.append(document.createTextNode(' '),count);}if(heading)heading.hidden=true;}
      const slot=document.querySelector('.report-view-selector');tabs.after(slot);slot.replaceChildren();if(kind){
        // Views are links styled as chips; counts come from the overview's saved finding categories.
        const nav=make('nav',null,'inventory-filter-chips');nav.setAttribute('aria-label','Views');
        for(const [text,value] of filters[kind]){const link=make('a',text);link.href='#'+value;const total=document.querySelector('#overview nav.cards a[href="#'+value+'"] b')?.textContent.trim();if(total){link.append(' ',make('span',total));}if(value===id)link.setAttribute('aria-current','page');nav.append(link);}
        slot.append(nav);const tools=selected?.querySelector('.table-tools');if(tools)tools.append(slot);}
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
        chart.querySelectorAll('.chart-legend a').forEach(link=>{const percent=parseFloat(link.querySelector('small')?.textContent)||0,label=link.querySelectorAll('span')[1]?.textContent||'',segment=make('span');segment.style.width=percent+'%';const tone=link.dataset.usage||(/unused|zero/i.test(label)?'candidate':/unknown/i.test(label)?'unknown':/not assessed|disabled|not supported/i.test(label)?'unassessed':'referenced');segment.className='usage-'+tone;const key=link.querySelector('.chart-key');if(key)key.style.background='var(--usage-'+tone+')';bar.append(segment);});
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
    // Saved-projection summary from the server leads the overview; report sections follow unchanged.
    const summaryTemplate=document.getElementById('inventory-overview-summary');
    if(overview&&summaryTemplate&&!overview.querySelector('[data-inventory-overview]')){
      const summary=summaryTemplate.content.firstElementChild.cloneNode(true),heading=overview.querySelector(':scope > h1, :scope > h2:first-child');
      if(heading&&heading.tagName==='H1')heading.after(summary);else overview.prepend(summary);
      const findings=overview.querySelector('.overview-findings-list'),slot=summary.querySelector('[data-overview-attention]');
      if(findings&&slot){
        const card=make('section',null,'content-card overview-attention'),head=make('div',null,'content-card-head'),title=make('h2','Needs attention'),note=make('span','From saved evidence','muted small');
        title.id='overview-attention-title';card.setAttribute('aria-labelledby',title.id);head.append(title,note);
        findings.querySelector(':scope > h2')?.remove();card.append(head,...findings.childNodes);findings.remove();slot.replaceWith(card);
      }else slot?.remove();
      overview.querySelector('.compact-inventory-totals')?.setAttribute('hidden','');
    }
    // Firewall Summary: the server-rendered summary replaces the report's legacy overview body.
    const firewallSection=document.getElementById('dfw-overview'),firewallTemplate=document.getElementById('firewall-overview-summary');
    if(firewallSection&&firewallTemplate&&!firewallSection.querySelector('[data-firewall-overview]')){
      const heading=firewallSection.querySelector(':scope > h2');
      [...firewallSection.children].forEach(node=>{if(node!==heading)node.remove();});
      firewallSection.append(firewallTemplate.content.firstElementChild.cloneNode(true));
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

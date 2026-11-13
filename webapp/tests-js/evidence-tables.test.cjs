const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function setup(){const dom=new JSDOM('<main></main><dialog open id="detail-dialog"><div class="dialog-heading"><h2 id="detail-title">VM references</h2><button>Close</button></div><div id="detail-body"></div></dialog>',{url:'https://example.test/snapshots/one/',runScripts:'outside-only'});dom.window.matchMedia=()=>({matches:false,addEventListener(){}});for(const name of ['workspace-tabs.js','evidence.js','application.js','report-data.js'])dom.window.eval(fs.readFileSync(path.join(__dirname,'../static',name),'utf8'));dom.window.document.getElementById("detail-dialog").membershipInitialSource="saved";return dom;}
function tab(d,label,root=d.querySelector('#detail-body > .evidence-tabs')){[...root.querySelectorAll('[role=tab]')].find(b=>b.textContent===label).click();}
const summary={name:'VM',power_state:'VM_RUNNING',tag_count:1,group_count:1};
const rule={name:'HTTPS <script>unsafe</script>',path:'/infra/rules/https',policy_name:'Portal',action:'DROP',disabled:true};
const responses={summary:{data:summary},tags:{items:[{scope:'Application',tag:'Portal'}]},related_groups:{items:[{name:'Web',path:'/infra/groups/web'}]},related_rules:{items:[rule,{name:'Legacy',path:'/infra/rules/legacy'}],has_next:true},rule:{details:{action:'ALLOW',disabled:false,services:['ANY',{name:'HTTPS',path:'/infra/services/https'}]}},via_groups:{items:['/infra/groups/web']}};
responses.resolved_groups={items:[{name:'Web',path:'/infra/groups/web'}],status:'partial'};responses.confirmed_rules=responses.related_rules;responses.confirmed_rule=responses.rule;responses.confirmed_via_groups=responses.via_groups;
test('VM reference tables are lazy, paged, labelled and do not fetch every rule',async()=>{const dom=setup(),w=dom.window,d=w.document,calls=[];w.reportData=async(_,query)=>{calls.push(query);return responses[query.section];};await w.showVmRelationships(d.getElementById('detail-body'),null,7,null);assert.deepEqual(calls.map(c=>c.section),['summary']);tab(d,'References');await tick();assert.equal(d.querySelector('#evidence-references details'),null);assert.equal(d.querySelector('.evidence-table caption').textContent,'Confirmed groups');tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();const table=[...d.querySelectorAll('table')].find(t=>t.caption.textContent==='Firewall references');assert.deepEqual([...table.querySelectorAll('th')].map(th=>th.textContent),['Category','Name','ID','Sources','Destinations','Services','Applied to','Action','State','Matched in','Reason / limits']);assert.equal(table.querySelector('[data-tone=negative]').textContent,'DROP');assert.equal(table.querySelector('[data-tone=warning]').textContent,'Disabled');assert.equal(table.querySelectorAll('script').length,0);assert.match(table.textContent,/<script>unsafe<\/script>/);assert.ok(table.rows[2].textContent.includes('Not recorded'));assert.equal(calls.some(c=>c.section==='rule'),false);const section=table.closest('section');section.querySelector('[aria-label="Next firewall references"]').click();await tick();assert.equal(calls.at(-1).page,1);assert.match(section.querySelector('[role=status]').textContent,/26–27/);dom.window.close();});
test('rule inspection uses service and provenance tables and returns focus to the same row',async()=>{const dom=setup(),w=dom.window,d=w.document;w.reportData=async(_,query)=>responses[query.section];w.openRelatedEvidence=async()=>{};await w.showVmRelationships(d.getElementById('detail-body'),null,7,summary);tab(d,'References');tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();const inspect=d.querySelector('[aria-label^="Inspect references"]');inspect.focus();inspect.click();await tick();await tick();const detail=d.querySelector('.evidence-rule-inspection');assert.equal(detail.hidden,false);assert.equal(detail.querySelectorAll('details').length,0);assert.match(detail.textContent,/Any service/);assert.deepEqual([...detail.querySelectorAll('caption')].map(c=>c.textContent),['Configured services','Related groups for this rule']);assert.equal(detail.parentElement.dataset.inspecting,'true');detail.querySelector('header button').click();assert.equal(detail.hidden,true);assert.equal(detail.parentElement.dataset.inspecting,undefined);assert.equal(d.activeElement,inspect);dom.window.close();});
test('reference errors offer retry and closed drawers reject late responses',async()=>{const dom=setup(),w=dom.window,d=w.document;let fail=true,resolve;w.reportData=async(_,q)=>{if(q.section==='resolved_groups'){if(fail)throw Error('Unavailable');return new Promise(r=>resolve=r);}return responses[q.section];};await w.showVmRelationships(d.getElementById('detail-body'),null,7,summary);tab(d,'References');await tick();const content=d.querySelector('#evidence-references .evidence-table-content');assert.match(content.textContent,/Unavailable/);fail=false;content.querySelector('button').click();d.getElementById('detail-body').evidenceGeneration=null;resolve({items:[{scope:'Late',tag:'Should not appear'}]});await tick();assert.doesNotMatch(content.textContent,/Should not appear/);dom.window.close();});
test('shared table category tabs support keyboard selection and retain loaded rows',async()=>{const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');w.reportData=async(_,q)=>responses[q.section];await w.showVmRelationships(body,null,7,summary);tab(d,'References');await tick();const nav=d.querySelector('#evidence-references .evidence-category-tabs'),buttons=nav.querySelectorAll('button');buttons[0].focus();nav.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await tick();assert.equal(d.activeElement,buttons[1]);assert.equal(buttons[1].getAttribute('aria-selected'),'true');const group=d.querySelector('table caption').textContent;assert.equal(group,'Confirmed groups');assert.equal(nav.parentElement.querySelectorAll('table').length,2);dom.window.close();});
test('existing group and service reference lists inherit the same table without losing evidence',()=>{const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');body.innerHTML='<details data-evidence-section="relationships"><summary>2 references</summary><p>Saved coverage</p><ul><li>Rule one<code>/infra/rules/one</code></li><li>Rule two<code>/infra/rules/two</code></li></ul></details>';w.workspaceEvidence(body,'Group',{kind:'group',evidence_summary_only:true});const section=d.querySelector('#evidence-references');assert.equal(section.querySelector('details'),null);assert.equal(section.querySelectorAll('tbody tr').length,2);assert.match(section.textContent,/Saved coverage/);assert.equal(section.querySelectorAll('.evidence-object-link').length,2);dom.window.close();});
test('tag and scope drawers use the shared table categories and keep missing state explicit',async()=>{
 for(const view of ['tags','scopes']){const dom=setup(),w=dom.window,d=w.document,calls=[];d.querySelector('main').innerHTML='<div data-server-table="tags-all"></div><button class="detail-button" data-tag-row="3">Tag details</button>';w.reportData=async(_,q)=>{calls.push(q.section);if(q.section==='summary')return {view,data:{name:'Portal',scope:'Application',inventory_type:view==='tags'?'Tag':'Scope'}};if(q.section==='notes')return {items:['Saved evidence']};if(q.section==='firewall_references')return {items:[{name:'Rule',path:'/infra/rule/1',via_group:'/infra/groups/web',tag_use:'condition',reference_reason:'Group membership condition'}]};return {items:[{name:'Portal',vm_count:4,group_count:2,other_count:0}]};};d.querySelector('.detail-button').click();await tick();assert.deepEqual(calls,['summary']);tab(d,'References');await tick();const nav=d.querySelector('#evidence-references .evidence-category-tabs');assert.equal(d.querySelector('#evidence-references details'),null);if(view==='tags'){tab(d,'Firewall rules',nav);await tick();const section=[...d.querySelectorAll('.evidence-table-section')].find(s=>s.querySelector('h3').textContent==='Firewall rules');assert.match(section.textContent,/Not recorded/);assert.match(section.textContent,/Group membership condition/);}else{assert.deepEqual([...d.querySelectorAll('thead th')].map(n=>n.textContent),['Tag','VMs','Groups','Other']);assert.match(d.querySelector('tbody').textContent,/Portal420/);}dom.window.close();}
});
test('technical definitions remain lazy and paginated outside reference tables',async()=>{const dom=setup(),w=dom.window,d=w.document,calls=[];d.querySelector('main').innerHTML='<div data-server-table="tags-all"></div><button class="detail-button" data-tag-row="3">Tag details</button>';w.reportData=async(_,q)=>{calls.push(q);return q.section==='summary'?{view:'tags',data:{name:'Portal',scope:'Application'}}:{items:[{resource_type:'Condition',key:'Tag',value:'Application|Portal'}],has_next:true};};d.querySelector('.detail-button').click();await tick();tab(d,'Technical details');await new Promise(r=>setTimeout(r,5));const definition=d.querySelector('#evidence-technical-details details details');assert.ok(definition);assert.equal(definition.querySelector('.technical-viewer'),null);definition.open=true;await new Promise(r=>setTimeout(r,5));assert.ok(definition.querySelector('.technical-viewer'));const outer=definition.parentElement;[...outer.querySelectorAll('button')].find(b=>b.textContent==='Next').click();await tick();assert.equal(calls.at(-1).page,1);assert.equal(calls.at(-1).section,'condition_evidence');dom.window.close();});
test('firewall category widens only while active, restores related Back width and clears on close',async()=>{const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),dialog=d.getElementById('detail-dialog');w.reportData=async(_,q)=>q.op==='resolve'?{view:'inventory',id:9}:responses[q.section];await w.showVmRelationships(body,null,7,summary);tab(d,'References');tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();assert.equal(dialog.classList.contains('evidence-firewall-wide'),true);tab(d,'Summary');assert.equal(dialog.classList.contains('evidence-firewall-wide'),false);tab(d,'References');assert.equal(dialog.classList.contains('evidence-firewall-wide'),true);await w.openRelatedEvidence(body,{path:'/infra/groups/web'},'Web');dialog.classList.remove('evidence-firewall-wide');dialog.querySelector('.evidence-back').click();assert.equal(dialog.classList.contains('evidence-firewall-wide'),true);tab(d,'Groups',d.querySelector('#evidence-references .evidence-category-tabs'));assert.equal(dialog.classList.contains('evidence-firewall-wide'),false);tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));dialog.dispatchEvent(new w.Event('close'));assert.equal(dialog.classList.contains('evidence-firewall-wide'),false);dom.window.close();});
test('wide firewall columns retain explicit field/group pairs and unmatched provenance',()=>{const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');const cols=w.firewallReferenceColumns(body,()=>{});assert.equal(cols[0].label,'Category');assert.equal(cols[0].width,'7%');assert.equal(cols[1].width,'12%');assert.equal(cols[3].width,'10%');const row={matched_fields:['Source'],matched_groups:[{path:'/infra/g/web',name:'Web'},{path:'/infra/g/nested',name:'Nested'}],field_matches:[{field:'Source',group:{path:'/infra/g/web',name:'Web'}}]};const cell=cols.find(c=>c.label==='Matched in').cell(row);assert.match(cell.textContent,/^Source/);assert.doesNotMatch(cell.textContent,/Web/);assert.match(cell.textContent,/Field not retainedNested/);dom.window.close();});
test('group and service drawers share the firewall schema, lazy paging, width and other references',async()=>{
 for(const kind of ['group','custom_service']){const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];d.querySelector('main').innerHTML='<div data-server-table="all-groups"></div>';w.reportData=async(_,q)=>{calls.push(q);return {items:q.section==='objects'?[{name:'Parent',path:'/infra/groups/parent'}]:[rule],has_next:q.section==='firewall'};};body.innerHTML='<details data-evidence-section="relationships"><summary>Legacy reference list</summary><ul><li>Old content</li></ul></details>';w.workspaceEvidence(body,'Web',{kind,path:'/infra/groups/web',referenced_by:['/infra/rules/https']});assert.equal(calls.length,0);tab(d,'References');await tick();const table=d.querySelector('.evidence-firewall-table');assert.ok(table);assert.equal(table.querySelectorAll('th').length,11);assert.equal(table.querySelectorAll('th')[9].textContent,'Matched in');assert.equal(d.querySelector('#detail-dialog').classList.contains('evidence-firewall-wide'),true);assert.equal(calls[0].op,'object-references');assert.equal(calls[0].path,'/infra/groups/web');assert.equal(d.querySelector('#evidence-references details'),null);d.querySelector('[aria-label="Next firewall references"]').click();await tick();assert.equal(calls.at(-1).page,1);tab(d,'Other objects',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();assert.equal(d.querySelector('#detail-dialog').classList.contains('evidence-firewall-wide'),false);assert.match(d.querySelector('#evidence-references').textContent,/Parent/);assert.equal(calls.at(-1).section,'objects');dom.window.close();}
});
test('object reference load rejects responses after navigating to another group',async()=>{const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');d.querySelector('main').innerHTML='<div data-server-table="all-groups"></div>';let resolve;w.reportData=async()=>new Promise(r=>resolve=r);w.workspaceEvidence(body,'First',{kind:'group',path:'/infra/groups/first'});tab(d,'References');await tick();const old=body.querySelector('.evidence-table-content');body.replaceChildren();w.workspaceEvidence(body,'Second',{kind:'group',path:'/infra/groups/second'});resolve({items:[{name:'Stale rule'}]});await tick();assert.doesNotMatch(old.textContent,/Stale rule/);dom.window.close();});
test('remembered group References loads when the saved renderer opens the dialog after restoring tabs',async()=>{const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),dialog=d.getElementById('detail-dialog'),calls=[];d.querySelector('main').innerHTML='<div data-server-table="all-groups"></div>';w.reportData=async(_,q)=>{calls.push(q.path);return {items:[]};};w.workspaceEvidence(body,'First',{kind:'group',path:'/infra/groups/first'});tab(d,'References');await tick();dialog.open=false;body.replaceChildren();w.workspaceEvidence(body,'Second',{kind:'group',path:'/infra/groups/second'});dialog.open=true;await tick();assert.deepEqual(calls,['/infra/groups/first','/infra/groups/second']);assert.ok(d.querySelector('.evidence-firewall-table'));dom.window.close();});
test('VM References groups replaces Members and pages saved evidence lazily',async()=>{
 const dom=setup(),w=dom.window,d=w.document,calls=[];w.reportData=async(_,q)=>{calls.push(q);return q.section==='resolved_groups'?{items:[{path:'/infra/groups/explicit',name:'Explicit VM group',status:'partial'}],status:'partial',has_next:q.page===0}:responses[q.section];};
 await w.showVmRelationships(d.getElementById('detail-body'),null,7,{...summary,membership_evidence_version:1,resolved_group_count:1,resolved_membership_status:'partial'});
 assert.equal(calls.length,0);assert.equal(d.querySelector('#evidence-members'),null);tab(d,'References');await tick();const section=d.querySelector('#evidence-references');assert.match(section.textContent,/Explicit VM group/);assert.match(section.textContent,/Partial/);assert.doesNotMatch(section.textContent,/Web servers/);assert.equal(calls[0].section,'resolved_groups');section.querySelector('[aria-label="Next confirmed groups"]').click();await tick();assert.equal(calls.at(-1).page,1);
 assert.equal(calls.some(q=>q.section==='related_groups'||q.section==='related_rules'),false);dom.window.close();
});
test('older VM membership is not relabelled from tag-reference counts',async()=>{
 const dom=setup(),w=dom.window,d=w.document;w.reportData=async()=>({items:[],status:'not_collected',has_next:false});await w.showVmRelationships(d.getElementById('detail-body'),null,7,{...summary,group_count:999,resolved_group_count:null,resolved_membership_status:'not_collected'});tab(d,'References');await tick();assert.match(d.querySelector('#evidence-references').textContent,/not fully collected in this snapshot/);assert.match(d.querySelector('#evidence-references .evidence-table-status').textContent,/Not collected/);assert.doesNotMatch(d.querySelector('#evidence-summary').textContent,/Groups referencing tags|999/);dom.window.close();
});
test('group Members uses shared tables, exact VM links, independent categories and partial totals',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];d.querySelector('main').innerHTML='<div data-server-table="all-groups"></div>';w.reportData=async(_,q)=>{calls.push(q);return {status:q.section==='virtual-machines'?'partial':'unavailable',saved_count:1,reported_count:200,notes:['Second page failed'],items:q.section==='virtual-machines'?[{name:'VM one',external_id:'one',identity:'one',vm_path:'/vm/one'}]:[],has_next:false};};let query;w.openRelatedEvidence=async(_,value)=>query=value;
 w.workspaceEvidence(body,'Group',{kind:'group',path:'/infra/groups/web'});assert.equal(calls.length,0);tab(d,'Members');await tick();let section=d.querySelector('#evidence-members');assert.equal(calls[0].op,'group-members');assert.match(section.textContent,/200 reported by NSX/);assert.match(section.textContent,/Partial/);section.querySelector('.evidence-object-link').click();assert.deepEqual({...query},{path:'/vm/one',view:'vms'});tab(d,'IP addresses',section.querySelector('.evidence-category-tabs'));await tick();assert.equal(calls.at(-1).section,'ip-addresses');assert.match(section.textContent,/membership is not established/);assert.equal(d.querySelector('#detail-dialog').classList.contains('evidence-firewall-wide'),false);dom.window.close();
});
test('member responses cannot replace a newly selected group and remembered Members loads on reopen',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),dialog=d.getElementById('detail-dialog');d.querySelector('main').innerHTML='<div data-server-table="all-groups"></div>';let resolve;w.reportData=async()=>new Promise(r=>resolve=r);w.workspaceEvidence(body,'First',{kind:'group',path:'/infra/groups/first'});tab(d,'Members');await tick();const old=body.querySelector('#evidence-members .evidence-table-content');dialog.open=false;body.replaceChildren();w.reportData=async()=>({items:[],status:'not_collected'});w.workspaceEvidence(body,'Second',{kind:'group',path:'/infra/groups/second'});dialog.open=true;resolve({items:[{name:'Stale VM',identity:'x'}],status:'complete'});await tick();assert.doesNotMatch(old.textContent,/Stale VM/);assert.match(body.querySelector('#evidence-members').textContent,/not collected in this snapshot/);dom.window.close();
});
test('a group member VM link opens the current paged VM renderer and Back restores the group',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');d.querySelector('main').innerHTML='<div data-server-table="all-groups"></div>';w.reportData=async(_,q)=>q.op==='resolve'?{view:'vms',id:8}:q.op==='vm-relationships'?(q.section==='summary'?{data:{...summary,resolved_group_count:1,resolved_membership_status:'complete',membership_evidence_version:1}}:{items:[{name:'Web',path:'/infra/groups/web',status:'complete'}],status:'complete'}):{items:[{name:'VM',identity:'id',vm_path:'/vm'}],status:'complete'};w.workspaceEvidence(body,'Web',{kind:'group',path:'/infra/groups/web'});tab(d,'Members');await tick();body.querySelector('.evidence-object-link').click();await tick();await tick();assert.equal(body.querySelector('#evidence-members'),null);tab(d,'References');await tick();assert.match(body.querySelector('#evidence-references').textContent,/Web/);d.querySelector('.evidence-back').click();assert.match(body.querySelector('#evidence-members').textContent,/VM/);dom.window.close();
});
test('member timestamps use shared plain-text formatting without exposing time markup',async()=>{
 const dom=setup(),w=dom.window,d=w.document;w.workspaceTime=()=>'<time>formatted</time>';w.workspaceTimeText=()=> '05 Nov 2026 08:00 CET';const table=w.evidenceTable({title:'Members',columns:[{label:'Member',cell:i=>i.name}],load:async()=>({items:[],status:'partial',checked_at:'2026-11-05T07:00:00Z'})});d.getElementById('detail-body').append(table.panel);table.show();await tick();assert.match(table.panel.textContent,/Collected 05 Nov 2026 08:00 CET/);assert.doesNotMatch(table.panel.textContent,/<time/);assert.equal(w.evidenceBadge('partial').dataset.tone,'warning');dom.window.close();
});
test('member tables distinguish configured IPs, inapplicable categories and budget-limited evidence',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');
 for(const data of [
  {status:'configured',items:[{name:'192.0.2.0/24'}],notes:['Configured IP entries; not an expanded resolved list.']},
  {status:'not_applicable',items:[],notes:['Simple explicit IP definition.']},
  {status:'not_collected',items:[],notes:['Detailed member collection budget reached; missing members remain unknown.']}
 ]){
  const table=w.evidenceTable({title:'IP members',columns:[{label:'Member',cell:i=>i.name}],load:async()=>data});body.append(table.panel);table.show();await tick();
  if(data.status==='configured'){assert.match(table.panel.textContent,/Configured/);assert.match(table.panel.textContent,/192.0.2.0\/24/);assert.match(table.panel.textContent,/not an expanded resolved list/);}
  if(data.status==='not_applicable'){assert.match(table.panel.textContent,/Not applicable/);assert.match(table.panel.textContent,/does not apply/);assert.doesNotMatch(table.panel.textContent,/membership is not established/);}
  if(data.status==='not_collected'){assert.match(table.panel.textContent,/budget reached/);assert.match(table.panel.textContent,/see the collection notes/);assert.doesNotMatch(table.panel.textContent,/Run a new collection/);}
 }
 dom.window.close();
});

function currentResult(items=[],extra={}){return {items,status:'complete',saved_count:items.length,source:'current_nsx',checked_at:'2026-11-05T12:00:00Z',stale:false,can_refresh:true,...extra};}
function autoVm(dom){dom.window.document.getElementById('detail-dialog').membershipInitialSource=null;}
const four=Array.from({length:4},(_,i)=>({path:'/infra/groups/'+i,name:'Actual group '+i}));

test('IP/subnet evidence is lazy, labelled and separate from confirmed groups and is retained in rule inspection',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];
 const edge={path:'/infra/groups/subnet',name:'Subnet',reference_basis:'ip_subnet',membership_source:'saved_snapshot',
  ip_checked_at:'2026-11-11T08:00:00Z',ip_matches:[{ip:'192.0.2.10',entry:'192.0.2.0/24',via_groups:[]}]};
 w.fetch=async()=>{throw Error('Saved IP references must not call current NSX');};
 w.reportData=async(_,q)=>{calls.push(q.section);if(q.section==='resolved_groups')return {items:[],status:'complete'};
  if(q.section==='ip_groups')return {items:[edge],status:'partial',saved_count:1,source:'saved_snapshot'};
  if(q.section==='confirmed_rules')return {items:[{...rule,ip_matches:edge.ip_matches,reference_reason:'Saved IP/subnet configuration match; not confirmed VM membership.'}],status:'complete'};
  if(q.section==='confirmed_rule')return {details:{services:['ANY'],via_groups:[edge.path],membership_evidence:{[edge.path]:edge}}};
  return {items:[]};};
 await w.showVmRelationships(body,null,7,{...summary,resolved_group_count:0,resolved_membership_status:'complete'});
 tab(d,'References');await tick();assert.equal(calls.includes('ip_groups'),false);assert.doesNotMatch(d.querySelector('#evidence-references tbody').textContent,/Subnet/);
 tab(d,'IP/subnet matches',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 assert.match(body.textContent,/192.0.2.10 → 192.0.2.0\/24/);assert.ok(calls.includes('ip_groups'));
 assert.match(d.querySelector('#evidence-references .evidence-table-section:not([hidden]) .evidence-table-status').textContent,/1 match; total unknown/);
 tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 d.querySelector('[aria-label^="Inspect references"]').click();await tick();await tick();
 assert.match(d.querySelector('.evidence-rule-inspection').textContent,/IP\/subnet match/);
 assert.match(d.querySelector('.evidence-rule-inspection').textContent,/192.0.2.10 → 192.0.2.0\/24/);
 assert.equal(d.querySelectorAll('script').length,0);dom.window.close();
});

test('network-aware VM references retain saved attachment provenance and keep direct counts separate',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];autoVm(dom);
 const edge={path:'/infra/groups/network',name:'Network group',membership_basis:'network_attachment',via:[{interface_id:'vif-1',port_path:'/infra/segments/s/ports/p',segment_path:'/infra/segments/s'}]};
 w.fetch=async()=>{throw Error('Saved network evidence must not call current NSX');};
 w.reportData=async(_,q)=>{calls.push(q.section);return {items:q.section==='resolved_groups'||q.section==='network_groups'?[edge]:[{...rule,reference_reason:'Verified VM interface attachment'}],status:'complete',source:'saved_snapshot'};};
 await w.showVmRelationships(body,null,7,{...summary,network_membership_version:1,direct_group_count:0,network_group_count:1,network_membership_status:'complete',resolved_membership_status:'complete'});
 assert.match(d.querySelector('#evidence-summary').textContent,/0 direct VM groups · 1 network groups/);
 tab(d,'References');await tick();assert.match(d.querySelector('#evidence-references').textContent,/Interface attachment/);
 assert.match(d.querySelector('#evidence-references').textContent,/vif-1/);
 tab(d,'Network links (saved)',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 assert.ok(calls.includes('network_groups'));assert.match(d.querySelector('#evidence-references').textContent,/\/infra\/segments\/s\/ports\/p/);
 tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 assert.match(d.querySelector('.evidence-firewall-table').textContent,/Verified VM interface attachment/);
 dom.window.close();
});

test('network groups show direct VMs and attached VMs as separate saved categories',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];
 d.querySelector('main').innerHTML='<div data-server-table="all-groups"></div>';
 w.reportData=async(_,q)=>{calls.push(q.section);return {items:q.section==='network-virtual-machines'?[{name:'Attached VM',identity:'vm',vm_path:'/vm',via:[{interface_id:'vif',port_path:'/infra/segments/s/ports/p',segment_path:'/infra/segments/s'}]}]:[],status:'complete',saved_count:q.section==='network-virtual-machines'?1:0};};
 w.workspaceEvidence(body,'Network group',{kind:'group',path:'/infra/groups/network',network_membership_version:1});
 tab(d,'Members');await tick();assert.match(d.querySelector('#evidence-members').textContent,/Direct VM members/);
 tab(d,'Attached VMs',d.querySelector('#evidence-members .evidence-category-tabs'));await tick();
 assert.ok(calls.includes('network-virtual-machines'));assert.match(d.querySelector('#evidence-members').textContent,/Attached VM/);
 assert.match(d.querySelector('#evidence-members').textContent,/Interface vif/);dom.window.close();
});

test('a complete saved VM association list opens without current NSX requests',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');autoVm(dom);
 const calls=[];w.fetch=async()=>{throw Error('Complete saved evidence must not check current NSX automatically');};
 w.reportData=async(_,q)=>{calls.push(q.section);return {items:q.section==='resolved_groups'?four:[rule],status:'complete',source:'saved_snapshot'};};
 await w.showVmRelationships(body,null,7,{...summary,resolved_group_count:4,resolved_membership_status:'complete'});
 tab(d,'References');await tick();assert.equal(d.querySelector('.evidence-membership-source select').value,'saved');
 assert.equal(d.querySelector('#evidence-references tbody').children.length,4);
 tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 assert.ok(calls.includes('confirmed_rules'));assert.match(d.querySelector('#evidence-references').textContent,/ALLOW|DROP/);
 dom.window.close();
});

test('VM References lazily refreshes missing associations once and keeps snapshot evidence separate',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[],savedCalls=[];autoVm(dom);
 w.reportData=async(_,q)=>{savedCalls.push(q.section);return q.section==='resolved_groups'?{items:four.slice(0,2),status:'partial'}:responses[q.section];};
 w.fetch=async(url,options)=>{calls.push({url:String(url),method:options.method,body:options.body});return {ok:true,json:async()=>options.method==='POST'?currentResult(four):currentResult([],{status:'not_collected',stale:true})};};
 await w.showVmRelationships(body,null,7,{...summary,resolved_group_count:2,resolved_membership_status:'partial'});
 assert.equal(calls.length,0);assert.equal(d.querySelector('#evidence-members'),null);assert.doesNotMatch(d.querySelector('#evidence-summary').textContent,/Groups referencing tags/);
 tab(d,'References');await tick();await tick();assert.deepEqual(calls.map(c=>c.method),['GET','POST']);assert.equal(calls[1].body.get('refresh_mode'),'if_needed');
 assert.equal(d.querySelector('#evidence-references tbody').children.length,4);assert.match(d.querySelector('#evidence-references').textContent,/Current NSX/);assert.match(d.querySelector('#evidence-references').textContent,/4 confirmed/);
 const select=d.querySelector('.evidence-membership-source select');select.value='saved';select.dispatchEvent(new w.Event('change'));await tick();assert.equal(d.querySelector('#evidence-references tbody').children.length,2);
 select.value='current';select.dispatchEvent(new w.Event('change'));await tick();assert.equal(calls.length,2);assert.equal(d.querySelector('#evidence-references tbody').children.length,4);
 assert.deepEqual([...d.querySelectorAll('#evidence-references [role=tab]')].map(b=>b.textContent),['Groups','Firewall rules','IP/subnet matches']);assert.equal(savedCalls.some(s=>['related_groups','related_rules'].includes(s)),false);
 dom.window.close();
});

test('fresh cache is reused across categories, explicit Refresh makes a new check, tags stay in Summary',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];autoVm(dom);
 w.reportData=async(_,q)=>responses[q.section];w.fetch=async(url,opts)=>{calls.push(opts.method);return {ok:true,json:async()=>String(url).includes('section=firewall')?currentResult([rule]):currentResult(four)};};
 await w.showVmRelationships(body,null,7,summary);assert.equal(calls.length,0);
 const assigned=d.querySelector('#evidence-summary details');assert.equal(assigned.open,false);assigned.open=true;await new Promise(r=>setTimeout(r,5));assert.match(assigned.textContent,/ApplicationPortal/);
 tab(d,'References');await tick();tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();assert.deepEqual(calls,['GET','GET']);assert.match(d.querySelector('.evidence-firewall-table').textContent,/DROP/);
 tab(d,'Groups',d.querySelector('#evidence-references .evidence-category-tabs'));body.querySelector('.evidence-membership-source button').click();await tick();await tick();assert.equal(calls.filter(m=>m==='POST').length,1);assert.equal(d.querySelector('#evidence-references tbody').children.length,4);
 dom.window.close();
});

test('confirmed saved firewall references never request tag candidates',async()=>{
 const dom=setup(),w=dom.window,d=w.document,calls=[];w.reportData=async(_,q)=>{calls.push(q.section);return {items:[{name:'Confirmed rule',path:'/rule'}]};};
 await w.showVmRelationships(d.getElementById('detail-body'),null,7,summary);tab(d,'References');tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 assert.ok(calls.includes('confirmed_rules'));assert.equal(calls.includes('related_rules'),false);assert.equal(calls.includes('related_groups'),false);assert.match(d.querySelector('.evidence-firewall-table').textContent,/Confirmed rule/);dom.window.close();
});

test('busy collection retains partial cache and does not repeatedly call NSX when switching categories',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];autoVm(dom);w.reportData=async(_,q)=>responses[q.section];
 w.fetch=async(url,options)=>{calls.push(options.method);return options.method==='POST'?{ok:false,json:async()=>({error:'Collection has priority.'})}:{ok:true,json:async()=>currentResult(four.slice(0,2),{status:'partial',stale:true})};};
 await w.showVmRelationships(body,null,7,summary);tab(d,'References');await tick();await tick();assert.match(body.textContent,/Collection has priority/);assert.match(d.querySelector('#evidence-references .evidence-table-status').textContent,/2 confirmed; total unknown/);
 tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();tab(d,'Groups',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();assert.equal(calls.filter(m=>m==='POST').length,1);dom.window.close();
});

test('read-only users see missing evidence rather than a false zero or repeated forbidden refresh',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];autoVm(dom);
 w.fetch=async(url,options)=>{calls.push(options.method);return {ok:true,json:async()=>currentResult([],{status:'not_collected',stale:true,can_refresh:false})};};
 await w.showVmRelationships(body,null,7,summary);tab(d,'References');await tick();assert.deepEqual(calls,['GET']);assert.equal(body.querySelector('.evidence-membership-source button').disabled,true);assert.match(d.querySelector('#evidence-references').textContent,/Membership is not established|membership is not established/);assert.doesNotMatch(d.querySelector('#evidence-references').textContent,/NSX returned no group/);dom.window.close();
});

test('complete zero is distinguished from unavailable membership',async()=>{
 for(const status of ['complete','unavailable']){const dom=setup(),w=dom.window,d=w.document;autoVm(dom);w.fetch=async()=>({ok:true,json:async()=>currentResult([],{status,can_refresh:false})});await w.showVmRelationships(d.getElementById('detail-body'),null,7,summary);tab(d,'References');await tick();const text=d.querySelector('#evidence-references').textContent;if(status==='complete')assert.match(text,/NSX returned no group associations/);else assert.match(text,/membership is not established/);dom.window.close();}
});

test('current lookup errors keep saved evidence selectable and late results cannot replace another VM',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');autoVm(dom);w.reportData=async()=>({items:[{name:'Saved group'}],status:'partial'});
 w.fetch=async()=>({ok:false,json:async()=>({error:'Unavailable'})});await w.showVmRelationships(body,null,7,summary);tab(d,'References');await tick();assert.match(d.querySelector('#evidence-references .evidence-table-content').textContent,/Unavailable/);
 const select=d.querySelector('.evidence-membership-source select');select.value='saved';select.dispatchEvent(new w.Event('change'));await tick();assert.match(d.querySelector('#evidence-references').textContent,/Saved group/);
 let resolve;w.fetch=async()=>new Promise(r=>resolve=r);body.querySelector('.evidence-membership-source button').click();body.evidenceGeneration={};body.replaceChildren();resolve({ok:true,json:async()=>currentResult(four)});await tick();assert.equal(body.textContent,'');dom.window.close();
});

test('switching reference categories during initial lookup shares one automatic refresh',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];autoVm(dom);let resolveCache;
 w.fetch=async(url,opts)=>{calls.push(opts.method);if(calls.length===1)return new Promise(r=>resolveCache=r);return {ok:true,json:async()=>currentResult(opts.method==='POST'?four:[rule])};};
 await w.showVmRelationships(body,null,7,summary);tab(d,'References');tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));
 resolveCache({ok:true,json:async()=>currentResult([],{status:'not_collected',stale:true})});await tick();await tick();
 assert.equal(calls.filter(method=>method==='POST').length,1);assert.match(d.querySelector('.evidence-firewall-table').textContent,/DROP/);tab(d,'Groups',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();assert.equal(d.querySelector('#evidence-references tbody').children.length,4);dom.window.close();
});

test('leaving a VM before cache metadata returns prevents the automatic NSX check',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),methods=[];autoVm(dom);let resolveCache;
 w.fetch=async(url,opts)=>{methods.push(opts.method);return new Promise(r=>resolveCache=r);};await w.showVmRelationships(body,null,7,summary);tab(d,'References');body.evidenceGeneration={};body.replaceChildren();resolveCache({ok:true,json:async()=>currentResult([],{status:'not_collected',stale:true})});await tick();assert.deepEqual(methods,['GET']);assert.equal(body.textContent,'');dom.window.close();
});

test('stale positive groups display before NSX responds, then update groups and firewall coverage',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');autoVm(dom);let finishRefresh,posts=0;
 w.fetch=async(url,opts)=>{
  if(opts.method==='POST'){posts++;return new Promise(resolve=>finishRefresh=resolve);}
  return {ok:true,json:async()=>String(url).includes('section=firewall')?currentResult([rule],{stale:true}):currentResult(four,{stale:true})};
 };
 await w.showVmRelationships(body,null,7,summary);tab(d,'References');await tick();
 assert.equal(d.querySelector('#evidence-references tbody').children.length,4);assert.match(body.textContent,/Stale observation/);assert.match(body.textContent,/Checking current NSX membership/);assert.equal(posts,1);
 tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();assert.match(d.querySelector('.evidence-firewall-table').textContent,/DROP/);
 finishRefresh({ok:true,json:async()=>currentResult(four.slice(0,3))});await tick();await tick();
 tab(d,'Groups',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();assert.equal(d.querySelector('#evidence-references tbody').children.length,3);assert.doesNotMatch(d.querySelector('#evidence-references .evidence-table-section:not([hidden])').textContent,/Stale observation/);assert.equal(posts,1);dom.window.close();
});

test('background refresh failure keeps stale groups visible and a late result cannot touch another drawer',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');autoVm(dom);let finishRefresh;
 w.fetch=async(url,opts)=>opts.method==='POST'?new Promise(resolve=>finishRefresh=resolve):{ok:true,json:async()=>currentResult(four,{stale:true})};
 await w.showVmRelationships(body,null,7,summary);tab(d,'References');await tick();
 finishRefresh({ok:false,json:async()=>({error:'NSX unavailable; cached evidence retained.'})});await tick();assert.equal(d.querySelector('#evidence-references tbody').children.length,4);assert.match(body.textContent,/NSX unavailable/);assert.match(body.textContent,/Stale observation/);
 body.querySelector('.evidence-membership-source button').click();body.evidenceGeneration={};body.replaceChildren();finishRefresh({ok:true,json:async()=>currentResult([])});await tick();assert.equal(body.textContent,'');dom.window.close();
});

test('group refresh uses the returned first page without an extra app request',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];
 body.evidenceGeneration={};const source=w.membershipSource(body,{kind:'group',path:'/infra/domains/default/groups/web'});body.append(source.panel);w.fetch=async(url,opts)=>{calls.push(opts.method);return {ok:true,json:async()=>currentResult(four)};};
 body.querySelector('button').click();await tick();const result=await source.load('',0,()=>{throw Error('Wrong evidence source');});assert.equal(result.items.length,4);assert.deepEqual(calls,['POST']);dom.window.close();
});

function groupChunk(page=0,extra={}){const loaded=extra.loaded_count??100;return {items:Array.from({length:25},(_,i)=>({external_id:'vm-'+String(page*25+i).padStart(4,'0'),name:'VM '+(page*25+i)})),page,status:'partial',source:'current_nsx',stale:false,loaded_count:loaded,saved_count:loaded,reported_count:4075,paging_session:'session-one',can_continue:true,can_refresh:true,has_next:true,...extra};}

test('group paging loads 100 once, renders 25, fetches another chunk only at its boundary and keeps Back stable',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),calls=[];body.evidenceGeneration={};
 const source=w.membershipSource(body,{kind:'group',path:'/infra/domains/default/groups/web'});body.append(source.panel);
 w.fetch=async(url,opts)=>{const query=new URL(url).searchParams,page=Number(opts.method==='POST'?opts.body.get('page'):query.get('page'));calls.push({method:opts.method,page,session:query.get('paging_session'),mode:opts.body?.get('refresh_mode')});return {ok:true,json:async()=>opts.method==='POST'?(opts.body.get('refresh_mode')==='continue'?groupChunk(page,{loaded_count:200}):groupChunk()):groupChunk(page,{items:page===4?[]:groupChunk(page).items,needs_more:page===4})};};
 const table=w.evidenceTable({title:'Group VMs',columns:[{label:'VM',cell:i=>i.name}],load:page=>source.load('',page,()=>({items:[]}))});source.register(table);body.append(table.panel);
 source.panel.querySelector('button').click();await tick();await tick();assert.equal(table.panel.querySelectorAll('tbody tr').length,25);assert.match(table.panel.textContent,/100 loaded · 4075 reported by NSX/);assert.deepEqual(calls.map(c=>c.method),['POST']);
 for(let page=1;page<=4;page++){table.panel.querySelector('[aria-label="Next group vms"]').click();await tick();await tick();assert.equal(table.panel.querySelector('tbody tr').textContent,'VM '+page*25);}
 assert.equal(calls.filter(c=>c.method==='POST').length,2);const continuation=calls.find(c=>c.mode==='continue');assert.equal(continuation.page,4);assert.equal(continuation.session,'session-one');
 table.panel.querySelector('[aria-label="Previous group vms"]').click();await tick();assert.equal(table.panel.querySelector('tbody tr').textContent,'VM 75');assert.equal(calls.filter(c=>c.method==='POST').length,2);dom.window.close();
});

test('failed next chunk keeps current rows and navigation available, Retry reuses the session',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');body.evidenceGeneration={};let failed=true,posts=0;
 const source=w.membershipSource(body,{kind:'group',path:'/infra/domains/default/groups/web'});body.append(source.panel);
 w.fetch=async(url,opts)=>{const page=Number(opts.method==='POST'?opts.body.get('page'):new URL(url).searchParams.get('page'));if(opts.method==='POST'&&opts.body.get('refresh_mode')==='continue'){posts++;if(failed)return {ok:false,json:async()=>({error:'NSX next page unavailable; retry.'})};return {ok:true,json:async()=>groupChunk(page,{loaded_count:200})};}return {ok:true,json:async()=>groupChunk(page,{items:page===4?[]:groupChunk(page).items,needs_more:page===4})};};
 const table=w.evidenceTable({title:'Group VMs',columns:[{label:'VM',cell:i=>i.name}],load:page=>source.load('',page,()=>({items:[]}))});source.register(table);body.append(table.panel);source.panel.querySelector('button').click();await tick();
 for(let i=0;i<4;i++){table.panel.querySelector('[aria-label="Next group vms"]').click();await tick();await tick();}
 assert.equal(table.panel.querySelector('tbody tr').textContent,'VM 75');assert.match(table.panel.textContent,/showing the previous loaded page/);assert.equal(table.panel.querySelector('[aria-label="Previous group vms"]').disabled,false);
 failed=false;[...table.panel.querySelectorAll('button')].find(b=>b.textContent==='Retry').click();await tick();await tick();assert.equal(table.panel.querySelector('tbody tr').textContent,'VM 100');assert.equal(posts,2);dom.window.close();
});

test('read-only group paging cannot silently start an NSX continuation',async()=>{
 const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),methods=[];body.evidenceGeneration={};const source=w.membershipSource(body,{kind:'group',path:'/infra/domains/default/groups/web'});body.append(source.panel);source.panel.querySelector('select').value='current';w.fetch=async(url,opts)=>{methods.push(opts.method);return {ok:true,json:async()=>groupChunk(4,{items:[],needs_more:true,can_refresh:false})};};
 await assert.rejects(source.load('',4,()=>{}),/An operator must load more/);assert.deepEqual(methods,['GET']);dom.window.close();
});
test('rule fields mark matches in place, keep negation and reveal more entries on request',()=>{const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body'),opened=[];w.openRelatedEvidence=async(_,query)=>opened.push(query.path);
 const entry=(n,extra={})=>({path:'/infra/groups/g'+n,name:'Group '+n,known:true,matched:false,...extra});
 const sources={recorded:true,any:false,excluded:true,total:30,origin:'rule',items:[entry(0,{matched:true}),...Array.from({length:24},(_,i)=>entry(i+1))]};
 const cell=w.firewallRuleFieldCell(body,sources);d.body.append(cell);
 assert.equal(cell.querySelector('.evidence-status').textContent,'NOT');
 assert.equal(cell.querySelectorAll('.evidence-field-entry').length,3);
 assert.equal(cell.querySelector('[data-matched=true]').textContent,'Group 0✓ (matched)');
 const more=cell.querySelector('.evidence-more');assert.equal(more.textContent,'+27 more');more.click();
 assert.equal(cell.querySelectorAll('.evidence-field-entry').length,25);assert.match(cell.textContent,/5 more in the rule record/);assert.equal(cell.querySelector('.evidence-more'),null);
 cell.querySelector('.evidence-object-link').click();assert.deepEqual(opened,['/infra/groups/g0']);
 const builtin=w.firewallRuleFieldCell(body,{recorded:true,any:false,total:1,items:[{path:'/infra/services/HTTPS',name:'HTTPS',known:false,matched:false}]});assert.equal(builtin.querySelector('button'),null);assert.equal(builtin.textContent,'HTTPS');
 assert.equal(w.firewallRuleFieldCell(body,{recorded:true,any:true,total:0,items:[]},{none:'DFW'}).textContent,'DFW');
 assert.equal(w.firewallRuleFieldCell(body,{recorded:true,any:false,origin:'policy',total:1,items:[entry(9)]}).textContent,'From policyGroup 9');
 assert.equal(w.firewallRuleFieldCell(body,undefined).textContent,'Not recorded');dom.window.close();});
test('context profiles column appears only when the snapshot uses profiles',async()=>{const dom=setup(),w=dom.window,d=w.document;const profiled={...rule,show_context_profiles:true,rule_fields:{context_profiles:{recorded:true,any:false,total:1,items:[{path:'/infra/context-profiles/SSL',name:'SSL',known:false,matched:false}]}}};
 w.reportData=async(_,q)=>q.section==='confirmed_rules'?{items:[profiled],status:'complete'}:responses[q.section];await w.showVmRelationships(d.getElementById('detail-body'),null,7,summary);tab(d,'References');tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 const table=d.querySelector('.evidence-firewall-table');assert.deepEqual([...table.querySelectorAll('th')].map(th=>th.textContent).slice(5,8),['Services','Context profiles','Applied to']);assert.match(table.textContent,/SSL/);dom.window.close();});
test('firewall references group by NSX category and policy, repeating headings on later pages',async()=>{const dom=setup(),w=dom.window,d=w.document;
 const order=(category,policy,extra={})=>({category,category_known:category!=='Custom',policy,policy_key:'/p/'+policy,policy_sequence:10,rule_sequence:1,category_count:2,policy_count:1,category_continued:false,policy_continued:false,...extra});
 const pages=[[{...rule,name:'eth',nsx_order:order('Ethernet','L2')},{...rule,name:'app-1',nsx_order:order('Application','Web',{category_count:3,policy_count:2})},{...rule,name:'app-2',nsx_order:order('Application','Web',{rule_sequence:2,policy_continued:true,category_continued:true})}],
  [{...rule,name:'app-3',nsx_order:order('Application','Web',{policy_sequence:null,rule_sequence:null,category_count:3,policy_count:2,category_continued:true,policy_continued:true})},{...rule,name:'odd',nsx_order:order('Custom','Other',{category_count:1})}]];
 w.reportData=async(_,q)=>q.section==='confirmed_rules'?{items:pages[q.page],status:'complete',has_next:q.page===0}:responses[q.section];await w.showVmRelationships(d.getElementById('detail-body'),null,7,summary);tab(d,'References');tab(d,'Firewall rules',d.querySelector('#evidence-references .evidence-category-tabs'));await tick();
 let table=d.querySelector('.evidence-firewall-table');const headings=()=>[...table.querySelectorAll('.evidence-group-row')].map(row=>row.dataset.level+':'+row.textContent);
 assert.deepEqual(headings(),['category:Ethernet2 references','policy:Policy: L2Policy sequence 101 reference','category:Application3 references','policy:Policy: WebPolicy sequence 102 references']);
 const header=table.querySelector('.evidence-group-row th');assert.equal(header.scope,'rowgroup');assert.equal(header.colSpan,11);assert.equal(table.tBodies.length,3);
 assert.deepEqual([...table.querySelectorAll('tbody tr:not(.evidence-group-row) td:first-child')].map(td=>td.textContent),['Ethernet','Application','Application']);
 assert.match(table.querySelector('.evidence-rule-name small').textContent,/Rule sequence 1/);
 d.querySelector('[aria-label="Next firewall references"]').click();await tick();table=d.querySelector('.evidence-firewall-table');
 assert.deepEqual(headings(),['category:Application3 referencescontinued from the previous page','policy:Policy: WebOrder not recorded2 referencescontinued from the previous page','category:Category: Custom1 reference','policy:Policy: OtherPolicy sequence 101 reference']);
 assert.match(table.textContent,/Rule sequence not recorded/);dom.window.close();});

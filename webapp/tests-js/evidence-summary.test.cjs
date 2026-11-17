const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../static/application.js'),'utf8');
function setup(){
  const dom=new JSDOM('<main></main><dialog id="detail-dialog"><div class="dialog-heading"><h2 id="detail-title">prd-web-01 — VM relationships</h2><button id="close-details">Close</button></div><div id="detail-body"></div></dialog>',{runScripts:'outside-only',url:'https://example.test/snapshots/s1/'});
  dom.window.eval(source);return dom;
}
const summary=d=>d.querySelector('#evidence-summary');

test('VM summary shows labels, per-kind evidence and never shows unknown as zero',()=>{
  const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');
  w.workspaceEvidence(body,d.getElementById('detail-title'),{inventory_type:'Virtual machine',name:'prd-web-01',power_state:'VM_RUNNING',tag_count:2,referenced_by:[],
    direct_group_count:3,direct_membership_status:'complete',network_group_count:4,network_membership_status:'partial',resolved_group_count:7,resolved_membership_status:'unknown'});
  const panel=summary(d);
  assert.deepEqual([...panel.querySelectorAll('.evidence-badges .pill')].map(p=>p.textContent),['Running']);
  assert.doesNotMatch(panel.textContent,/VM_RUNNING|VM RUNNING/);
  const tiles=[...panel.querySelectorAll('.evidence-strip > div')].map(t=>t.textContent);
  assert.deepEqual(tiles,['Direct VM groups3Complete','Interface groups4Partial · confirmed only','All saved groups—Unknown']);
  assert.equal(d.getElementById('detail-title').textContent,'prd-web-01');
  assert.equal(d.querySelector('#detail-dialog .evidence-context').textContent,'Virtual machine');
  dom.window.close();
});

test('rule and group summaries use state labels and turn qualification lines into a readiness card',()=>{
  const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');
  w.workspaceEvidence(body,'Rule',{action:'DROP',disabled:false,hit_status:'zero_hits',hit_count:0,scope:['ANY'],category:'Emergency'});
  let panel=summary(d);
  assert.deepEqual([...panel.querySelectorAll('.evidence-badges .pill')].map(p=>p.textContent),['Drop','Enabled','Zero recorded hits']);
  const rows=Object.fromEntries([...panel.querySelectorAll('.evidence-rows dt')].map(dt=>[dt.textContent,dt.nextElementSibling.textContent]));
  assert.equal(rows['Rule Applied to'],'Any');assert.equal(rows.Category,'Emergency');
  body.innerHTML='<p class="row-qualification"><span class="badge gray">unused: Eligible for review</span><br><small>33 / 21 days · 10 observations</small></p><ul class="notes"><li>VM-only definition.</li></ul>';
  w.workspaceEvidence(body,'Group',{kind:'group',membership:'empty',usage:'unknown',referenced_by:[],finding_assessments:[{label:'Eligible for review'},{label:'Eligible for review'}]});
  panel=summary(d);
  assert.deepEqual([...panel.querySelectorAll('.evidence-badges .pill')].map(p=>p.textContent),['Empty','Usage unknown','Eligible for review']);
  const cards=[...panel.querySelectorAll('.evidence-card h3')].map(h=>h.textContent);
  assert.deepEqual(cards,['Details','Review readiness','Notes']);
  assert.match(panel.textContent,/33 days observed, 21 required · 10 observations/);
  assert.match(panel.textContent,/VM-only definition\./);
  dom.window.close();
});

test('rule cards show saved fields with NOT and From policy, counters, and replace the old rule block',()=>{
  const dom=setup(),w=dom.window,d=w.document,body=d.getElementById('detail-body');
  body.innerHTML='<details data-evidence-section="overview"><summary>Counter &amp; rule details</summary><p><code>/infra/old</code></p></details><h3>Confirmed empty group references</h3><ul><li>Old list</li></ul><p>Last observed positive count: Unavailable</p><p>Saved audit observation, not the last packet time. Traffic and resets between audits may be missed.</p>';
  w.workspaceEvidence(body,'Rule',{action:'ALLOW',disabled:false,hit_status:'traffic_recorded',hit_count:12,
    source_groups:['/infra/domains/default/groups/web'],destination_groups:['ANY'],services:['/infra/services/HTTPS'],scope:['ANY'],
    configuration:{rule:{sources_excluded:true,destinations_excluded:false,direction:'IN_OUT',logged:false},policy:{scope:['/infra/domains/default/groups/app']}},
    statistics:[{enforcement_point:'/infra/sites/default/enforcement-points/default',hit_count:12,packet_count:30}],
    empty_group_references:[{name:'EMPTY-ONE',path:'/infra/domains/default/groups/EMPTY-ONE',field:'Source'}]});
  const panel=summary(d),fields={};
  for(const dt of panel.querySelectorAll('.rule-field-rows dt'))fields[dt.textContent]=dt.nextElementSibling.textContent;
  assert.equal(fields.Sources,'NOTweb');
  assert.equal(fields.Destinations,'Any');
  assert.equal(fields.Services,'HTTPS');
  assert.equal(fields['Applied to'],'From policyapp','A policy Applied To takes precedence over the rule scope.');
  assert.equal(fields.Logging,'Off');
  assert.deepEqual([...panel.querySelectorAll('.evidence-card h3')].map(h=>h.textContent),['Details','Rule fields','Counters','Empty group references']);
  assert.match(panel.textContent,/Enforcement point default12 hits · 30 packets/);
  assert.match(panel.textContent,/Unavailable — no saved positive snapshot/);
  assert.doesNotMatch(panel.textContent,/Old list|\/infra\/old|Counter & rule details/);
  assert.equal(panel.querySelector('.rule-field-rows .evidence-value[title]')?.title,'/infra/domains/default/groups/web');
  dom.window.close();
});

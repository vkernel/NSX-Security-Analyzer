const {test} = require('node:test'), assert = require('node:assert/strict'), {JSDOM} = require('jsdom'), fs = require('node:fs'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/rule-order.js'), 'utf8');
const order = (category, policy, sequence, extra = {}) => ({category, category_known: true, policy, policy_key: '/p/' + policy, policy_sequence: 10, rule_sequence: sequence, category_count: 2, policy_count: 2, ...extra});
const field = {recorded: true, any: true, items: [], total: 0};
const RULES = {items: [
  {name: 'Quarantine', ordinal: 5, rule_id: 3001, action: 'DROP', disabled: false, nsx_order: order('Emergency', 'Isolation', 10, {category_count: 1, policy_count: 1}), rule_fields: {sources: field, destinations: field, services: field, applied_to: field}},
  {name: 'Web to app', ordinal: 6, rule_id: 3002, action: 'ALLOW', disabled: true, nsx_order: order('Application', 'App', 10), rule_fields: {sources: field, destinations: field, services: field, applied_to: field}},
], page: 0, has_next: false, total: 2, page_size: 50};
const POLICIES = {items: [{ordinal: 9, name: 'App', path: '/p/App', category: 'Application', category_known: true, sequence: 100, stateful: true, saved_rules: 2, disabled_rules: 1, rule_count: 2, status: 'has_rules', category_count: 1, applied_to: {recorded: true, any: true, items: []}}], page: 0, has_next: false, page_size: 100};

async function fixture({hash = '#dfw-rules', layout = null} = {}) {
  const dom = new JSDOM(`<body><main><div class="report-view-selector"><nav class="inventory-filter-chips"></nav></div><div class="report-content">
    <section id="dfw-rules"><h2>Rules</h2><div class="table-widget"><div class="table-tools"></div><table></table></div></section>
    <section id="dfw-policies"><div class="table-widget"><div class="table-tools"></div></div></section></div></main></body>`, {url: 'https://example.test/snapshots/s1/?browse=1' + hash, runScripts: 'outside-only'});
  const w = dom.window, calls = [];
  if (layout) w.localStorage.setItem('rules-layout', layout);
  w.matchMedia = () => ({matches: true});
  w.Element.prototype.scrollIntoView = function () {};
  w.reportData = async (_, params) => { calls.push(params); return params.op === 'policy-order' ? POLICIES : RULES; };
  w.firewallRuleFieldCell = (_, value) => { const node = w.document.createElement('div'); node.textContent = value?.any ? 'Any' : 'x'; return node; };
  w.evidenceBadge = value => { const node = w.document.createElement('span'); node.textContent = value; return node; };
  w.eval(source);
  await new Promise(resolve => setTimeout(resolve, 0));
  await new Promise(resolve => setTimeout(resolve, 0));
  return {dom, w, d: w.document, calls};
}
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

test('rules render in NSX order with category and policy headings and hide the sortable table', async () => {
  const {w, d, calls} = await fixture();
  assert.ok(calls.some(call => call.op === 'rule-order' && call.page === 0));
  const headings = [...d.querySelectorAll('#dfw-rules .rule-order-heading')].map(row => row.dataset.level + ':' + row.querySelector('strong').textContent);
  assert.deepEqual(headings, ['category:Emergency', 'policy:Isolation', 'category:Application', 'policy:App']);
  assert.ok(d.querySelector('#dfw-rules').classList.contains('rule-order-active'));
  assert.equal(d.querySelectorAll('tr.is-disabled').length, 1);
  assert.equal(d.querySelector('.rule-order-table').dataset.serverFooter, 'true');
  assert.match(d.querySelector('.rule-order-status').textContent, /2 rules in this snapshot/);
  w.close();
});

test('layout choice is remembered and the sortable table keeps its tools', async () => {
  const {w, d} = await fixture({layout: 'table'});
  assert.ok(!d.querySelector('#dfw-rules').classList.contains('rule-order-active'));
  d.querySelector('#dfw-rules .rule-order-modes button').click();
  assert.equal(w.localStorage.getItem('rules-layout'), 'nsx');
  assert.ok(d.querySelector('#dfw-rules').classList.contains('rule-order-active'));
  w.close();
});

test('filters reload from the first page and the policy filter can be removed', async () => {
  const {w, d, calls} = await fixture();
  const category = d.querySelector('.rule-order-tools select');
  category.value = 'Emergency'; category.dispatchEvent(new w.Event('change')); await settle();
  assert.equal(calls.at(-1).category, 'Emergency'); assert.equal(calls.at(-1).page, 0);
  w.ruleOrderShowPolicy('/p/App', 'App'); await settle();
  assert.equal(calls.at(-1).policy, '/p/App'); assert.equal(calls.at(-1).category, '');
  const chip = d.querySelector('.rule-order-policy');
  assert.equal(chip.hidden, false);
  chip.click(); await settle();
  assert.equal(calls.at(-1).policy, ''); assert.equal(chip.hidden, true);
  w.close();
});

test('a policy opens its panel with rules in order and Open in Rules filters the rule list', async () => {
  const {w, d, calls} = await fixture({hash: '#dfw-policies'});
  d.querySelector('.policy-order-table .rule-order-name button').click(); await settle();
  const panel = d.getElementById('policy-detail');
  assert.equal(panel.hidden, false);
  assert.equal(d.querySelector('.policy-order-table tbody tr[aria-current]') !== null, true);
  assert.equal(JSON.stringify(calls.at(-1)), JSON.stringify({op: 'rule-order', policy: '/p/App', page: 0}));
  assert.equal(panel.querySelectorAll('.policy-rules li').length, 2);
  [...panel.querySelectorAll('button')].find(button => button.textContent === 'Open in Rules').click(); await settle(); await settle();
  assert.equal(w.location.hash, '#dfw-rules');
  assert.equal(calls.at(-1).policy, '/p/App');
  w.close();
});

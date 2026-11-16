const {test} = require('node:test'), assert = require('node:assert/strict'), {JSDOM} = require('jsdom'), fs = require('node:fs'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/catalog-views.js'), 'utf8');
const SERVICES = {items: [{ordinal: 3, name: 'svc-old', path: '/infra/services/svc-old', entries: ['UDP 9100'], predefined: false, rules: 0, notes: ['Not referenced']},
  {ordinal: 4, name: 'HTTPS', path: '/infra/services/HTTPS', entries: ['TCP 443'], predefined: true, rules: null, notes: []}],
  page: 0, has_next: false, count: 2, page_size: 50, totals: {total: 2, custom: 1, predefined: 1, unreferenced: 1}};
const SCOPES = {scopes: [{name: 'app', values: 2, vms: 5, groups: 4, other: 0}, {name: 'env', values: 1, vms: 3, groups: 1, other: 0}]};
const VALUES = {scope: 'app', values: [{ordinal: 9, value: 'web', vms: 4, groups: 2, other: 0, notes: ['Case differs from Web']}], truncated: false};

async function fixture(hash) {
  const dom = new JSDOM(`<body><main><div class="report-view-selector"></div><div class="report-content">
    <section id="all-services"><div class="table-widget"><div class="table-tools"></div></div></section>
    <section id="tags-scopes"><div class="table-widget"><div class="table-tools"></div></div></section></div></main></body>`, {url: 'https://example.test/snapshots/s1/?browse=1' + hash, runScripts: 'outside-only'});
  const w = dom.window, calls = [], opened = [];
  w.reportData = async (_, params) => { calls.push(params); return params.op === 'service-list' ? SERVICES : params.scope != null ? VALUES : SCOPES; };
  w.document.addEventListener('click', event => { const button = event.target.closest('.detail-button'); if (button) opened.push({...button.dataset}); });
  w.eval(source);
  for (let i = 0; i < 4; i++) await new Promise(resolve => setTimeout(resolve, 0));
  return {w, d: w.document, calls, opened};
}
const settle = async () => { for (let i = 0; i < 3; i++) await new Promise(resolve => setTimeout(resolve, 0)); };

test('services overview shows filter cards, built-ins as not assessed and opens the shared drawer', async () => {
  const {w, d, calls, opened} = await fixture('#all-services');
  assert.ok(d.getElementById('all-services').classList.contains('rule-order-active'));
  const rows = [...d.querySelectorAll('#all-services .catalog-table tbody tr')].map(row => row.textContent);
  assert.match(rows[0], /Not referenced/); assert.match(rows[1], /Not assessed/);
  const unreferenced = [...d.querySelectorAll('.catalog-cards button')].find(card => /Not referenced/.test(card.textContent));
  unreferenced.click(); await settle();
  assert.equal(calls.filter(call => call.op === 'service-list').at(-1).view, 'unreferenced');
  d.querySelector('#all-services .catalog-table .evidence-object-link').click();
  assert.equal(opened.at(-1).evidenceRow, '3');
  w.close();
});

test('scopes overview selects the first scope, lists flagged values and switches scope', async () => {
  const {w, d, calls, opened} = await fixture('#tags-scopes');
  assert.equal(d.querySelectorAll('.scope-list li').length, 2);
  assert.equal(d.querySelector('.scope-list button[aria-pressed=true]').textContent.startsWith('app'), true);
  assert.match(d.querySelector('.scope-main tbody').textContent, /Case differs from Web/);
  d.querySelector('.scope-main .evidence-object-link').click();
  assert.equal(opened.at(-1).tagRow, '9');
  d.querySelectorAll('.scope-list li button')[1].click(); await settle();
  assert.equal(calls.at(-1).scope, 'env');
  const filter = d.querySelector('.scope-list input'); filter.value = 'zzz'; filter.dispatchEvent(new w.Event('input'));
  assert.match(d.querySelector('.scope-list ul').textContent, /No scopes match/);
  w.close();
});

test('the sortable table choice is remembered', async () => {
  const {w, d} = await fixture('#all-services');
  d.querySelector('#all-services .rule-order-modes button:nth-child(2)').click();
  assert.equal(w.localStorage.getItem('services-layout'), 'table');
  assert.ok(!d.getElementById('all-services').classList.contains('rule-order-active'));
  w.close();
});

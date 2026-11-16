const {test} = require('node:test'), assert = require('node:assert/strict'), {JSDOM} = require('jsdom'), fs = require('node:fs'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/object-references.js'), 'utf8');
const GROUP = {name: 'web', path: '/g/web', ordinal: 3, references: 2, usage: 'referenced'};
const RULES = {items: [
  {name: 'Allow web', path: '/p/a/rules/1', action: 'ALLOW', nsx_order: {rule_sequence: 10, category: 'Application', policy: 'App'},
   field_matches: [{field: 'Source', group: {path: '/g/web', name: 'web'}, excluded: false}, {field: 'Destination (excluded)', group: {path: '/g/web', name: 'web'}, excluded: true}]},
  {name: 'Via nested', path: '/p/a/rules/2', action: 'DROP', nsx_order: {rule_sequence: 20}, field_matches: [{field: 'Source', group: {path: '/g/parent', name: 'parent'}, excluded: false}]},
], has_next: false};

async function fixture() {
  const dom = new JSDOM(`<body><main><div class="report-content"><section id="object-references"><div data-object-references></div></section></div></main></body>`, {url: 'https://example.test/snapshots/s1/?browse=1#object-references', runScripts: 'outside-only'});
  const w = dom.window, calls = [], exports = [];
  w.reportData = async (_, params) => { calls.push(params); return params.op === 'reference-objects' ? {items: [GROUP]} : params.section === 'objects' ? {items: [], has_next: false} : RULES; };
  w.referenceExportControl = (identity, entries) => { exports.push({identity, entries}); const node = w.document.createElement('div'); node.className = 'reference-export-toolbar'; return node; };
  w.eval(source);
  for (let i = 0; i < 4; i++) await new Promise(resolve => setTimeout(resolve, 0));
  return {w, d: w.document, calls, exports};
}
const settle = async () => { for (let i = 0; i < 4; i++) await new Promise(resolve => setTimeout(resolve, 0)); };

test('choosing an object lists one row per rule and field, with NOT and nested references', async () => {
  const {w, d, calls, exports} = await fixture();
  assert.equal(calls[0].op, 'reference-objects'); assert.equal(calls[0].kind, 'group');
  d.querySelector('.reference-suggestion').click(); await settle();
  const firewall = calls.find(call => call.op === 'object-references' && call.section === 'firewall');
  assert.equal(firewall.full, 1); assert.equal(firewall.path, '/g/web');
  const rows = [...d.querySelectorAll('.reference-table tbody tr')].map(row => [...row.cells].map(cell => cell.textContent));
  assert.equal(rows.length, 3);
  assert.equal(rows[1][2], 'NOT Destination');
  assert.equal(rows[1][3], 'Named directly, excluded');
  assert.equal(rows[2][3], 'Through nested group parent');
  assert.equal(JSON.stringify(exports[0].identity), JSON.stringify({view: 'inventory', path: '/g/web'}));
  const chips = [...d.querySelectorAll('.inventory-filter-chips a')];
  assert.deepEqual(chips.map(chip => chip.textContent), ['All fields 3', 'Source 2', 'Destination 1']);
  chips[2].click();
  assert.equal(d.querySelectorAll('.reference-table tbody tr').length, 1);
  w.close();
});

test('switching to services searches custom services and clears the selection', async () => {
  const {w, d, calls} = await fixture();
  d.querySelectorAll('.rule-order-modes button')[1].click(); await settle();
  assert.equal(calls.at(-1).kind, 'custom_service');
  assert.equal(d.querySelector('.reference-export').hidden, true);
  w.close();
});

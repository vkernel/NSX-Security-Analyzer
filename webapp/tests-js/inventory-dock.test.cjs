const {test} = require('node:test'), assert = require('node:assert/strict'), {JSDOM} = require('jsdom'), fs = require('node:fs'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/inventory-dock.js'), 'utf8');

async function fixture({hash = '#all-groups', wide = true} = {}) {
  const dom = new JSDOM(`<body class="snapshot-page"><main class="workspace-main"><div class="report-content">
    <section id="all-groups"><table><tbody><tr id="row-a"><td><button class="row-open" title="/infra/vm/a">A</button></td><td><button class="detail-button" data-evidence-row="1">Inspect</button></td></tr>
    <tr id="row-b"><td><button class="detail-button" data-evidence-row="2">Inspect</button></td></tr></tbody></table></section>
    <section id="dfw-rules"><table><tbody><tr id="rule"><td><button class="detail-button">Inspect</button></td></tr></tbody></table></section></div></main>
    <dialog id="detail-dialog"><div class="dialog-heading"><h2 id="detail-title">Details</h2><button id="close-details">Close</button></div><div id="detail-body"></div></dialog></body>`,
    {url: 'https://example.test/snapshots/s1/?browse=1' + hash, runScripts: 'outside-only'});
  const w = dom.window;
  w.matchMedia = () => ({matches: wide, addEventListener() {}});
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; this.dataset.modal = 'true'; };
  w.HTMLDialogElement.prototype.show = function () { this.open = true; this.dataset.modal = 'false'; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  w.eval(source);
  if (w.document.readyState === 'loading') await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, {once: true}));
  // The evidence renderer opens the shared drawer as it always has.
  w.document.addEventListener('click', event => { if (event.target.closest('.detail-button')) w.document.getElementById('detail-dialog').showModal(); });
  return dom;
}

test('on wide screens the group drawer docks beside the list and marks the selected row', async () => {
  const dom = await fixture(), w = dom.window, d = w.document, dialog = d.getElementById('detail-dialog');
  d.querySelector('#row-b .detail-button').click();
  assert.equal(dialog.open, true);
  assert.equal(dialog.dataset.modal, 'false');
  assert.ok(dialog.classList.contains('is-docked'));
  assert.ok(d.body.classList.contains('evidence-docked'));
  assert.equal(d.getElementById('row-b').getAttribute('aria-current'), 'true');
  d.querySelector('#row-a .row-open').click();
  assert.equal(d.getElementById('row-b').hasAttribute('aria-current'), false);
  assert.equal(d.getElementById('row-a').getAttribute('aria-current'), 'true');
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert.equal(dialog.open, false);
  assert.ok(!d.body.classList.contains('evidence-docked'));
  assert.equal(d.querySelector('tr[aria-current]'), null);
  w.close();
});

test('narrow screens and other report pages keep the modal drawer', async () => {
  let dom = await fixture({wide: false}), d = dom.window.document;
  d.querySelector('#row-a .detail-button').click();
  assert.equal(d.getElementById('detail-dialog').dataset.modal, 'true');
  assert.ok(!d.body.classList.contains('evidence-docked'));
  dom.window.close();
  dom = await fixture({hash: '#dfw-rules'}); d = dom.window.document;
  d.querySelector('#rule .detail-button').click();
  assert.equal(d.getElementById('detail-dialog').dataset.modal, 'true');
  assert.equal(d.getElementById('rule').hasAttribute('aria-current'), false);
  dom.window.close();
});

test('leaving the list for a page without docking closes the docked panel', async () => {
  const dom = await fixture(), w = dom.window, d = w.document;
  d.querySelector('#row-a .detail-button').click();
  w.location.hash = '#dfw-rules';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.equal(d.getElementById('detail-dialog').open, false);
  w.close();
});

test('the docked panel offers docked, wide and full screen; firewall tables widen it and Escape leaves full screen first', async () => {
  const dom = await fixture(), w = dom.window, d = w.document, dialog = d.getElementById('detail-dialog');
  d.querySelector('#row-a .row-open').click(); d.querySelector('#row-a .detail-button').click();
  const modes = () => [...d.querySelectorAll('.evidence-modes button')].map(b => b.dataset.mode + ':' + b.getAttribute('aria-pressed'));
  assert.deepEqual(modes(), ['docked:true', 'wide:false', 'full:false']);
  assert.ok(dialog.classList.contains('is-docked') && dialog.classList.contains('mode-docked'));
  assert.match(d.querySelector('.evidence-new-tab').href, /\?browse=1&view=inventory&object=%2Finfra%2Fvm%2Fa&name=A\+%E2%80%94\+Evidence\+%26\+notes$/);
  dialog.classList.add('evidence-firewall-wide'); await new Promise(r => setTimeout(r, 0));
  assert.ok(dialog.classList.contains('mode-wide'), 'firewall references widen the panel');
  assert.ok(dialog.classList.contains('is-docked'), 'widening keeps the panel docked');
  d.querySelector('.evidence-modes [data-mode=full]').click();
  assert.ok(dialog.classList.contains('mode-full'));
  assert.equal(w.localStorage.getItem('evidence-panel-mode'), 'full');
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert.equal(dialog.open, true); assert.ok(dialog.classList.contains('mode-wide'));
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert.equal(dialog.open, false);
  assert.equal(d.querySelector('.evidence-modes'), null, 'controls are removed with the panel');
  w.close();
});

test('the resize handle changes the docked width within bounds and remembers it', async () => {
  const dom = await fixture(), w = dom.window, d = w.document;
  w.localStorage.setItem('evidence-panel-mode', 'docked');
  d.querySelector('#row-a .detail-button').click();
  const handle = d.querySelector('.evidence-resize');
  assert.equal(handle.getAttribute('role'), 'separator');
  handle.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}));
  assert.equal(handle.getAttribute('aria-valuenow'), '492');
  assert.equal(w.localStorage.getItem('evidence-panel-width'), '492');
  for (let i = 0; i < 20; i++) handle.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
  assert.equal(handle.getAttribute('aria-valuenow'), '360');
  w.close();
});

test('coverage notes in the docked panel collapse into About this evidence', async () => {
  const dom = await fixture(), w = dom.window, d = w.document;
  d.querySelector('#row-a .detail-button').click();
  d.getElementById('detail-body').innerHTML = '<section class="evidence-table-section"><h3>Firewall references</h3><p class="evidence-table-status">Partial</p><p class="evidence-note">Saved snapshot caveat</p><p class="evidence-note" hidden>Hidden</p><div class="evidence-table-content"></div></section>';
  await new Promise(r => setTimeout(r, 0));
  const about = d.querySelector('details.evidence-about');
  assert.ok(about); assert.equal(about.querySelector('summary').textContent, 'About this evidence');
  assert.equal(about.querySelectorAll('p.evidence-note').length, 2);
  assert.equal(about.previousElementSibling.className, 'evidence-table-status');
  w.close();
});

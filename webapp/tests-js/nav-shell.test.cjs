const {test} = require('node:test'), assert = require('node:assert/strict'), {JSDOM} = require('jsdom'), fs = require('node:fs'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/nav-shell.js'), 'utf8');
const index = JSON.stringify([
  {type: 'Pages', label: 'Inventory', meta: 'Explore', url: '/workspace/inventory/'},
  {type: 'Pages', label: 'Reviews › All findings', meta: 'Reviews', url: '/my-work/?queue=all'},
  {type: 'Environments', label: 'East', meta: 'Snapshots and collections', url: '/environments/1/'},
]);

async function fixture({expanded = true, remember = true, fetcher, desktop = true, url = 'https://example.test/'} = {}) {
  const dom = new JSDOM(`<body data-theme="light" data-section-expanded="${expanded}" data-remember-menus="${remember}">
    <form id="interface-csrf"><input value="test-csrf"></form>
    <aside data-nav-shell>
      <button data-command-open><kbd data-command-shortcut></kbd></button>
      <div class="workspace-module-rail"><nav class="nav-tree">
        <div class="nav-module has-pages" data-nav-module="inventory"><div class="nav-row"><a href="/workspace/inventory/" data-module-label="Inventory" data-nav-flyout-trigger aria-haspopup="true">Inventory</a><button data-nav-disclosure aria-expanded="false" aria-controls="nav-pages-inventory"></button></div>
          <div class="nav-pages" id="nav-pages-inventory" data-nav-pages hidden><a href="/vms/">VMs</a><a href="/groups/">Groups</a></div></div>
        <div class="nav-module" data-nav-module="overview"><div class="nav-row"><a href="/" data-module-label="Overview">Overview</a></div></div>
      </nav></div>
      <button data-section-toggle data-save-url="/api/preferences/interface/" aria-expanded="${expanded}"><span>Collapse</span></button>
      <span data-section-nav-status role="status"></span>
    </aside>
    <script type="application/json" id="navigation-index">${index}</script>
    <dialog id="command-palette" data-search-url="/api/search/"><input data-command-input><ul data-command-results></ul><p data-command-status></p></dialog>
    <button class="relay-theme-toggle"></button>
    <main id="main"><input name="reason" value="Unsaved reason"></main></body>`, {url, runScripts: 'outside-only'});
  const w = dom.window;
  w.matchMedia = query => ({matches: query.includes('801px') ? desktop : false, addEventListener() {}});
  w.fetch = fetcher || (async () => ({ok: true, redirected: false, json: async () => ({results: []})}));
  w.AbortSignal.timeout = () => undefined;
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  w.Element.prototype.scrollIntoView = function () {};
  if (!w.CSS) w.CSS = {escape: value => value};
  w.eval(source);
  await new Promise(resolve => w.document.addEventListener('DOMContentLoaded', resolve, {once: true}));
  return dom;
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('collapse toggles the rail, saves the preference with CSRF and keeps page input', async () => {
  let call;
  const dom = await fixture({fetcher: async (url, options) => { call = {url, options}; return {ok: true, redirected: false}; }});
  const d = dom.window.document, toggle = d.querySelector('[data-section-toggle]');
  toggle.click();
  assert.equal(d.body.dataset.sectionExpanded, 'false');
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(toggle.getAttribute('aria-label'), 'Expand navigation');
  assert.equal(call.url, '/api/preferences/interface/');
  assert.deepEqual(JSON.parse(call.options.body), {key: 'menu:workspace:section-panel', value: false});
  assert.equal(call.options.headers['X-CSRFToken'], 'test-csrf');
  await tick(); toggle.click();
  assert.equal(d.body.dataset.sectionExpanded, 'true');
  assert.equal(d.querySelector('[name=reason]').value, 'Unsaved reason');
  dom.window.close();
});

test('menu memory off avoids saving; a failed save reports and keeps the layout', async () => {
  let calls = 0;
  let dom = await fixture({remember: false, fetcher: async () => { calls++; return {ok: true}; }});
  dom.window.document.querySelector('[data-section-toggle]').click();
  assert.equal(calls, 0); dom.window.close();
  dom = await fixture({fetcher: async () => ({ok: false})});
  const d = dom.window.document; d.querySelector('[data-section-toggle]').click(); await tick();
  assert.equal(d.body.dataset.sectionExpanded, 'false');
  assert.match(d.querySelector('[data-section-nav-status]').textContent, /could not be saved/);
  dom.window.close();
});

test('disclosure expands module pages in the expanded sidebar', async () => {
  const dom = await fixture(), d = dom.window.document, button = d.querySelector('[data-nav-disclosure]');
  button.click();
  assert.equal(d.getElementById('nav-pages-inventory').hidden, false);
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  assert.equal(button.getAttribute('aria-label'), 'Hide Inventory pages');
  button.click();
  assert.equal(d.getElementById('nav-pages-inventory').hidden, true);
  dom.window.close();
});

test('in the collapsed rail a module opens a flyout instead of navigating; Escape closes it', async () => {
  const dom = await fixture({expanded: false}), w = dom.window, d = w.document;
  const module = d.querySelector('[data-nav-module=inventory]'), trigger = module.querySelector('[data-nav-flyout-trigger]');
  const event = new w.MouseEvent('click', {bubbles: true, cancelable: true});
  trigger.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.ok(module.classList.contains('is-flyout'));
  assert.equal(d.getElementById('nav-pages-inventory').hidden, false);
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');
  module.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  assert.ok(!module.classList.contains('is-flyout'));
  assert.equal(d.getElementById('nav-pages-inventory').hidden, true);
  w.close();
});

test('expanded sidebar links navigate normally', async () => {
  const dom = await fixture(), w = dom.window;
  const event = new w.MouseEvent('click', {bubbles: true, cancelable: true});
  w.document.querySelector('[data-nav-flyout-trigger]').dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
  w.close();
});

test('palette opens with the keyboard shortcut, filters pages and environments, and supports arrows', async () => {
  const dom = await fixture(), w = dom.window, d = w.document, dialog = d.getElementById('command-palette'), input = d.querySelector('[data-command-input]');
  d.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true}));
  assert.equal(dialog.open, true);
  assert.ok(d.querySelectorAll('[role=option]').length >= 3);
  input.value = 'east'; input.dispatchEvent(new w.Event('input'));
  const labels = [...d.querySelectorAll('.command-label')].map(node => node.textContent);
  assert.deepEqual(labels, ['East']);
  input.value = 'review'; input.dispatchEvent(new w.Event('input'));
  input.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'ArrowDown'}));
  assert.equal(input.getAttribute('aria-activedescendant'), 'command-option-0');
  w.close();
});

test('palette actions run without navigation and saved-object results come from the search endpoint', async () => {
  let requested;
  const dom = await fixture({fetcher: async url => { requested = String(url); return {ok: true, redirected: false, json: async () => ({results: [{label: 'web-tier-prod', meta: 'Group · East', url: '/snapshots/x/?browse=1&find=web-tier-prod#all-groups'}]})}; }});
  const w = dom.window, d = w.document, input = d.querySelector('[data-command-input]');
  d.querySelector('[data-command-open]').click();
  input.value = 'collapse'; input.dispatchEvent(new w.Event('input'));
  input.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Enter'}));
  assert.equal(d.body.dataset.sectionExpanded, 'false');
  assert.equal(d.getElementById('command-palette').open, false);
  d.querySelector('[data-command-open]').click();
  input.value = 'web'; input.dispatchEvent(new w.Event('input'));
  await new Promise(resolve => setTimeout(resolve, 260));
  assert.match(requested, /\/api\/search\/\?q=web$/);
  assert.equal(d.querySelector('.command-group').textContent, 'Objects');
  assert.equal(d.querySelector('.command-label').textContent, 'web-tier-prod');
  w.close();
});

test('a failed object search keeps page results and explains the failure', async () => {
  const dom = await fixture({fetcher: async () => ({ok: false})}), w = dom.window, d = w.document, input = d.querySelector('[data-command-input]');
  d.querySelector('[data-command-open]').click();
  input.value = 'inv'; input.dispatchEvent(new w.Event('input'));
  await new Promise(resolve => setTimeout(resolve, 260));
  assert.match(d.querySelector('[data-command-status]').textContent, /could not be searched/);
  assert.deepEqual([...d.querySelectorAll('.command-label')].map(n => n.textContent), ['Inventory']);
  w.close();
});

test('snapshot report hash keeps the sidebar module, page and breadcrumb in step', async () => {
  const dom = new JSDOM(`<body data-section-expanded="true"><aside data-nav-shell><nav class="nav-tree">
    <div class="nav-module has-pages is-active" data-nav-module="inventory"><div class="nav-row"><a href="#overview" data-primary="inventory" data-module-label="Inventory" aria-current="true">Inventory</a><button data-nav-disclosure aria-expanded="true"></button></div><div data-nav-pages><a href="/snapshots/s1/#overview" aria-current="page">Overview</a></div></div>
    <div class="nav-module has-pages" data-nav-module="firewall"><div class="nav-row"><a href="#dfw-overview" data-primary="firewall" data-module-label="Firewall">Firewall</a><button data-nav-disclosure aria-expanded="false"></button></div><div data-nav-pages hidden><a href="/snapshots/s1/#dfw-rules">Rules</a></div></div>
  </nav></aside><nav class="topbar-breadcrumb"><span>Production</span><span aria-hidden="true">/</span><span>Inventory</span><span aria-hidden="true">/</span><span aria-current="page">Overview</span></nav></body>`, {url: 'https://example.test/snapshots/s1/?browse=1#dfw-rules', runScripts: 'outside-only'});
  const w = dom.window, d = w.document;
  w.matchMedia = () => ({matches: true, addEventListener() {}});
  w.eval(source);
  await new Promise(resolve => d.addEventListener('DOMContentLoaded', resolve, {once: true}));
  const firewall = d.querySelector('[data-nav-module=firewall]');
  assert.ok(firewall.classList.contains('is-active'));
  assert.ok(!d.querySelector('[data-nav-module=inventory]').classList.contains('is-active'));
  assert.equal(firewall.querySelector('[data-nav-pages] a').getAttribute('aria-current'), 'page');
  assert.equal(firewall.querySelector('[data-nav-pages]').hidden, false);
  assert.equal(d.querySelector('.topbar-breadcrumb').textContent, 'Production/Firewall/Rules');
  w.location.hash = '#overview';
  w.dispatchEvent(new w.HashChangeEvent('hashchange'));
  assert.ok(d.querySelector('[data-nav-module=inventory]').classList.contains('is-active'));
  w.close();
});

test('report sub-views keep their list page selected in the sidebar', async () => {
  const dom = new JSDOM(`<body data-section-expanded="true"><aside data-nav-shell><nav class="nav-tree">
    <div class="nav-module has-pages is-active" data-nav-module="inventory"><div class="nav-row"><a href="#overview" data-primary="inventory" data-module-label="Inventory" aria-current="true">Inventory</a></div><div data-nav-pages><a href="/snapshots/s1/#all-vms" aria-current="page">VMs</a><a href="/snapshots/s1/#all-groups">Groups</a></div></div>
  </nav></aside><nav class="topbar-breadcrumb"><span>Production</span><span aria-hidden="true">/</span><span>Inventory</span><span aria-hidden="true">/</span><span aria-current="page">VMs</span></nav></body>`, {url: 'https://example.test/snapshots/s1/?browse=1#empty-groups', runScripts: 'outside-only'});
  const w = dom.window, d = w.document;
  w.matchMedia = () => ({matches: true, addEventListener() {}});
  w.eval(source);
  await new Promise(resolve => d.addEventListener('DOMContentLoaded', resolve, {once: true}));
  assert.equal(d.querySelector('[data-nav-pages] a[aria-current]').textContent, 'Groups');
  assert.equal(d.querySelector('.topbar-breadcrumb').textContent, 'Production/Inventory/Groups');
  w.close();
});

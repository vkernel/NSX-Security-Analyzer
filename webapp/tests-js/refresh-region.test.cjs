// Run with Node's test runner and jsdom available through NODE_PATH.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require('jsdom');
const source = name => readFileSync(join(__dirname, '../static', name), 'utf8');
function setup(html) {
  const dom = new JSDOM(html, {runScripts:'outside-only', pretendToBeVisual:true, url:'https://example.test/deletion/'});
  dom.window.scrollTo = () => {};
  dom.window.eval(source('refresh-region.js'));
  return dom;
}
const row = (id, status) => `<section data-refresh-key="${id}"><span>${status}</span><details><summary>Verification details</summary><pre>Evidence</pre></details></section>`;
function next(w, html) { return new w.DOMParser().parseFromString(html, 'text/html').body.firstElementChild; }

test('expanded focused details update, retain focus and follow object identity after reorder', () => {
  const dom=setup(`<div id="results">${row('a','checking')}${row('b','checking')}</div>`),w=dom.window;
  const root=w.document.getElementById('results');
  root.querySelector('details').open=true;
  root.querySelector('summary').focus();
  root.querySelector('pre').scrollTop=24;
  assert.equal(w.RefreshRegion.replace(root,next(w,`<div id="results">${row('b','deleted')}${row('a','deleted')}</div>`)), '');
  assert.equal(root.querySelector('[data-refresh-key="a"] span').textContent,'deleted');
  assert.equal(root.querySelector('[data-refresh-key="a"] details').open,true);
  assert.equal(root.querySelector('[data-refresh-key="b"] details').open,false);
  assert.equal(w.document.activeElement.closest('section').dataset.refreshKey,'a');
  assert.equal(root.querySelector('[data-refresh-key="a"] pre').scrollTop,24);
  dom.window.close();
});

test('protect focused form fields and open dialogs, preserve draft values on resume', () => {
  const dom=setup('<div id="results"><input id="ticket" value="draft"><input name="confirm" type="checkbox" checked><span>old</span></div><dialog open></dialog>'),w=dom.window;
  const root=w.document.getElementById('results');
  const html='<div id="results"><input id="ticket"><input name="confirm" type="checkbox"><span>new</span></div>';
  assert.match(w.RefreshRegion.replace(root,next(w,html)),/dialog/);
  w.document.querySelector('dialog').removeAttribute('open');
  w.document.querySelector('input').focus();
  assert.match(w.RefreshRegion.replace(root,next(w,html)),/edit a field/);
  assert.equal(root.querySelector('span').textContent,'old');
  w.document.activeElement.blur();
  assert.equal(w.RefreshRegion.replace(root,next(w,html)),'');
  assert.equal(root.querySelector('#ticket').value,'draft');
  assert.equal(root.querySelector('[name="confirm"]').checked,true);
  dom.window.close();
});

test('shared poll updates expanded rows and reports genuine dialog pause', async () => {
  const dom=setup(`<p data-live-status></p><div id="results" data-live-region data-poll="true">${row('a','old')}</div>`),w=dom.window;
  let poll;
  w.setTimeout=fn=>{poll=fn;};
  w.fetch=async()=>({ok:true,text:async()=>`<div id="results" data-poll="true">${row('a','new')}</div>`});
  w.document.querySelector('details').open=true; w.document.querySelector('summary').focus();
  w.eval(source('live-tasks.js'));
  await poll();
  assert.equal(w.document.querySelector('span').textContent,'new');
  assert.equal(w.document.querySelector('details').open,true);
  const dialog=w.document.createElement('dialog');dialog.open=true;w.document.body.append(dialog);
  await poll();
  assert.match(w.document.querySelector('[data-live-status]').textContent,/paused while a dialog/);
  assert.doesNotMatch(w.document.querySelector('[data-live-status]').textContent,/Status updated/);
  dom.window.close();
});

test('deletion timer refreshes focused disclosure and stops on terminal state', async () => {
  const dom=setup(`<div id="deletion-progress" data-active="true">${row('a','checking')}<p data-refresh-status></p></div>`),w=dom.window;
  let tick, calls=0;
  w.setInterval=fn=>{tick=fn;};
  w.fetch=async()=>{calls++;return {ok:true,text:async()=>`<div id="deletion-progress" data-active="false">${row('a','deleted')}</div>`};};
  w.document.querySelector('details').open=true;w.document.querySelector('summary').focus();
  w.eval(source('nsx-deletion.js'));
  tick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(w.document.querySelector('span').textContent,'deleted');
  assert.equal(w.document.querySelector('details').open,true);
  tick();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,1);
  dom.window.close();
});

test('automatic refresh preserves validation errors until explicit refresh', () => {
  const dom=setup('<div id="results"><p role="alert" data-preserve-error>Approval could not be recorded</p></div>'),w=dom.window;
  const root=w.document.getElementById('results');
  assert.match(w.RefreshRegion.replace(root,next(w,'<div id="results">Updated</div>')),/error visible/);
  assert.match(root.textContent,/Approval/);
  assert.equal(w.RefreshRegion.replace(root,next(w,'<div id="results">Updated</div>'),{allowError:true}),'');
  assert.equal(root.textContent,'Updated');
  dom.window.close();
});

test('shared refresh pause controls do not submit forms or lose reading state', async () => {
  const dom=setup('<div data-task-refresh-controls hidden><span data-live-status></span><button data-task-refresh>Refresh</button><button data-task-pause>Pause</button></div><div id="results" data-live-region data-poll="true">old</div>'),w=dom.window;
  let calls=0,poll;
  w.setTimeout=fn=>{poll=fn;};
  w.fetch=async()=>{calls++;return {ok:true,text:async()=>'<div id="results" data-poll="true">new</div>'};};
  w.eval(source('live-tasks.js'));
  w.document.querySelector('[data-task-pause]').click();
  await poll();
  assert.equal(calls,0);
  assert.equal(w.document.querySelector('[data-task-pause]').getAttribute('aria-pressed'),'true');
  w.document.querySelector('[data-task-refresh]').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,1);
  assert.match(w.document.querySelector('[data-live-status]').textContent,/paused/);
  dom.window.close();
});

test('action menus close with Escape and selection preview stays read-only', () => {
  const dom=setup('<main id="main"><div class="cleanup-task-actions"><details open><summary>More</summary><a href="/history">History</a></details></div><form data-preview-selection><input type="checkbox" name="finding" value="1"><span data-selected-count></span><button data-preview-selected>Preview</button></form></main>'),w=dom.window;
  w.eval(source('interface-patterns.js'));
  const menu=w.document.querySelector('details'),link=menu.querySelector('a');
  link.focus();link.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(menu.open,false);assert.equal(w.document.activeElement,menu.querySelector('summary'));
  const button=w.document.querySelector('button');
  assert.equal(button.disabled,true);
  const input=w.document.querySelector('input');input.checked=true;input.dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(button.disabled,false);assert.equal(button.textContent,'Preview 1 selected object');
  dom.window.close();
});

test('collection-only pages share pause and manual refresh controls', async () => {
  const dom=setup('<div data-task-refresh-controls hidden><span data-live-status></span><button data-task-refresh>Refresh</button><button data-task-pause>Pause</button></div><div data-job="1" data-status="running"><span class="pill">Running</span><div class="collection-details"></div></div>'),w=dom.window;
  let tick,calls=0;
  w.setTimeout=fn=>{tick=fn;return 1;};
  w.fetch=async()=>{calls++;return {ok:true,json:async()=>({jobs:[{id:'1',status:'succeeded',label:'Completed',progress:{stage:'Finished'},result_url:'/result'}]})};};
  w.eval(source('workspace.js'));w.eval(source('live-tasks.js'));
  assert.equal(w.document.querySelector('[data-task-refresh-controls]').hidden,false);
  w.document.querySelector('[data-task-pause]').click();
  await tick();assert.equal(calls,0);
  w.document.querySelector('[data-task-refresh]').click();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls,1);
  assert.equal(w.document.querySelector('.pill').textContent,'Completed');
  assert.equal(w.document.querySelector('[data-task-pause]').disabled,true);
  assert.equal(w.document.querySelector('.collection-details a').getAttribute('href'),'/result');
  dom.window.close();
});

test('sidebar navigation clears stale list positions and opens the page at the top', () => {
  const dom=setup('<aside id="primary-sidebar"><a href="/">Overview</a></aside><main id="main"></main>'),w=dom.window;
  const suffix=w.location.pathname+w.location.search;
  w.sessionStorage.setItem('review-position:'+suffix,JSON.stringify({y:700,at:Date.now()}));
  w.sessionStorage.setItem('ui-list-position:'+suffix,JSON.stringify({y:900,at:Date.now()}));
  w.sessionStorage.setItem('workspace-sidebar-navigation',String(Date.now()));
  const scrolls=[];w.scrollTo=(...args)=>scrolls.push(args);w.requestAnimationFrame=fn=>fn();
  w.eval(source('navigation-scroll.js'));w.eval(source('review-navigation.js'));w.eval(source('interface-patterns.js'));
  w.dispatchEvent(new w.PageTransitionEvent('pageshow'));
  assert.equal(scrolls.length,1);assert.equal(scrolls[0][0].top,0);
  assert.equal(w.sessionStorage.getItem('review-position:'+suffix),null);
  assert.equal(w.sessionStorage.getItem('ui-list-position:'+suffix),null);
  dom.window.close();
});

test('ordinary returns still restore list position and modified sidebar clicks do not reset it', () => {
  const dom=setup('<aside id="primary-sidebar"><a href="/">Overview</a></aside><main id="main"></main>'),w=dom.window;
  const suffix=w.location.pathname+w.location.search;
  const scrolls=[];w.scrollTo=(...args)=>scrolls.push(args);w.requestAnimationFrame=fn=>fn();
  w.eval(source('navigation-scroll.js'));w.eval(source('review-navigation.js'));
  w.document.querySelector('a').addEventListener('click',e=>e.preventDefault());
  w.document.querySelector('a').dispatchEvent(new w.MouseEvent('click',{bubbles:true,ctrlKey:true,button:0,cancelable:true}));
  assert.equal(w.sessionStorage.getItem('workspace-sidebar-navigation'),null);
  w.sessionStorage.setItem('review-position:'+suffix,JSON.stringify({y:700,at:Date.now()}));
  w.dispatchEvent(new w.PageTransitionEvent('pageshow'));
  assert.equal(scrolls[0][1],700);
  w.document.querySelector('a').dispatchEvent(new w.MouseEvent('click',{bubbles:true,button:0,cancelable:true}));
  assert.ok(w.sessionStorage.getItem('workspace-sidebar-navigation'));
  dom.window.close();
});

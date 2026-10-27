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

const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {JSDOM} = require('jsdom');
test('environment scope includes All environments and supports search and Escape', () => {
 const dom = new JSDOM('<div><label for="environment-switcher">Environment</label><select id="environment-switcher" data-scope-navigation><option value="" data-url="/activity/" selected>All environments</option><option value="1" data-url="/activity/?environment=1">Rotterdam</option><option value="2" data-url="/activity/?environment=2">Amsterdam</option></select><span class="scope-status"></span></div>', {runScripts:'outside-only', url:'https://example.test/activity/'});
 const w=dom.window;
 w.eval(readFileSync(require('node:path').join(__dirname,'../static/switchers.js'),'utf8'));
 w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
 const root=w.document.querySelector('.workspace-switcher');
 const buttons=[...root.querySelectorAll('.switcher-options button')];
 assert.deepEqual(buttons.map(b=>b.textContent), ['All environments','Rotterdam','Amsterdam']);
 assert.equal(buttons[0].getAttribute('aria-current'),'true');
 root.open=true;
 const search=root.querySelector('input');search.value='amster';search.dispatchEvent(new w.Event('input'));
 assert.equal(buttons[1].hidden,true);assert.equal(buttons[2].hidden,false);
 search.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
 assert.equal(root.open,false);assert.equal(w.document.activeElement,root.querySelector('summary'));
 dom.window.close();
});

test('failed environment switch retains current scope and exposes Retry', async () => {
 const dom = new JSDOM('<div><label>Environment</label><select id="environment-switcher" data-scope-navigation><option value="1" selected data-url="/activity/?environment=1">East</option><option value="2" data-url="/activity/?environment=2">West</option></select><span class="scope-status"></span></div>', {runScripts:'outside-only', url:'https://example.test/activity/?environment=1'});
 const w=dom.window; let calls=0;
 w.fetch=async()=>{calls++;return {ok:false,status:503};};
 w.eval(readFileSync(require('node:path').join(__dirname,'../static/switchers.js'),'utf8'));
 w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
 const select=w.document.querySelector('select');select.value='2';select.dispatchEvent(new w.Event('change'));
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(select.value,'1');assert.match(w.document.querySelector('.scope-status').textContent,/current page is unchanged/);
 const retry=w.document.querySelector('.scope-retry');assert.equal(retry.hidden,false);
 retry.click();await new Promise(resolve=>setTimeout(resolve,0));assert.equal(calls,2);
 assert.equal(w.location.search,'?environment=1');dom.window.close();
});

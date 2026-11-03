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

test('environment selection navigates once, clears old object context and never prefetches a full page', () => {
 const {VirtualConsole}=require('jsdom');
 const dom = new JSDOM('<div><label>Environment</label><select id="environment-switcher" data-scope-navigation data-browsing="true"><option value="1" selected data-url="/snapshots/east/?browse=1">East</option><option value="2" data-url="/snapshots/west/?browse=1&amp;context_changed=1">West</option></select><span class="scope-status"></span></div>', {runScripts:'outside-only', url:'https://example.test/snapshots/east/?browse=1&groups=old#all-groups',virtualConsole:new VirtualConsole()});
 const w=dom.window,calls=[];w.fetch=()=>{throw Error('Context switching must not prefetch');};
 w.WorkspaceNavigation={start:url=>calls.push(url)};
 w.eval(readFileSync(require('node:path').join(__dirname,'../static/switchers.js'),'utf8'));w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
 const select=w.document.querySelector('select');select.value='2';select.dispatchEvent(new w.Event('change'));
 assert.equal(calls.length,1);const target=new URL(calls[0]);assert.equal(target.pathname,'/snapshots/west/');assert.equal(target.hash,'#all-groups');assert.equal(target.searchParams.has('groups'),false);assert.match(w.document.querySelector('.scope-status').textContent,/Opening West/);dom.window.close();
});

test('recent environments appear once in recency order, stay searchable and support keyboard navigation',()=>{
 const dom=new JSDOM('<div><select id="environment-switcher"><option value="1" selected data-url="/environments/1/">East</option><option value="2" data-url="/environments/2/" data-recent-rank="3">West</option><option value="3" data-url="/environments/3/" data-recent-rank="2">North</option><option value="4" data-url="/environments/4/">South</option></select><span class="scope-status"></span></div>',{runScripts:'outside-only',url:'https://example.test/environments/1/'}),w=dom.window;
 w.eval(readFileSync(require('node:path').join(__dirname,'../static/switchers.js'),'utf8'));w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
 const root=w.document.querySelector('.workspace-switcher'),groups=[...root.querySelectorAll('.switcher-group')],buttons=[...root.querySelectorAll('.switcher-options button')],search=root.querySelector('input');
 assert.deepEqual(buttons.map(b=>b.textContent),['North','West','East','South']);assert.equal(buttons.length,4);assert.match(groups[0].textContent,/Recently visited/);
 root.open=true;search.focus();search.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));assert.equal(w.document.activeElement,buttons[0]);
 search.value='South';search.dispatchEvent(new w.Event('input'));assert.equal(groups[0].hidden,true);assert.equal(groups[1].hidden,false);assert.equal(buttons.filter(b=>!b.hidden).length,1);
 search.focus();search.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));assert.equal(w.document.activeElement.textContent,'South');
 w.document.activeElement.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert.equal(root.open,false);assert.equal(w.document.activeElement,root.querySelector('summary'));w.close();
});

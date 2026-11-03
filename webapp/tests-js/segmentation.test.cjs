const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const {JSDOM}=require('jsdom');
function page(html){const dom=new JSDOM(html,{runScripts:'outside-only',url:'https://example.test/environments/1/segmentation/'});dom.window.HTMLElement.prototype.scrollIntoView=function(){};dom.window.eval(fs.readFileSync(path.join(__dirname,'../static/segmentation.js'),'utf8'));return dom;}
test('application selection focuses related connections and reset restores all',()=>{
 const dom=page(`<a href="#application-0" data-application="0">Portal</a><details id="application-0"><summary>Portal</summary></details><path data-edge-source="0" data-edge-destination="1"></path><path data-edge-source="1" data-edge-destination="2"></path><details data-connection-source="0" data-connection-destination="1"></details><details data-connection-source="1" data-connection-destination="2"></details><p data-application-status></p><button data-clear-application>Reset</button>`);const d=dom.window.document;
 d.querySelector('[data-application]').click();assert.equal(d.getElementById('application-0').open,true);assert.equal(d.querySelectorAll('[data-connection-source][hidden]').length,1);assert.equal(d.querySelectorAll('.is-muted').length,1);
 d.querySelector('[data-clear-application]').click();assert.equal(d.querySelectorAll('[data-connection-source][hidden]').length,0);dom.window.close();
});
test('matrix search preserves selection and enforces the twelve-group limit',()=>{
 const dom=page(`<input data-group-search><span data-group-count></span>${Array.from({length:13},(_,i)=>`<label data-group-option>Group ${i}<input type="checkbox" value="/groups/${i}" ${i<12?'checked':''}></label>`).join('')}`);const d=dom.window.document;const inputs=d.querySelectorAll('[type=checkbox]');assert.equal(inputs[12].disabled,true);
 const search=d.querySelector('[data-group-search]');search.value='/groups/12';search.dispatchEvent(new dom.window.Event('input'));assert.equal(d.querySelectorAll('[data-group-option]:not([hidden])').length,1);assert.equal(d.querySelectorAll(':checked').length,12);
 inputs[0].checked=false;inputs[0].dispatchEvent(new dom.window.Event('change',{bubbles:true}));assert.equal(inputs[12].disabled,false);assert.equal(d.querySelector('[data-group-count]').textContent,'11 / 12 groups selected');dom.window.close();
});
test('remote matrix search retains selections and renders labels as text',async()=>{
 const dom=page('<input data-group-search data-group-url="/groups?snapshot=one"><p data-group-search-status></p><span data-group-count></span><div data-group-options><label data-group-option><input type="checkbox" name="groups" value="/kept" checked>Kept group</label></div>');
 const w=dom.window,d=w.document;let request;
 w.fetch=async url=>{request=String(url);return {ok:true,json:async()=>({results:[{name:'<img src=x>',path:'/new',type:'dynamic'}],total:1,more:false})};};
 const search=d.querySelector('[data-group-search]');search.value='new';search.dispatchEvent(new w.Event('input'));
 await new Promise(r=>setTimeout(r,300));
 assert.match(request,/snapshot=one/);assert.match(request,/q=new/);
 assert.equal(d.querySelectorAll('[data-group-option]').length,2);assert.equal(d.querySelector(':checked').value,'/kept');assert.equal(d.querySelectorAll('img').length,0);
 assert.match(d.querySelector('[data-group-search-status]').textContent,/1 matching group/);dom.window.close();
});
test('failed remote search keeps the current list and selected groups',async()=>{
 const dom=page('<input data-group-search data-group-url="/groups?snapshot=one"><p data-group-search-status></p><div data-group-options><label data-group-option><input type="checkbox" value="/kept" checked>Kept</label></div>'),w=dom.window,d=w.document;
 w.fetch=async()=>({ok:false});d.querySelector('input').dispatchEvent(new w.Event('input'));await new Promise(r=>setTimeout(r,300));
 assert.equal(d.querySelector(':checked').value,'/kept');assert.match(d.querySelector('[data-group-search-status]').textContent,/Unable to search/);dom.window.close();
});
test('connection rules load only on opening and are reused after reopening',async()=>{
 const dom=page('<details class="application-connection"><summary>Portal → Data</summary><div data-connection-details data-url="/connections?snapshot=one"><p role="status"></p><button data-load-connection>Load rule details</button></div></details>'),w=dom.window,d=w.document;
 const calls=[];w.fetch=async(url,options)=>{calls.push({url,options});return {ok:true,status:200,text:async()=>'<p><a href="/rule?snapshot=one">SQL rule</a></p>'};};
 assert.equal(calls.length,0);d.querySelector('details').open=true;await new Promise(r=>setTimeout(r,20));
 assert.equal(calls.length,1);assert.match(calls[0].url,/snapshot=one/);assert.equal(calls[0].options.credentials,'same-origin');assert.equal(d.querySelector('[data-connection-details] a').textContent,'SQL rule');
 d.querySelector('details').open=false;await new Promise(r=>setTimeout(r,10));d.querySelector('details').open=true;await new Promise(r=>setTimeout(r,10));assert.equal(calls.length,1);dom.window.close();
});
test('failed connection load offers retry and stale evidence offers reopening the map',async()=>{
 const dom=page('<details class="application-connection"><summary>Portal → Data</summary><div data-connection-details data-url="/connections?snapshot=one"><p role="status"></p><button data-load-connection>Load rule details</button></div></details>'),w=dom.window,d=w.document;
 let count=0;w.fetch=async()=>++count===1?{ok:false,status:503}:{ok:false,status:409,text:async()=>'<p>The evidence changed. <a href="/map?snapshot=one">Reopen the application map</a></p>'};
 d.querySelector('details').open=true;await new Promise(r=>setTimeout(r,20));
 const retry=d.querySelector('[data-load-connection]');assert.equal(retry.disabled,false);assert.equal(retry.textContent,'Retry loading rules');
 retry.click();await new Promise(r=>setTimeout(r,20));assert.match(d.querySelector('[data-connection-details]').textContent,/Reopen the application map/);assert.equal(count,2);dom.window.close();
});

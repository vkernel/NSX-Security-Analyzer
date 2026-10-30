const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const {JSDOM}=require('jsdom');
function page(html){const dom=new JSDOM(html,{runScripts:'outside-only'});dom.window.HTMLElement.prototype.scrollIntoView=function(){};dom.window.eval(fs.readFileSync(path.join(__dirname,'../static/segmentation.js'),'utf8'));return dom;}
test('application selection focuses related connections and reset restores all',()=>{
 const dom=page(`<a href="#application-0" data-application="0">Portal</a><details id="application-0"><summary>Portal</summary></details><path data-edge-source="0" data-edge-destination="1"></path><path data-edge-source="1" data-edge-destination="2"></path><details data-connection-source="0" data-connection-destination="1"></details><details data-connection-source="1" data-connection-destination="2"></details><p data-application-status></p><button data-clear-application>Reset</button>`);const d=dom.window.document;
 d.querySelector('[data-application]').click();assert.equal(d.getElementById('application-0').open,true);assert.equal(d.querySelectorAll('[data-connection-source][hidden]').length,1);assert.equal(d.querySelectorAll('.is-muted').length,1);
 d.querySelector('[data-clear-application]').click();assert.equal(d.querySelectorAll('[data-connection-source][hidden]').length,0);dom.window.close();
});
test('matrix search preserves selection and enforces the twelve-group limit',()=>{
 const dom=page(`<input data-group-search><span data-group-count></span>${Array.from({length:13},(_,i)=>`<label data-group-option>Group ${i}<input type="checkbox" value="/groups/${i}" ${i<12?'checked':''}></label>`).join('')}`);const d=dom.window.document;const inputs=d.querySelectorAll('[type=checkbox]');assert.equal(inputs[12].disabled,true);
 const search=d.querySelector('[data-group-search]');search.value='/groups/12';search.dispatchEvent(new dom.window.Event('input'));assert.equal(d.querySelectorAll('[data-group-option]:not([hidden])').length,1);assert.equal(d.querySelectorAll(':checked').length,12);
 inputs[0].checked=false;inputs[0].dispatchEvent(new dom.window.Event('change'));assert.equal(inputs[12].disabled,false);assert.equal(d.querySelector('[data-group-count]').textContent,'11 / 12 groups selected');dom.window.close();
});

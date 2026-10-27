const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const {JSDOM}=require('jsdom');
function setup(n=2){
 const dom=new JSDOM(`<div data-bulk-toolbar="bulk" hidden><span data-bulk-count></span><small data-bulk-help></small><button data-bulk-action="export"></button><button data-bulk-action="cancel"></button><button data-bulk-action="preview" data-bulk-one-environment></button><button data-bulk-clear></button></div><form id="bulk"><input name="return_to"><input name="action"><div data-bulk-values></div></form><input type="checkbox" data-bulk-all="bulk">`+Array.from({length:n},(_,i)=>`<input type="checkbox" data-bulk-choice="bulk" data-bulk-allowed="export cancel preview" data-environment="1" value="${i}:3">`).join(''),{runScripts:'outside-only',url:'https://example.test/my-work/?queue=ready#table'});
 dom.window.eval(readFileSync(join(__dirname,'../static/bulk-actions.js'),'utf8'));return dom;
}
test('shared selection stays page bounded and sends revisions plus return context',()=>{
 const dom=setup(101),w=dom.window,d=w.document;
 const all=d.querySelector('[data-bulk-all]');all.checked=true;all.dispatchEvent(new w.Event('change',{bubbles:true}));
 assert.equal(d.querySelectorAll('[data-bulk-choice]:checked').length,100);
 let payload;const form=d.querySelector('form');form.requestSubmit=()=>payload=new w.FormData(form);
 d.querySelector('[data-bulk-action="export"]').click();
 assert.equal(payload.getAll('selected').length,100);
 assert.equal(payload.get('action'),'export');assert.equal(payload.get('return_to'),'/my-work/?queue=ready#table');
 d.querySelector('[data-bulk-clear]').click();assert.equal(d.querySelector('[data-bulk-toolbar]').hidden,true);
 dom.window.close();
});
test('mixed status and environment disable incompatible actions with explanation',()=>{
 const dom=setup(),w=dom.window,d=w.document,boxes=d.querySelectorAll('[data-bulk-choice]');
 boxes[1].dataset.bulkAllowed='export preview';boxes[1].dataset.environment='2';
 boxes.forEach(box=>{box.checked=true;box.dispatchEvent(new w.Event('change',{bubbles:true}));});
 assert.equal(d.querySelector('[data-bulk-action="cancel"]').disabled,true);
 assert.equal(d.querySelector('[data-bulk-action="preview"]').disabled,true);
 assert.equal(d.querySelector('[data-bulk-action="export"]').disabled,false);
 assert.match(d.querySelector('[data-bulk-help]').textContent,/unavailable/i);
 boxes.forEach(box=>box.remove());d.dispatchEvent(new w.Event('bulk:updated'));
 assert.equal(d.querySelector('[data-bulk-toolbar]').hidden,true);
 dom.window.close();
});

const {test}=require('node:test');const assert=require('node:assert/strict');const {JSDOM}=require('jsdom');const fs=require('node:fs');const path=require('node:path');
test('bulk naming confirmation identifies selected policy and allows cancellation',()=>{
 const dom=new JSDOM(`<form id="naming-proposals"><p data-naming-selected></p><table><tbody><tr data-name="VDI-123" data-policy="VDI policy"><td><input type="checkbox" name="selected" value="1" checked></td></tr></tbody></table><button name="action" value="workflow">Update progress</button></form>`,{runScripts:'outside-only'}),w=dom.window,d=w.document;
 let prompt='';w.confirm=text=>{prompt=text;return false;};w.eval(fs.readFileSync(path.join(__dirname,'../static/naming-compliance.js'),'utf8'));
 const form=d.querySelector('form'),event=new w.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:d.querySelector('button')});form.dispatchEvent(event);
 assert.equal(event.defaultPrevented,true);assert.match(prompt,/VDI policy/);assert.match(prompt,/VDI-123/);assert.match(prompt,/1 groups/);assert.match(prompt,/No NSX changes/);dom.window.close();
});

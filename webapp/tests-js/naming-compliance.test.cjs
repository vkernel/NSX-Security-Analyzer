const {test}=require('node:test');const assert=require('node:assert/strict');const {JSDOM}=require('jsdom');const fs=require('node:fs');const path=require('node:path');
function setup(){
 const dom=new JSDOM(`<button data-naming-import hidden>Import</button><form id="naming-proposals"><div class="naming-selection-bar" hidden><p data-naming-selected></p><button data-naming-open type="button">Choose action</button><button data-naming-clear type="button">Clear</button></div><table><thead><tr><th><input type="checkbox" data-naming-select-all></th></tr></thead><tbody>${['VDI-123','VDI-456'].map((name,i)=>`<tr data-name="${name}" data-policy="VDI policy"><td><input type="checkbox" name="selected" value="${i+1}"><a href="#naming-work-actions" data-naming-row-action>Next action</a></td></tr>`).join('')}</tbody></table><section data-naming-action-panel><h2>Corrections</h2><p data-naming-action-help></p><label hidden>Action<select data-naming-action-choice>${['propose','workflow','assign','review','reject','exception','import'].map(a=>`<option value="${a}">${a}</option>`).join('')}</select></label>${['scope','value','note','progress','ticket','owner','expires_at','csv_file'].map(n=>`<div data-naming-field="${n}"><input name="${n}" ${n==='csv_file'?'type="file"':''}></div>`).join('')}<details data-naming-import-help></details><details data-naming-process-help></details>${['propose','workflow','assign','review','reject','exception','import'].map(a=>`<button name="action" value="${a}">${a}</button>`).join('')}</section></form>`,{url:'https://example.test',runScripts:'outside-only'});
 const w=dom.window;w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};w.HTMLDialogElement.prototype.close=function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
 w.eval(fs.readFileSync(path.join(__dirname,'../static/naming-compliance.js'),'utf8'));return dom;
}
test('selection stays page bounded and clear restores the empty toolbar state',()=>{
 const dom=setup(),w=dom.window,d=w.document,all=d.querySelector('[data-naming-select-all]');all.checked=true;all.dispatchEvent(new w.Event('change'));
 assert.equal(d.querySelectorAll('[name=selected]:checked').length,2);assert.match(d.querySelector('[data-naming-selected]').textContent,/2 groups/);assert.equal(d.querySelector('.naming-selection-bar').hidden,false);
 d.querySelector('[data-naming-clear]').click();assert.equal(d.querySelectorAll('[name=selected]:checked').length,0);assert.equal(d.querySelector('[data-naming-open]').disabled,true);dom.window.close();
});
test('a row action retains that object context and posts only fields for the chosen action',()=>{
 const dom=setup(),w=dom.window,d=w.document;d.querySelectorAll('[name=selected]')[1].checked=true;d.querySelector('[data-naming-row-action]').click();
 assert.equal(d.querySelector('.naming-action-dialog').open,true);assert.deepEqual([...d.querySelectorAll('[name=selected]:checked')].map(b=>b.value),['1']);
 const choice=d.querySelector('[data-naming-action-choice]');assert.equal(choice.value,'workflow');choice.value='assign';choice.dispatchEvent(new w.Event('change'));
 assert.equal(d.querySelector('[name=action][value=propose]').disabled,true);assert.equal(d.querySelector('[name=action][value=assign]').disabled,false);
 const data=new w.FormData(d.querySelector('form'));assert.equal(data.has('owner'),true);assert.equal(data.has('scope'),false);assert.equal(data.has('csv_file'),false);dom.window.close();
});
test('bulk review identifies policy and objects, cancellation retains the draft and confirmation submits once',()=>{
 const dom=setup(),w=dom.window,d=w.document;d.querySelector('[data-naming-row-action]').click();const form=d.querySelector('form'),button=d.querySelector('[name=action][value=workflow]');d.querySelector('[name=ticket]').value='CHG-42';
 const event=new w.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:button});form.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 const review=d.querySelector('.naming-confirmation');assert.equal(review.open,true);assert.match(review.textContent,/VDI policy/);assert.match(review.textContent,/VDI-123/);assert.doesNotMatch(review.textContent,/VDI-456/);assert.match(review.textContent,/No NSX changes/);
 review.querySelector('button').click();assert.equal(review.open,false);assert.equal(d.querySelector('.naming-action-dialog').open,true);assert.equal(d.querySelector('[name=ticket]').value,'CHG-42');
 form.dispatchEvent(new w.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter:button}));let submitted=0;form.requestSubmit=submitter=>{assert.equal(submitter,button);const final=new w.SubmitEvent('submit',{bubbles:true,cancelable:true,submitter});form.dispatchEvent(final);assert.equal(final.defaultPrevented,false);submitted++;};
 review.querySelector('button:last-child').click();assert.equal(submitted,1);dom.window.close();
});
test('import is available without table selection and does not submit unrelated correction fields',()=>{
 const dom=setup(),w=dom.window,d=w.document;d.querySelector('[data-naming-import]').click();const button=d.querySelector('[name=action][value=import]');assert.equal(button.disabled,false);assert.equal(button.hidden,false);const data=new w.FormData(d.querySelector('form'));assert.equal(data.has('csv_file'),true);assert.equal(data.has('scope'),false);assert.match(d.querySelector('[data-naming-action-help]').textContent,/table selection is not used/i);dom.window.close();
});

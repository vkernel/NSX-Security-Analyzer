const {test}=require('node:test'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom'),{readFileSync}=require('fs'),{join}=require('path');
const pause=()=>new Promise(r=>setTimeout(r,40));
test('local counts update after live row changes without duplicating server footers',async()=>{
 const dom=new JSDOM('<main><div class="table-scroll"><table id="local"><tbody><tr><td>A</td></tr></tbody></table></div><div class="table-scroll"><table id="server"><tbody><tr><td>A</td></tr></tbody></table></div><nav class="table-footer">Showing 1–1 of 1</nav><div class="table-widget"><table><tbody><tr><td>remote</td></tr></tbody></table></div></main>',{runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document;w.eval(readFileSync(join(__dirname,'../static/table-controls.js'),'utf8'));await pause();assert.equal(d.querySelectorAll('[data-local-table-count]').length,1);assert.equal(d.querySelector('[data-local-table-count]').textContent,'1 row shown');
 d.querySelector('#local tbody').insertAdjacentHTML('beforeend','<tr><td><input type="checkbox" checked>B</td></tr>');await pause();assert.equal(d.querySelector('[data-local-table-count]').textContent,'2 rows shown · 1 selected on this page');
 d.querySelector('#local tbody').innerHTML='<tr><td colspan="3">No results</td></tr>';await pause();assert.equal(d.querySelector('[data-local-table-count]').textContent,'0 rows shown');dom.window.close();
});
test('owner filter supports shortcuts and retains selection until another result is chosen',async()=>{
 const dom=new JSDOM('<label for="owner">Owner</label><select id="owner" data-user-search="/users" data-reviewer-combobox data-reviewer-filter><option value="">Anyone</option><option value="me">Assigned to me</option><option value="none">Unassigned</option></select>',{runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document;w.fetch=async()=>({ok:true,json:async()=>({items:[{id:7,label:'Person · person@example.test'}],has_more:false})});w.HTMLElement.prototype.scrollIntoView=()=>{};
 w.eval(readFileSync(join(__dirname,'../static/reviewer-search.js'),'utf8'));await pause();d.querySelector('.reviewer-selection button').click();await pause();assert.equal(d.querySelectorAll('[role=option]').length,4);
 [...d.querySelectorAll('[role=option]')].find(n=>n.textContent.includes('Person')).click();assert.equal(d.querySelector('select').value,'7');d.querySelector('.reviewer-selection button').click();await pause();assert.equal(d.querySelector('select').value,'7');
 [...d.querySelectorAll('[role=option]')].find(n=>n.textContent==='Anyone').click();assert.equal(d.querySelector('select').value,'');dom.window.close();
});

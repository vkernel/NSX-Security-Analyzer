const {test}=require('node:test');const assert=require('node:assert/strict');const {JSDOM}=require('jsdom');const fs=require('node:fs');const path=require('node:path');
test('empty content is visible outside the wide table and restores after results arrive',async()=>{
 const dom=new JSDOM('<main id="main"><div class="table-scroll"><table><thead><tr><th>Finding</th></tr></thead><tbody><tr><td colspan="5" class="empty-state"><strong>No matching findings</strong><a href="?">Clear filters</a></td></tr></tbody></table></div></main>',{runScripts:'outside-only',pretendToBeVisual:true}),d=dom.window.document;
 dom.window.eval(fs.readFileSync(path.join(__dirname,'../static/interface-patterns.js'),'utf8'));assert.equal(d.querySelector('table').hidden,true);assert.match(d.querySelector('.table-empty-message').textContent,/No matching findings/);assert.equal(d.querySelector('.table-empty-message a').getAttribute('href'),'?');
 d.querySelector('tbody').innerHTML='<tr><td>Current finding</td></tr>';await new Promise(resolve=>dom.window.setTimeout(resolve,60));assert.equal(d.querySelector('table').hidden,false);assert.equal(d.querySelector('.table-empty-message'),null);dom.window.close();
});

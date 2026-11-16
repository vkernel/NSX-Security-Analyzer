const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {JSDOM}=require('jsdom');
function setup(){
 const dom=new JSDOM(`<p id="review-selection-status"></p><input type="checkbox" data-bulk-all="bulk-work"><input type="checkbox" data-bulk-all="bulk-cleanup"><input type="checkbox" data-review-kind="finding"><input type="checkbox" data-review-kind="cleanup"><span data-review-queue-count="assigned">2</span><section id="review-results"><template data-review-totals><span data-review-count="assigned">1</span></template></section>`,{runScripts:'outside-only'});
 dom.window.eval(fs.readFileSync(path.join(__dirname,'../static/reviews.js'),'utf8'));return dom;
}
test('review selections cannot mix finding decisions with cleanup-plan actions',()=>{
 const dom=setup(),d=dom.window.document,[finding,cleanup]=d.querySelectorAll('[data-review-kind]');
 finding.checked=true;cleanup.checked=true;cleanup.dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.equal(finding.checked,false);assert.equal(cleanup.checked,true);
 assert.match(d.getElementById('review-selection-status').textContent,/finding selection cleared/);
 d.querySelector('[data-bulk-all="bulk-work"]').checked=true;
 d.querySelector('[data-bulk-all="bulk-work"]').dispatchEvent(new dom.window.Event('change',{bubbles:true}));
 assert.equal(cleanup.checked,false);dom.window.close();
});
test('review queue badges take the same totals as the refreshed task table',()=>{
 const dom=setup(),d=dom.window.document;d.dispatchEvent(new dom.window.Event('region:updated'));
 assert.equal(d.querySelector('[data-review-queue-count="assigned"]').textContent,'1');dom.window.close();
});

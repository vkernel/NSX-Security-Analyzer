const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {JSDOM} = require('jsdom');
function setup() {
  const dom = new JSDOM(`<div id="finding-selection-bar" hidden><span id="finding-selection-count"></span><button id="assign-open"></button><button id="release-open"></button><button id="release-clear"></button></div>
    <input type="checkbox" id="finding-select-all">
    <dialog id="finding-release-dialog"><form id="bulk-release"><span id="release-title"></span><textarea id="release-reason"></textarea><button type="button" id="release-cancel"></button></form></dialog>
    <dialog id="finding-assignment-dialog"><form id="bulk-assignment"><h2 id="assignment-title"></h2><select name="owner"><option value=""></option><option value="12">Reviewer</option></select><span id="assignment-error" hidden></span><div data-assignment-selection></div><button type="button" id="assign-cancel"></button></form></dialog>
    <input type="checkbox" form="bulk-release" name="selected" value="1:2" data-assign-eligible="true" data-release-eligible="false">
    <input type="checkbox" form="bulk-release" name="selected" value="2:4" data-assign-eligible="true" data-release-eligible="false">`, {runScripts:'outside-only'});
  const w = dom.window;
  w.HTMLDialogElement.prototype.showModal = function(){this.open=true;};
  w.HTMLDialogElement.prototype.close = function(){this.open=false;this.dispatchEvent(new w.Event('close'));};
  w.eval(readFileSync(join(__dirname,'../static/finding-release.js'),'utf8'));
  return dom;
}
test('select all enables assignment, validates reviewer and submits selected revisions', () => {
  const dom=setup(), w=dom.window, d=w.document;
  const all=d.getElementById('finding-select-all'); all.checked=true; all.dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(d.getElementById('finding-selection-count').textContent,'2 selected on this page');
  assert.equal(d.getElementById('release-open').disabled,true);
  d.getElementById('assign-open').click();
  assert.equal(d.getElementById('finding-assignment-dialog').open,true);
  const form=d.getElementById('bulk-assignment');
  assert.equal(form.dispatchEvent(new w.Event('submit',{cancelable:true})),false);
  form.elements.owner.value='12';
  assert.equal(form.dispatchEvent(new w.Event('submit',{cancelable:true})),true);
  assert.deepEqual([...form.querySelectorAll('[name="selected"]')].map(x=>x.value),['1:2','2:4']);
  d.getElementById('assign-cancel').click();
  assert.equal(d.querySelectorAll('[type="checkbox"][name="selected"]:checked').length,2);
  d.getElementById('release-clear').click();
  assert.equal(d.getElementById('finding-selection-bar').hidden,true);
  dom.window.close();
});
test('mixed owned and unassigned selection cannot bulk assign', () => {
  const dom=setup(), w=dom.window, d=w.document;
  const boxes=d.querySelectorAll('[name="selected"]'); boxes[1].dataset.assignEligible='false';
  boxes.forEach(box=>{box.checked=true;box.dispatchEvent(new w.Event('change',{bubbles:true}));});
  assert.equal(d.getElementById('assign-open').disabled,true);
  dom.window.close();
});

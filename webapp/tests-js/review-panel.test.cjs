const {test} = require('node:test'), assert = require('node:assert/strict'), {JSDOM} = require('jsdom'), fs = require('node:fs'), path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/reviews.js'), 'utf8');

function fixture() {
  const dom = new JSDOM(`<body><div id="review-results"><div data-review-board data-selected="finding-1"><table><tbody>
    <tr data-review-key="finding-1" aria-current="true"><td><a data-review-select="finding-1" href="?queue=all&selected=finding-1">One</a></td></tr>
    <tr data-review-key="cleanup-2"><td><a data-review-select="cleanup-2" href="?queue=all&selected=cleanup-2">Two</a></td></tr></tbody></table>
    <article data-review-detail="finding-1" tabindex="-1"><input name="draft" value="kept"></article><article data-review-detail="cleanup-2" tabindex="-1" hidden></article>
    </div></div></body>`, {url: 'https://example.test/my-work/?queue=all', runScripts: 'outside-only'});
  dom.window.matchMedia = () => ({matches: true});
  dom.window.Element.prototype.scrollIntoView = function () {};
  dom.window.eval(source);
  return dom;
}

test('selecting a review row shows its panel in place and keeps the queue in the URL', () => {
  const dom = fixture(), w = dom.window, d = w.document;
  const event = new w.MouseEvent('click', {bubbles: true, cancelable: true});
  d.querySelector('[data-review-select="cleanup-2"]').dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(d.querySelector('[data-review-detail="cleanup-2"]').hidden, false);
  assert.equal(d.querySelector('[data-review-detail="finding-1"]').hidden, true);
  assert.equal(d.querySelector('[data-review-key="cleanup-2"]').getAttribute('aria-current'), 'true');
  assert.equal(d.querySelector('[data-review-key="finding-1"]').hasAttribute('aria-current'), false);
  assert.equal(d.activeElement.dataset.reviewDetail, 'cleanup-2');
  assert.equal(w.location.search, '?queue=all&selected=cleanup-2');
  assert.equal(d.querySelector('[name=draft]').value, 'kept');
  w.close();
});

test('modified clicks keep normal link behaviour and a refreshed region keeps the chosen panel', () => {
  const dom = fixture(), w = dom.window, d = w.document;
  const modified = new w.MouseEvent('click', {bubbles: true, cancelable: true, ctrlKey: true});
  d.querySelector('[data-review-select="cleanup-2"]').dispatchEvent(modified);
  assert.equal(modified.defaultPrevented, false);
  d.querySelector('[data-review-select="cleanup-2"]').click();
  // Live polling replaces the region with server markup that selected the first row.
  d.querySelector('[data-review-detail="cleanup-2"]').hidden = true;
  d.querySelector('[data-review-detail="finding-1"]').hidden = false;
  d.dispatchEvent(new w.Event('region:updated'));
  assert.equal(d.querySelector('[data-review-detail="cleanup-2"]').hidden, false);
  assert.equal(d.querySelector('[data-review-detail="finding-1"]').hidden, true);
  w.close();
});

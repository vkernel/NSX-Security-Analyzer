(() => {
  const bar = document.getElementById('work-deletion-selection');
  const form = document.getElementById('work-delete-preview');
  if (!bar || !form) return;
  const boxes = [...document.querySelectorAll('input[form="work-delete-preview"][name="selected"]')];
  const button = bar.querySelector('[data-work-preview]');
  const menu = bar.querySelector('details');
  const selected = () => boxes.filter(box => box.checked && !box.disabled);
  function update() {
    const rows = selected();
    const mixed = new Set(rows.map(box => box.dataset.environment)).size > 1;
    bar.hidden = rows.length === 0;
    bar.querySelector('[data-work-selection-count]').textContent = `${rows.length} selected`;
    button.disabled = !rows.length || rows.length > 100 || mixed;
    bar.querySelector('[data-work-selection-help]').textContent = mixed
      ? 'Select objects from one environment at a time, or use the Environment filter.'
      : rows.length > 100 ? 'Select no more than 100 objects per preview.'
      : 'Opens a read-only preview; explicit confirmation is required before deletion.';
    if (!rows.length) menu.open = false;
    return rows;
  }
  boxes.forEach(box => box.addEventListener('change', update));
  form.addEventListener('submit', event => {
    const rows = update();
    if (button.disabled) { event.preventDefault(); return; }
    form.action = rows[0].dataset.previewUrl;
    menu.open = false;
  });
  bar.querySelector('[data-work-clear]').addEventListener('click', () => {
    const first = selected()[0];
    boxes.forEach(box => { box.checked = false; });
    update();
    first?.focus();
  });
  window.addEventListener('pageshow', update);
  update();
})();

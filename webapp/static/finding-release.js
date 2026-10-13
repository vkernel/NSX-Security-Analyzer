(() => {
  const bar = document.getElementById('finding-selection-bar');
  if (!bar) return;
  const dialog = document.getElementById('finding-release-dialog');
  const form = document.getElementById('bulk-release');
  const open = document.getElementById('release-open');
  const checkboxes = [...document.querySelectorAll('input[form="bulk-release"][name="selected"]')];
  const selected = () => checkboxes.filter(box => box.checked && !box.disabled);
  const update = () => {
    const count = selected().length;
    bar.hidden = count === 0;
    document.getElementById('finding-selection-count').textContent = `${count} selected`;
    document.getElementById('release-title').textContent =
      `Return ${count} finding${count === 1 ? '' : 's'} to unassigned`;
  };
  checkboxes.forEach(box => box.addEventListener('change', update));
  open.addEventListener('click', () => {
    update();
    if (selected().length) dialog.showModal();
  });
  document.getElementById('release-cancel').addEventListener('click', () => dialog.close());
  document.getElementById('release-clear').addEventListener('click', () => {
    const first = selected()[0];
    checkboxes.forEach(box => { box.checked = false; });
    update();
    if (first) first.focus();
  });
  dialog.addEventListener('close', () => {
    document.getElementById('release-reason').value = '';
    open.focus();
  });
  form.addEventListener('submit', event => {
    if (!selected().length) {
      event.preventDefault();
      dialog.close();
      update();
    }
  });
  window.addEventListener('pageshow', update);
  update();
})();

(() => {
  const bar = document.getElementById('finding-selection-bar');
  if (!bar) return;
  const dialog = document.getElementById('finding-release-dialog');
  const form = document.getElementById('bulk-release');
  const open = document.getElementById('release-open');
  const checkboxes = [...document.querySelectorAll('input[form="bulk-release"][name="selected"]')];
  const selected = () => checkboxes.filter(box => box.checked && !box.disabled);
  const deletion = document.getElementById('delete-selected-open');
  const deleteForm = document.getElementById('bulk-delete-preview');
  const assign = document.getElementById('assign-open');
  const assignmentDialog = document.getElementById('finding-assignment-dialog');
  const assignmentForm = document.getElementById('bulk-assignment');
  const all = document.getElementById('finding-select-all');
  const menu = bar.querySelector('.selection-action-menu');
  const update = () => {
    const count = selected().length;
    bar.hidden = count === 0;
    document.dispatchEvent(new Event('bulk:updated'));
    open.disabled = !count || count > 100 || selected().some(box => box.dataset.releaseEligible === 'false');
    if (assign) assign.disabled = !count || count > 100 || selected().some(box => box.dataset.assignEligible !== 'true');
    if (all) {
      all.disabled = !checkboxes.length;
      all.checked = count > 0 && count === Math.min(checkboxes.length, 100);
      all.indeterminate = count > 0 && !all.checked;
    }
    if (!count && menu) menu.open = false;
    if (deletion) {
      deletion.disabled = count === 0 || count > 100 || selected().some(box => box.dataset.deleteEligible !== 'true');
      document.getElementById('delete-selection-help').textContent = deletion.disabled
        ? 'Select up to 100 assigned, ready findings of supported types to preview deletion.'
        : 'Opens a read-only preview; confirmation is required before deletion.';
    }
    document.getElementById('finding-selection-count').textContent = `${count} selected`;
    document.getElementById('release-title').textContent =
      `Return ${count} finding${count === 1 ? '' : 's'} to unassigned`;
  };
  checkboxes.forEach(box => box.addEventListener('change', update));
  all?.addEventListener('change', () => {
    checkboxes.forEach((box, index) => { box.checked = all.checked && index < 100; });
    update();
  });
  assign?.addEventListener('click', () => {
    update();
    if (assign.disabled) return;
    document.getElementById('assignment-title').textContent = `Assign ${selected().length} findings`;
    document.getElementById('assignment-error').hidden = true;
    if (menu) menu.open = false;
    assignmentDialog.showModal();
  });
  document.getElementById('assign-cancel')?.addEventListener('click', () => assignmentDialog.close());
  assignmentDialog?.addEventListener('close', () => assign.focus());
  assignmentForm?.addEventListener('submit', event => {
    update();
    if (assign.disabled || !assignmentForm.elements.owner.value) {
      event.preventDefault();
      document.getElementById('assignment-error').hidden = false;
      return;
    }
    const container = assignmentForm.querySelector('[data-assignment-selection]');
    container.replaceChildren();
    selected().forEach(box => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = 'selected'; input.value = box.value;
      container.appendChild(input);
    });
  });
  open.addEventListener('click', () => {
    update();
    if (menu) menu.open = false;
    if (!open.disabled) dialog.showModal();
  });
  if (deletion && deleteForm) deletion.addEventListener('click', () => {
    update();
    if (deletion.disabled) return;
    const container = deleteForm.querySelector('[data-delete-selection]');
    container.replaceChildren();
    selected().forEach(box => {
      const input = document.createElement('input');
      input.type = 'hidden'; input.name = 'selected'; input.value = box.value;
      container.appendChild(input);
    });
    if (menu) menu.open = false;
    deleteForm.requestSubmit();
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
    update();
    if (open.disabled) {
      event.preventDefault();
      dialog.close();
      update();
    }
  });
  window.addEventListener('pageshow', update);
  update();
})();

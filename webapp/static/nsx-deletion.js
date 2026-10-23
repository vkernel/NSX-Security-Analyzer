(() => {
  const dialog = document.getElementById('nsx-delete-dialog');
  const trigger = document.getElementById('delete-confirm-open');
  if (!dialog || !trigger) return;
  trigger.addEventListener('click', () => dialog.showModal());
  document.getElementById('delete-confirm-cancel').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { dialog.querySelector('form').reset(); trigger.focus(); });
})();

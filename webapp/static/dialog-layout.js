/* Shared action-dialog layout; evidence drawers keep their dedicated layout. */
(() => {
  let serial = 0;
  const enhance = dialog => {
    if (dialog.dataset.layoutReady || dialog.id === 'detail-dialog') return;
    const heading = dialog.querySelector('h2');
    const footer = dialog.querySelector('.finding-release-actions, .form-actions, .filter-actions');
    if (!heading || !footer) return;
    dialog.dataset.layoutReady = 'true'; dialog.classList.add('action-dialog');
    // Keep submit buttons attached to their original forms when moving the footer.
    footer.querySelectorAll('button,input').forEach(control => {
      if (control.form) {
        if (!control.form.id) control.form.id = `dialog-form-${++serial}`;
        control.setAttribute('form', control.form.id);
      }
    });
    const header = document.createElement('div'); header.className = 'action-dialog-header';
    const close = document.createElement('button'); close.type = 'button'; close.className = 'action-dialog-close';
    close.textContent = '×'; close.setAttribute('aria-label', 'Close dialog'); close.addEventListener('click', () => dialog.close());
    const oldHeader = heading.closest('.dialog-heading');
    header.append(heading, close);
    if (oldHeader) oldHeader.remove();
    footer.classList.add('action-dialog-footer'); footer.remove();
    const body = document.createElement('div'); body.className = 'action-dialog-body';
    body.append(...dialog.childNodes); dialog.append(header, body, footer);
  };
  const scan = () => document.querySelectorAll('dialog[open]').forEach(enhance);
  new MutationObserver(scan).observe(document.documentElement, {subtree:true, childList:true, attributes:true, attributeFilter:['open']});
  scan();
})();

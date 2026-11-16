/* Groups and VMs: on wide screens the shared evidence drawer docks beside the list
   instead of covering it. Evidence content, requests and authorization are unchanged;
   narrow screens keep the modal drawer. */
(() => {
  const DOCK_PANELS = ['all-groups', 'empty-groups', 'unused-groups', 'unknown-membership', 'all-vms'];
  const wide = () => !window.matchMedia || window.matchMedia('(min-width: 1280px)').matches;
  const panel = () => decodeURIComponent((location.hash || '').slice(1));
  const dockable = () => wide() && DOCK_PANELS.includes(panel());

  function selectRow(row) {
    document.querySelectorAll('.report-content tr[aria-current="true"]').forEach(node => node.removeAttribute('aria-current'));
    if (row) row.setAttribute('aria-current', 'true');
  }

  function init() {
    const dialog = document.getElementById('detail-dialog');
    if (!dialog || !document.body.classList.contains('snapshot-page')) return;
    const modal = HTMLDialogElement.prototype.showModal;
    dialog.showModal = function () {
      if (dockable()) {
        if (this.open && this.matches(':modal')) return;
        document.body.classList.add('evidence-docked');
        this.classList.add('is-docked');
        this.show();
        return;
      }
      document.body.classList.remove('evidence-docked');
      this.classList.remove('is-docked');
      return modal.call(this);
    };
    dialog.addEventListener('close', () => {
      document.body.classList.remove('evidence-docked');
      dialog.classList.remove('is-docked');
      selectRow(null);
    });
    // Remember which row opened the panel; window capture runs before the evidence handlers.
    window.addEventListener('click', event => {
      const trigger = event.target.closest('.report-content .detail-button, .report-content .row-open');
      if (trigger && trigger.closest(DOCK_PANELS.map(id => '#' + id).join(','))) selectRow(trigger.closest('tr'));
    }, true);
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !dialog.classList.contains('is-docked') || !dialog.open) return;
      if (document.querySelector('dialog[open]:modal')) return;
      event.preventDefault();
      dialog.close();
    });
    window.addEventListener('hashchange', () => { if (dialog.classList.contains('is-docked') && dialog.open && !dockable()) dialog.close(); });
    window.matchMedia?.('(min-width: 1280px)').addEventListener?.('change', () => { if (dialog.classList.contains('is-docked') && dialog.open && !wide()) dialog.close(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

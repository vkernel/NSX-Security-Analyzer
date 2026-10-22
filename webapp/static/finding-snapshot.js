/* Resolve exact identities in the saved snapshot, never by display name. */
(() => {
  const link = document.getElementById('finding-snapshot-link');
  if (!link) return;
  document.addEventListener('DOMContentLoaded', async () => {
    const status = link.querySelector('[role=status]');
    try {
      let record;
      if (window.reportIdentityRows) {
        const id = window.reportIdentityRows.findIndex(row => row.data.path === link.dataset.path);
        if (id < 0) throw new Error('This object is not available in this evaluated snapshot. Saved finding evidence remains available in the review.');
        record = {...window.reportIdentityRows[id], id};
      } else {
        record = await window.reportData(null, {op:'resolve', path:link.dataset.path, view:link.dataset.view||'', tag:link.dataset.tag||'', scope:link.dataset.scope||''});
        record = await window.reportData(null, {op:'detail', id:record.id});
      }
      const panel = record.view === 'dfw' ? ('rule_count' in record.data ? 'dfw-policies' : 'dfw-rules') : record.view === 'vms' ? 'all-vms' : record.view === 'tags' ? 'tags-all' : 'inventory';
      if (location.hash !== '#' + panel) {
        await new Promise(resolve => {
          window.addEventListener('hashchange', resolve, {once:true});
          location.hash = panel;
        });
      }
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'detail-button button';
      button.dataset.evidenceRow = record.id; button.dataset.title = link.dataset.name;
      button.textContent = 'View object details'; link.append(button);
      status.textContent = 'Showing the exact object from the selected snapshot.';
      const highlight = () => {
        document.querySelectorAll('.report-content .detail-button[data-evidence-row]').forEach(item => {
          if (item.dataset.evidenceRow === String(record.id)) item.closest('tr')?.classList.add('finding-target-row');
        });
      };
      document.addEventListener('table-render', highlight, true); highlight();
      button.click();
    } catch (error) {
      status.textContent = error.message;
    }
  }, {once:true});
})();

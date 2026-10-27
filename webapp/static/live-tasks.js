/* Refresh read-only task regions without reloading forms or changing selection. */
(() => {
  const interval = Math.max(5, Number(document.body.dataset.refreshSeconds) || 5) * 1000;
  const regions = () => [...document.querySelectorAll('[data-live-region][data-poll="true"]')];
  async function poll() {
    if (!regions().length) return;
    if (!document.hidden) {
      try {
        const response = await fetch(location.href, {credentials:'same-origin', cache:'no-store', headers:{'X-Requested-With':'task-status'}});
        if (!response.ok || response.redirected) throw new Error('Status unavailable. Your session may have expired.');
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        const pauses = [];
        let updated = 0;
        for (const region of regions()) {
          const next = doc.getElementById(region.id);
          if (!next) throw new Error('Unable to read the updated task status.');
          const reason = window.RefreshRegion.replace(region, next);
          if (reason) pauses.push(reason);
          else updated++;
        }
        const message = pauses.length ? `${updated ? 'Some statuses updated. ' : ''}${pauses[0]}` : 'Status updated.';
        document.querySelectorAll('[data-live-status]').forEach(node => { node.textContent = message; });
      } catch (error) {
        document.querySelectorAll('[data-live-status]').forEach(node => node.textContent = error.message + ' Retrying shortly.');
      }
    }
    setTimeout(poll, interval);
  }
  if (regions().length) setTimeout(poll, interval);
})();

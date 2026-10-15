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
        for (const region of regions()) {
          const next = doc.getElementById(region.id);
          // Never disturb a keyboard user or an expanded result they are reading.
          if (!next) throw new Error('Unable to read the updated task status.');
          if (region.contains(document.activeElement) || region.querySelector('details[open]')) continue;
          if (region.innerHTML !== next.innerHTML) region.replaceChildren(...next.childNodes);
          region.dataset.poll = next.dataset.poll;
        }
        document.querySelectorAll('[data-live-status]').forEach(node => { if (node.textContent !== 'Status updated.') node.textContent = 'Status updated.'; });
      } catch (error) {
        document.querySelectorAll('[data-live-status]').forEach(node => node.textContent = error.message + ' Retrying shortly.');
      }
    }
    setTimeout(poll, interval);
  }
  if (regions().length) setTimeout(poll, interval);
})();

/* Shared, bounded refresh for read-only task regions. Never resubmits a form. */
(() => {
  const interval = Math.max(5, Number(document.body.dataset.refreshSeconds) || 5) * 1000;
  const regions = (manual=false) => [...document.querySelectorAll(manual ? '[data-live-region]' : '[data-live-region][data-poll="true"]')];
  const controls = document.querySelector('[data-task-refresh-controls]');
  let paused = false, busy = false;
  const status = message => document.querySelectorAll('[data-live-status]').forEach(node => {node.textContent = message;});
  function updateControls() {
    if (!controls) return;
    controls.hidden = !document.querySelector('[data-live-region],[data-job]');
    const button = controls.querySelector('[data-task-pause]');
    button.textContent = paused ? 'Resume updates' : 'Pause updates';
    button.setAttribute('aria-pressed', String(paused));
    button.disabled = !regions().length && !document.querySelector('[data-job][data-status=queued],[data-job][data-status=running]');
  }
  async function poll(manual=false) {
    updateControls();
    if (busy || document.hidden || (paused && !manual) || !regions(manual).length) {
      if (!manual && regions().length) setTimeout(poll, interval);
      return;
    }
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(location.href, {credentials:'same-origin', cache:'no-store', signal:controller.signal, headers:{'X-Requested-With':'task-status'}});
      if (!response.ok || response.redirected) throw new Error('Status unavailable. Your session may have expired.');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const pauses = [];
      let updated = 0;
      for (const region of regions(manual)) {
        const next = doc.getElementById(region.id);
        if (!next) throw new Error('Unable to read the updated task status.');
        const reason = window.RefreshRegion.replace(region, next, {allowError:manual});
        if (reason) pauses.push(reason); else updated++;
      }
      status(pauses.length ? `${updated ? 'Some statuses updated. ' : ''}${pauses[0]}` :
        `${paused ? 'Automatic updates paused. ' : !regions().length ? 'No active tasks. ' : ''}Status updated at ${new Date().toLocaleTimeString()}.`);
    } catch (error) {
      status((error.name === 'AbortError' ? 'Status refresh timed out.' : error.message) + ' Background work is unaffected. Use Refresh now or wait for the next update.');
    } finally {
      clearTimeout(timeout); busy = false; updateControls();
      if (!manual && regions().length) setTimeout(poll, interval);
    }
  }
  document.addEventListener('click', event => {
    if (event.target.closest('[data-task-refresh]')) poll(true);
    if (event.target.closest('[data-task-pause]')) {
      paused = !paused; window.TaskRefreshPaused = paused; updateControls();
      status(paused ? 'Automatic updates paused. Background work continues.' : 'Automatic updates resumed.');
      if (!paused) poll(true);
    }
  });
  document.addEventListener('task-refresh-state', updateControls);
  updateControls();
  if (regions().length) setTimeout(poll, interval);
})();

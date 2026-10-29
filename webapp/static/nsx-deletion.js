(() => {
  let paused = false;
  let busy = false, failures=0, retryAt=0;
  const region = () => document.getElementById('deletion-progress');
  const status = (text) => {
    const node = region()?.querySelector('[data-refresh-status]');
    if (node) node.textContent = text;
  };
  async function refresh(manual = false) {
    if (busy || document.hidden || !region() || (!manual&&Date.now()<retryAt)) return;
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('fragment', '1');
      const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error('Refresh unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const next = page.getElementById('deletion-progress');
      if (!next) throw new Error('Missing status region');
      failures=0;retryAt=0;
      next.querySelectorAll('[data-refresh-deletion]').forEach(b=>b.hidden=true);
      if (paused && !manual) return;
      const reason = window.RefreshRegion.replace(region(), next, {allowError:manual});
      if (reason) { status(reason); return; }
      if (paused) {
        const toggle = next.querySelector('[data-toggle-refresh]');
        if (toggle) toggle.textContent = 'Resume updates';
        status('Automatic updates paused. Use Refresh now to update.');
      }
    } catch (_) {
      retryAt=Date.now()+Math.min(60000,5000*2**Math.min(++failures,4));
      region().querySelectorAll('[data-refresh-deletion]').forEach(b=>{b.hidden=false;b.textContent='Retry connection';});
      status('Unable to refresh. Your request continues in the background. Retrying automatically; you can also refresh this page.');
    } finally {
      clearTimeout(timeout);
      busy = false;
    }
  }
  document.addEventListener('click', (event) => {
    const open = event.target.closest('#delete-confirm-open');
    if (open) document.getElementById('nsx-delete-dialog')?.showModal();
    if (event.target.closest('#delete-confirm-cancel')) document.getElementById('nsx-delete-dialog')?.close();
    if (event.target.closest('[data-refresh-deletion]')) { event.preventDefault(); refresh(true); }
    const toggle = event.target.closest('[data-toggle-refresh]');
    if (toggle) {
      paused = !paused;
      toggle.textContent = paused ? 'Resume updates' : 'Pause updates';
      status(paused ? 'Automatic updates paused. Your request continues in the background.' : 'Automatic updates resumed.');
      if (!paused) refresh(true);
    }
  });
  document.addEventListener('close', (event) => {
    if (event.target.id === 'nsx-delete-dialog') document.getElementById('delete-confirm-open')?.focus();
  }, true);
  region()?.querySelectorAll('[data-refresh-deletion]').forEach(b=>b.hidden=true);
  window.addEventListener('online',()=>refresh(true));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&region()?.dataset.active==='true')refresh();});
  setInterval(() => {
    if (!paused && region()?.dataset.active === 'true') refresh();
  }, 5000);
})();

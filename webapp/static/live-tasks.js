/* Shared, bounded refresh for read-only task regions. Never resubmits a form. */
(() => {
  const interval = Math.max(5, Number(document.body.dataset.refreshSeconds) || 5) * 1000;
  const regions = (manual=false) => [...document.querySelectorAll('[data-live-region]')];
  const controls = document.querySelector('[data-task-refresh-controls]');
  let paused = false, busy = false, failures = 0, timer;
  const pending=new Map();
  const delay=()=>failures?Math.min(60000,interval*2**Math.min(failures,4)):regions().some(r=>r.dataset.poll==='true')?interval:30000;
  const schedule=()=>{clearTimeout(timer);timer=setTimeout(poll,delay());};
  const status = message => document.querySelectorAll('[data-live-status]:not([data-live-evidence] [data-live-status])').forEach(node => {node.textContent = message;});
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
      if (!manual && regions().length) schedule();
      return;
    }
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(location.href, {credentials:'same-origin', cache:'no-store', signal:controller.signal, headers:{'X-Requested-With':'task-status'}});
      if (!response.ok || response.redirected) throw new Error('Status unavailable. Your session may have expired.');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      failures=0;
      controls?.querySelector('[data-task-refresh]')?.setAttribute('hidden','');
      const pauses = [];
      let updated = 0;
      for (const region of regions(manual)) {
        const next = doc.getElementById(region.id);
        if (!next) throw new Error('Unable to read the updated task status.');
        const identities=node=>[...node.querySelectorAll('tbody tr')].map(row=>row.dataset.refreshKey||row.querySelector('a')?.getAttribute('href')||row.textContent.trim()).join('|');
        if(!manual && region.querySelector('tbody') && identities(region)!==identities(next)){
          pending.set(region.id,next);
          let button=document.querySelector(`[data-show-results="${region.id}"]`);
          if(!button){button=document.createElement('button');button.type='button';button.className='button';button.dataset.showResults=region.id;region.before(button);}
          button.textContent='Show new results';
          pauses.push('New results available. Your current list and selection are retained.');
          continue;
        }
        const reason = window.RefreshRegion.replace(region, next, {allowError:manual});
        if (reason) pauses.push(reason); else updated++;
      }
      status(pauses.length ? `${updated ? 'Some statuses updated. ' : ''}${pauses[0]}` :
        `${paused ? 'Automatic updates paused. ' : !regions().length ? 'No active tasks. ' : ''}Status updated at ${new Date().toLocaleTimeString()}.`);
    } catch (error) {
      failures++;
      const retry=controls?.querySelector('[data-task-refresh]');if(retry){retry.hidden=false;retry.textContent='Retry connection';}
      status((error.name === 'AbortError' ? 'Status refresh timed out.' : error.message) + ' Background work is unaffected. Reconnecting automatically.');
    } finally {
      clearTimeout(timeout); busy = false; updateControls();
      if (!manual && regions().length) schedule();
    }
  }
  document.addEventListener('click', event => {
    const show=event.target.closest('[data-show-results]');
    if(show){const id=show.dataset.showResults,next=pending.get(id);if(next){window.RefreshRegion.replace(document.getElementById(id),next);pending.delete(id);}show.remove();}
    if (event.target.closest('[data-task-refresh]')) poll(true);
    if (event.target.closest('[data-task-pause]')) {
      paused = !paused; window.TaskRefreshPaused = paused; updateControls();
      status(paused ? 'Automatic updates paused. Background work continues.' : 'Automatic updates resumed.');
      if (!paused) poll(true);
    }
  });
  document.addEventListener('task-refresh-state', updateControls);
  window.addEventListener('online',()=>poll(true));
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
  controls?.querySelector('[data-task-refresh]')?.setAttribute('hidden','');
  updateControls();
  if (regions().length) schedule();
})();

(() => {
  function highlightSettingsSection() {
    document.querySelectorAll('[data-section-link]').forEach(link => {
      const active = link.pathname === location.pathname && link.hash === (location.hash || '#display');
      if (active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }
  highlightSettingsSection();
  window.addEventListener('hashchange', highlightSettingsSection);
  const switcher = document.getElementById('snapshot-switcher');
  switcher?.addEventListener('change', () => { window.location.href = switcher.value + window.location.hash; });
  const refreshSeconds = [5,10,30,60].includes(Number(document.body.dataset.refreshSeconds)) ? Number(document.body.dataset.refreshSeconds) : 5;
  const active = Array.from(document.querySelectorAll('[data-job]'));
  if (!active.length) return;
  const status = document.querySelector('.poll-status');
  async function poll() {
    if (document.hidden) { setTimeout(poll, refreshSeconds * 1000); return; }
    try {
      const query = new URLSearchParams([...new Set(active.map(row => row.dataset.job))].map(id => ['id', id]));
      const response = await fetch('/api/jobs/?' + query, {headers: {Accept: 'application/json'}, cache: 'no-store'});
      if (!response.ok || response.redirected) throw new Error('Session unavailable');
      const data = await response.json();
      const states = new Map(data.jobs.map(job => [job.id, job]));
      active.forEach(row => {
        const job = states.get(row.dataset.job);
        if (!job?.progress) return;
        if (job.status !== row.dataset.status) {
          row.dataset.status = job.status;
          row.querySelectorAll('.pill').forEach(pill => { pill.textContent=job.label; pill.className='pill '+job.status; });
          if (!['queued','running'].includes(job.status)) {
            const outcome=document.createElement('p');outcome.setAttribute('role','status');outcome.textContent=job.progress.stage;
            const link=document.createElement('a');link.href=job.result_url || location.href;link.textContent=job.status==='succeeded'?'View collection result':'View environment and collection details';
            const target=row.querySelector('.collection-details,.table-action') || row;
            target.replaceChildren(outcome,link);
            row.querySelectorAll('.job-progress').forEach(widget=>widget.replaceWith(outcome.cloneNode(true)));
            return;
          }
        }

        row.querySelectorAll('.job-progress').forEach(widget => {
          const progress = job.progress;
          widget.querySelector('[data-progress-stage]').textContent = progress.stage;
          widget.querySelector('[data-progress-percent]').textContent = progress.percent + '%';
          const detail = progress.completed + ' of ' + progress.total + ' phases complete';

          const bar = widget.querySelector('progress');
          bar.max = progress.total;
          bar.value = progress.completed;
          bar.setAttribute('aria-valuetext', progress.stage + '; ' + detail);
        });
      });
      if (status) status.textContent = '';
    } catch (_) {
      if (status) status.textContent = 'Unable to refresh collection status. Retrying shortly; reload if your session has expired.';
    }
    if (active.some(row => ['queued','running'].includes(row.dataset.status))) setTimeout(poll, refreshSeconds * 1000);
  }
  setTimeout(poll, refreshSeconds * 1000);
})();

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
      if (active.some(row => states.has(row.dataset.job) && states.get(row.dataset.job).status !== row.dataset.status)) {
        window.location.reload(); return;
      }
      active.forEach(row => {
        const job = states.get(row.dataset.job);
        if (!job?.progress) return;
        row.querySelectorAll('.job-progress').forEach(widget => {
          const progress = job.progress;
          widget.querySelector('[data-progress-stage]').textContent = progress.stage;
          widget.querySelector('[data-progress-percent]').textContent = progress.percent + '%';
          const detail = progress.completed + ' of ' + progress.total + ' phases complete';
          widget.querySelector('[data-progress-detail]').textContent = detail;
          const bar = widget.querySelector('progress');
          bar.max = progress.total;
          bar.value = progress.completed;
          bar.setAttribute('aria-valuetext', progress.stage + '; ' + detail);
        });
      });
      if (status) status.textContent = 'Collection status updates automatically every '+refreshSeconds+' seconds.';
    } catch (_) {
      if (status) status.textContent = 'Unable to refresh collection status. Retrying shortly; reload if your session has expired.';
    }
    setTimeout(poll, refreshSeconds * 1000);
  }
  setTimeout(poll, refreshSeconds * 1000);
})();

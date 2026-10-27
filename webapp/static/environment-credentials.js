(() => {
  const button = document.querySelector('[data-test-credentials]');
  if (!button) return;
  const form = button.form;
  const status = form.querySelector('[data-credential-test-status]');
  let revision = 0;
  form.addEventListener('input', () => { revision++; status.textContent = 'Settings changed. Test again to verify these values.'; });
  form.addEventListener('submit', async event => {
    if (event.submitter !== button) return;
    event.preventDefault();
    const version = revision;
    const data = new FormData(form);
    data.set('action', 'test_credentials');
    button.disabled = true;
    status.textContent = 'Testing credentials… Nothing is being saved.';
    try {
      const response = await fetch(form.action || location.href, {method:'POST', body:data,
        credentials:'same-origin', headers:{'X-Requested-With':'XMLHttpRequest'}});
      if (!response.ok || response.redirected) throw new Error('Unavailable');
      const result = await response.json();
      if (revision !== version) return;
      status.textContent = result.message;
      if (result.errors) {
        const errors = Object.entries(result.errors).flatMap(([field, entries]) => entries.map(entry => `${field}: ${entry.message}`));
        if (errors.length) status.textContent += ' ' + errors.join(' ');
      }
    } catch (_) {
      if (revision === version) status.textContent = 'The test could not complete. Check your connection or sign-in and try again. Nothing was saved.';
    } finally { button.disabled = false; }
  });
})();

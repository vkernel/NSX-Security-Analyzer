(() => {
  const dialog = document.getElementById('saved-configuration-dialog');
  let opener;
  let loading = false;
  if (dialog) {
    dialog.querySelector('[data-close-configuration]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => opener?.focus());
  }
  document.addEventListener('click', async (event) => {
    const link = event.target.closest('[data-configuration-view]');
    if (link && dialog && typeof dialog.showModal === 'function' && !event.ctrlKey && !event.metaKey && !event.shiftKey && event.button === 0) {
      event.preventDefault();
      if (loading) return;
      loading = true;
      opener = link;
      try {
        const response = await fetch(link.href, {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok || response.redirected) throw new Error('Unavailable');
        const page = new DOMParser().parseFromString(await response.text(), 'text/html');
        const content = page.querySelector('#saved-configuration-content');
        if (!content) throw new Error('Unavailable');
        dialog.querySelector('[data-configuration-body]').replaceChildren(content);
        dialog.showModal();
      } catch (_) {
        window.location.assign(link.href);
      } finally { loading = false; }
    }
    const copy = event.target.closest('[data-copy-configuration]');
    if (!copy) return;
    const section = copy.closest('#saved-configuration-content');
    const json = section.querySelector('[data-configuration-json]');
    const status = section.querySelector('[data-copy-status]');
    try {
      await navigator.clipboard.writeText(json.textContent);
      status.textContent = 'JSON copied.';
    } catch (_) {
      const range = document.createRange();
      range.selectNodeContents(json);
      const selection = window.getSelection();
      selection.removeAllRanges(); selection.addRange(range);
      status.textContent = 'Clipboard unavailable. JSON selected; use your browser’s Copy command.';
      json.focus();
    }
  });
})();

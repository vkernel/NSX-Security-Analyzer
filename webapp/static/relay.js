/* Relay 04: the module rail selects a workspace; the second panel shows its views. */
(() => {
  'use strict';
  function init() {
    const panel = document.querySelector('[data-relay-section]');
    if (!panel) return;
    const main = document.getElementById('main');
    const target = panel.querySelector('[data-section-links]');
    const title = panel.querySelector('h2');
    const fallback = target.innerHTML;
    let signature = '';
    const selectors = '.report-section-tabs,.seg-primary,.history-navigation,.admin-tabs,.admin-subtabs,.settings-navigation,nav.page-tabs';
    function sync() {
      const active = document.querySelector('.workspace-module-rail [data-primary][aria-current]');
      if (active?.dataset.moduleLabel) title.textContent = active.dataset.moduleLabel;
      // Read the same anchors as the page, including snapshot hashes and validated filters.
      const sources = [...main.querySelectorAll(selectors)].filter(nav => !nav.closest('dialog'));
      const next = sources.map(nav => nav.outerHTML).join('');
      if (next === signature) return;
      signature = next;
      if (!sources.length) { target.innerHTML = fallback; return; }
      const groups = sources.map(source => {
        const nav = document.createElement('nav'); nav.className = 'section-link-group';
        const label = source.getAttribute('aria-label') || 'Current views';
        nav.setAttribute('aria-label', label);
        const heading = document.createElement('h3'); heading.textContent = label; nav.append(heading);
        source.querySelectorAll('a[href]').forEach(link => nav.append(link.cloneNode(true)));
        return nav;
      });
      target.replaceChildren(...groups);
      sources.forEach(source => source.classList.add('relay-context-source'));
      if (main.querySelector('.settings-navigation')) main.classList.add('relay-settings-context');
    }
    sync(); window.addEventListener('hashchange', sync);
    let scheduled;
    new MutationObserver(records => {
      if (!records.some(record => record.target.closest?.(selectors) || [...record.addedNodes].some(node => node.nodeType === 1 && (node.matches(selectors) || node.querySelector(selectors))))) return;
      clearTimeout(scheduled); scheduled = setTimeout(sync, 80);
    }).observe(main, {childList: true, subtree: true});

    const picker = document.querySelector('[data-theme-url]');
    const buttons = [...picker.querySelectorAll('[data-relay-theme]')];
    const status = picker.querySelector('[role=status]');
    new MutationObserver(() => buttons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.relayTheme === document.body.dataset.theme)))).observe(document.body, {attributes: true, attributeFilter: ['data-theme']});
    buttons.forEach(button => button.addEventListener('click', async () => {
      const before = document.body.dataset.theme;
      const theme = button.dataset.relayTheme;
      document.body.dataset.theme = theme;
      buttons.forEach(item => { item.disabled = true; item.setAttribute('aria-pressed', String(item === button)); });
      status.textContent = 'Saving appearance…';
      try {
        const csrf = document.querySelector('#interface-csrf input').value;
        const response = await fetch(picker.dataset.themeUrl, {method: 'POST', headers: {'X-CSRFToken': csrf}, body: new URLSearchParams({theme}), signal: AbortSignal.timeout(15000)});
        if (!response.ok || response.redirected) throw new Error();
        const saved = await response.json();
        if (saved.theme !== theme) throw new Error();
        const field = document.getElementById('id_theme'); if (field) {field.value = theme;document.querySelectorAll('.theme-choices button').forEach((choice, index) => choice.setAttribute('aria-pressed', String(field.options[index].value === theme)));}
        status.textContent = 'Appearance saved';
      } catch (_) {
        document.body.dataset.theme = before;
        buttons.forEach(item => item.setAttribute('aria-pressed', String(item.dataset.relayTheme === before)));
        status.textContent = 'Appearance could not be saved. Try again.';
      } finally { buttons.forEach(item => item.disabled = false); }
    }));
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

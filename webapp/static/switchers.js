/* Progressive enhancement: native selects remain available without JavaScript. */
document.addEventListener('DOMContentLoaded', () => {
  for (const id of ['environment-switcher', 'snapshot-switcher']) {
    const select = document.getElementById(id);
    if (!select || select.dataset.enhanced === 'true') continue;
    select.dataset.enhanced = 'true';
    if (select.hasAttribute('data-scope-navigation')) {
      const status = select.parentElement.querySelector('.scope-status');
      const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'button scope-retry'; retry.textContent = 'Retry'; retry.hidden = true; status.after(retry);
      const original = select.value;
      let controller, pendingTarget, generation = 0;
      const reset = () => { select.value = original; };
      const navigate = async () => {
        const target = pendingTarget;
        const attempt = ++generation;
        controller?.abort(); controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        retry.hidden = true; status.textContent = 'Checking selected environment…';
        try {
          const response = await fetch(target.href, {credentials:'same-origin', cache:'no-store', signal:controller.signal, headers:{'X-Environment-Check':'1'}});
          if (!response.ok || new URL(response.url || target.href).origin !== location.origin || /\/login\//.test(response.url)) throw new Error('unavailable');
          if (attempt !== generation) return;
          status.textContent = 'Opening selected environment…';
          const navigation = window.WorkspaceNavigation?.start(response.url || target.href);
          try { location.assign(target.href); }
          catch (error) { window.WorkspaceNavigation?.cancel(navigation); throw error; }
          // Keep the destination marker while a slow page loads; it cannot affect another URL.
          setTimeout(() => { reset(); status.textContent = ''; }, 500);
        } catch (error) {
          if (attempt !== generation) return;
          reset(); status.textContent = 'Could not open that environment. Your current page is unchanged.';
          retry.hidden = false;
        } finally { clearTimeout(timer); }
      };
      select.addEventListener('change', () => {
        const option = select.selectedOptions[0];
        if (!option?.dataset.url) return;
        pendingTarget = new URL(option.dataset.url, location.origin);
        if (select.dataset.browsing === 'true' && location.hash) pendingTarget.searchParams.set('panel', location.hash.slice(1));
        navigate();
      });
      retry.addEventListener('click', navigate);
      window.addEventListener('pageshow', event => { if (event.persisted) { reset(); status.textContent = ''; retry.hidden = true; } });
      window.addEventListener('pagehide', () => { ++generation; controller?.abort(); });
    }
    const caption = id === 'environment-switcher' ? 'Environment' : 'Snapshot';
    const root = document.createElement('details');
    root.className = 'workspace-switcher';
    const trigger = document.createElement('summary');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-label', caption + ': ' + select.selectedOptions[0]?.textContent);
    trigger.textContent = select.selectedOptions[0]?.textContent || 'Select ' + caption.toLowerCase();
    const panel = document.createElement('div'); panel.className = 'switcher-panel';
    const search = document.createElement('input'); search.type = 'search';
    search.placeholder = 'Find ' + (caption === 'Environment' ? 'an environment' : 'a snapshot');
    search.setAttribute('aria-label', search.placeholder);
    const list = document.createElement('div'); list.className = 'switcher-options';
    const empty = document.createElement('p'); empty.textContent = 'No matches'; empty.hidden = true;
    for (const option of select.options) {
      if (!option.value && !option.dataset.url) continue;
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = option.textContent;
      if (option.selected) button.setAttribute('aria-current', 'true');
      button.addEventListener('click', () => {
        select.value = option.value;
        root.open = false; select.dispatchEvent(new Event('change', {bubbles:true}));
      });
      list.append(button);
    }
    search.addEventListener('input', () => {
      const term = search.value.trim().toLocaleLowerCase();
      for (const button of list.children) button.hidden = !button.textContent.toLocaleLowerCase().includes(term);
      empty.hidden = Array.from(list.children).some(button => !button.hidden);
    });
    root.addEventListener('toggle', () => {
      trigger.setAttribute('aria-expanded', String(root.open));
      if (root.open) {
        document.querySelectorAll('.workspace-switcher').forEach(other => {if (other !== root) other.open = false;});
        search.focus();
      }
    });
    root.addEventListener('keydown', event => {
      if (event.key === 'Escape') {root.open = false; trigger.focus(); event.preventDefault();}
      if (['ArrowDown','ArrowUp'].includes(event.key) && root.open) {
        const targets = [search,...Array.from(list.children).filter(button => !button.hidden)];
        const direction = event.key === 'ArrowDown' ? 1 : -1;
        targets[(targets.indexOf(document.activeElement) + direction + targets.length) % targets.length].focus();
        event.preventDefault();
      }
    });
    document.addEventListener('click', event => {if (!root.contains(event.target)) root.open = false;});
    // Safari does not focus buttons on pointer clicks. Closing on focusout can
    // remove the option between pointer-down and click, cancelling navigation.
    document.addEventListener('focusin', event => {
      if (!root.contains(event.target)) root.open = false;
    });
    panel.append(search,list,empty); root.append(trigger,panel); select.after(root); select.hidden = true;
    const label = select.parentElement.querySelector('label');
    label.removeAttribute('for');
  }
});

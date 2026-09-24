/* Progressive enhancement: native selects remain available without JavaScript. */
document.addEventListener('DOMContentLoaded', () => {
  for (const id of ['environment-switcher', 'snapshot-switcher']) {
    const select = document.getElementById(id);
    if (!select) continue;
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
      if (!option.value) continue;
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = option.textContent;
      if (option.selected) button.setAttribute('aria-current', 'true');
      button.addEventListener('click', () => {
        select.value = option.value; trigger.textContent = option.textContent;
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

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('select[data-user-search]').forEach(select => {
    if (select.hasAttribute('data-reviewer-combobox')) {
      const wrapper = document.createElement('div'); wrapper.className = 'reviewer-combobox';
      const input = document.createElement('input'); input.type = 'text'; input.autocomplete = 'off';
      input.id = select.id + '-search'; input.placeholder = 'Search and select by name or email';
      input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-expanded', 'false');
      const list = document.createElement('ul'); list.id = select.id + '-results';
      list.className = 'reviewer-options'; list.setAttribute('role', 'listbox'); list.hidden = true;
      input.setAttribute('aria-controls', list.id);
      const label = document.querySelector(`label[for="${select.id}"]`);
      if (label) label.htmlFor = input.id;
      const help = select.getAttribute('aria-describedby'); if (help) input.setAttribute('aria-describedby', help);
      input.value = select.value ? select.selectedOptions[0].textContent : '';
      const status = document.createElement('small'); status.setAttribute('role', 'status');
      select.before(wrapper); wrapper.append(input, list, status); select.hidden = true;
      let timer, pending, generation = 0, active = -1, options = [];
      const close = () => { list.hidden = true; input.setAttribute('aria-expanded', 'false'); input.removeAttribute('aria-activedescendant'); active = -1; };
      const choose = item => {
        ++generation; clearTimeout(timer); pending?.abort();
        const value = String(item.id);
        if (!Array.from(select.options).some(option => option.value === value)) select.add(new Option(item.label, value));
        select.value = value; input.value = item.label; select.dispatchEvent(new Event('change', {bubbles:true}));
        status.textContent = 'Reviewer selected.'; close(); input.focus();
      };
      const highlight = () => {
        [...list.children].forEach((node, index) => node.setAttribute('aria-selected', String(index === active)));
        if (active >= 0) { input.setAttribute('aria-activedescendant', list.children[active].id); list.children[active].scrollIntoView({block:'nearest'}); }
      };
      async function search() {
        const token = ++generation; pending?.abort(); pending = new AbortController();
        status.textContent = 'Searching…'; close();
        try {
          const response = await fetch(select.dataset.userSearch + '?q=' + encodeURIComponent(input.value), {signal:pending.signal, headers:{Accept:'application/json'}});
          if (!response.ok) throw new Error('Search unavailable. Try again.');
          const data = await response.json(); if (token !== generation) return;
          options = data.items; list.replaceChildren();
          options.forEach((item, index) => {
            const option = document.createElement('li'); option.id = list.id + '-' + index;
            option.setAttribute('role', 'option'); option.setAttribute('aria-selected', 'false'); option.textContent = item.label;
            option.addEventListener('mousedown', event => event.preventDefault());
            option.addEventListener('click', () => choose(item)); list.append(option);
          });
          list.hidden = !options.length; input.setAttribute('aria-expanded', String(options.length > 0));
          status.textContent = data.has_more ? 'First 20 matches. Refine your search.' : options.length ? 'Select a matching reviewer.' : 'No matching reviewers.';
        } catch (error) { if (error.name !== 'AbortError' && token === generation) status.textContent = error.message; }
      }
      input.addEventListener('input', () => { select.value = ''; ++generation; pending?.abort(); close(); clearTimeout(timer); timer = setTimeout(search, 250); });
      input.addEventListener('focus', () => { if (!select.value) search(); });
      input.addEventListener('blur', () => { ++generation; clearTimeout(timer); pending?.abort(); close(); });
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !list.hidden) { event.preventDefault(); event.stopPropagation(); close(); }
        else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          event.preventDefault();
          if (list.hidden) { search(); return; }
          active = (active + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length; highlight();
        } else if (event.key === 'Enter' && !list.hidden) {
          event.preventDefault(); if (active >= 0) choose(options[active]);
        }
      });
      return;
    }
    const input = document.createElement('input'); input.type = 'search'; input.placeholder = 'Search by name or email'; input.setAttribute('aria-label', 'Search reviewers by name or email');
    const status = document.createElement('small'); status.setAttribute('role', 'status');
    select.before(input); select.after(status);
    let timer, pending, generation = 0;
    async function search() {
      const token = ++generation; pending?.abort(); pending = new AbortController(); status.textContent = 'Searching…';
      try {
        const response = await fetch(select.dataset.userSearch + '?q=' + encodeURIComponent(input.value), {signal:pending.signal, headers:{Accept:'application/json'}});
        if (!response.ok) throw new Error('Search unavailable. Try again.');
        const data = await response.json(); if(token !== generation) return;
        const chosen = select.value;
        const keep = Array.from(select.options).filter(o => !/^\d+$/.test(o.value) || o.value === chosen).map(o => o.cloneNode(true));
        select.replaceChildren(...keep);
        for(const item of data.items) if(!Array.from(select.options).some(o => o.value === String(item.id))) select.add(new Option(item.label, item.id));
        select.value = chosen;
        status.textContent = data.has_more ? 'First 20 matches. Refine your search.' : data.items.length ? 'Choose a reviewer below.' : 'No matching reviewers.';
      } catch(error) { if(error.name !== 'AbortError' && token === generation) status.textContent = error.message; }
    }
    input.addEventListener('input', () => {++generation; pending?.abort(); clearTimeout(timer); timer = setTimeout(search, 250);});
    input.addEventListener('keydown', event => {if(event.key === 'Enter'){event.preventDefault();clearTimeout(timer);search();}});
    input.addEventListener('focus', () => {if(!pending) search();});
  });
});

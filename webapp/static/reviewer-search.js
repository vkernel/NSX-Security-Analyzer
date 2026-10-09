document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('select[data-user-search]').forEach(select => {
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

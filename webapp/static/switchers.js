/* Context navigation: one document request, with native choices as fallback. */
document.addEventListener('DOMContentLoaded', () => {
  const select = document.getElementById('environment-switcher');
  if (!select || select.dataset.enhanced === 'true') return;
  select.dataset.enhanced = 'true';
  const status = select.parentElement.querySelector('.scope-status');
  const original = select.value;
  const root = document.createElement('details'); root.className = 'workspace-switcher';
  const trigger = document.createElement('summary');
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-label', 'Change environment: ' + select.selectedOptions[0]?.textContent);
  const selectedLabel=document.createElement('span');selectedLabel.className='context-selected';selectedLabel.textContent=select.selectedOptions[0]?.textContent||'Choose environment';trigger.append(selectedLabel);
  const panel = document.createElement('div'); panel.className = 'switcher-panel';
  const label = document.createElement('label'); label.textContent = 'Switch environment';
  const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Find an environment';
  search.setAttribute('aria-label', 'Find an environment'); label.append(search);
  const list = document.createElement('div'); list.className = 'switcher-options';
  const empty = document.createElement('p'); empty.textContent = 'No matching environments'; empty.hidden = true;
  const recent = [], other = [];
  for (const option of select.options) {
    const button = document.createElement('button'); button.type = 'button';button.textContent = option.textContent;
    if (option.selected) button.setAttribute('aria-current','true');
    button.addEventListener('click', () => {select.value = option.value;root.open = false;select.dispatchEvent(new Event('change',{bubbles:true}));});
    const rank = Number(option.dataset.recentRank);
    if (Number.isInteger(rank) && rank > 0 && !option.selected) recent.push({button,rank});
    else other.push(button);
  }
  if (recent.length) {
    for (const [title,buttons] of [['Recently visited',recent.sort((a,b)=>a.rank-b.rank).map(item=>item.button)],['All other environments',other]]) {
      if(!buttons.length)continue;
      const group=document.createElement('section'),heading=document.createElement('h3');group.className='switcher-group';group.setAttribute('aria-label',title);heading.textContent=title;group.append(heading,...buttons);list.append(group);
    }
  } else list.append(...other);
  const buttons=[...list.querySelectorAll('button')];
  select.addEventListener('change', () => {
    const option = select.selectedOptions[0]; if (!option?.dataset.url) return;
    const target = new URL(option.dataset.url, location.origin);if(target.origin !== location.origin)return;
    if(select.dataset.browsing === 'true' && location.hash) {
      if(target.pathname.startsWith('/snapshots/'))target.hash = location.hash;
      else target.searchParams.set('panel',location.hash.slice(1));
    }
    if (status) status.textContent = 'Opening ' + option.textContent + '…';
    const stamp = window.WorkspaceNavigation?.start(target.href);
    try { location.assign(target.href); }
    catch (_) {window.WorkspaceNavigation?.cancel(stamp);select.value=original;if(status)status.textContent='Unable to navigate. Choose the environment again or use Environments.';}
  });
  search.addEventListener('input',()=>{const term=search.value.trim().toLocaleLowerCase();for(const b of buttons)b.hidden=!b.textContent.toLocaleLowerCase().includes(term);for(const group of list.querySelectorAll('.switcher-group'))group.hidden=![...group.querySelectorAll('button')].some(b=>!b.hidden);empty.hidden=buttons.some(b=>!b.hidden);});
  root.addEventListener('toggle',()=>{trigger.setAttribute('aria-expanded',String(root.open));if(root.open&&(document.activeElement===trigger||!root.contains(document.activeElement)))search.focus();});
  root.addEventListener('keydown',event=>{
    if(event.key==='Escape'){root.open=false;trigger.focus();event.preventDefault();}
    if(['ArrowDown','ArrowUp'].includes(event.key)&&root.open){const targets=[search,...buttons.filter(b=>!b.hidden)],delta=event.key==='ArrowDown'?1:-1;targets[(targets.indexOf(document.activeElement)+delta+targets.length)%targets.length].focus();event.preventDefault();}
  });
  document.addEventListener('click',e=>{if(!root.contains(e.target))root.open=false;});
  document.addEventListener('focusin',e=>{if(!root.contains(e.target))root.open=false;});
  window.addEventListener('pageshow',e=>{if(e.persisted){select.value=original;if(status)status.textContent='';}});
  panel.append(label,list,empty);root.append(trigger,panel);select.after(root);select.hidden=true;
});

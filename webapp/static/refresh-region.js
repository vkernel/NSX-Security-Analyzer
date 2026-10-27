/* Preserve reading state while replacing server-rendered, read-only status regions. */
(() => {
  function key(node, root) {
    if (node === root) return 'root';
    const anchor = node.closest('[data-refresh-key], [id]');
    if (anchor && anchor !== root && root.contains(anchor)) {
      const identity = anchor.dataset.refreshKey || anchor.id;
      return `anchor:${identity}/${node === anchor ? '' : key(node, anchor)}`;
    }
    if (node.matches('summary')) return `summary:${key(node.parentElement, root)}`;
    const identity = node.getAttribute('name') || node.getAttribute('href') ||
      (node.matches('details') ? node.querySelector(':scope > summary')?.textContent.trim() : '');
    if (identity) return `${node.tagName}:${identity}`;
    const path=[];
    while (node && node !== root) {
      path.unshift([...node.parentElement.children].indexOf(node));
      node=node.parentElement;
    }
    return path.join('/');
  }
  function blocked(root, {allowError=false}={}) {
    if (!allowError && root.querySelector('[data-preserve-error]')) return 'Updates paused to keep the error visible. Review it, then use Refresh now.';
    if (document.querySelector('dialog[open]')) return 'Updates paused while a dialog is open. Background work continues.';
    if ([...root.querySelectorAll('input[type="file"]')].some(node=>node.files?.length))
      return 'Updates paused to preserve your selected file. Background work continues.';
    if (root.contains(document.activeElement) && document.activeElement.matches('input, textarea, select, [contenteditable="true"]'))
      return 'Updates paused while you edit a field. Background work continues.';
    return '';
  }
  function replace(root, next, options={}) {
    const reason=blocked(root, options);
    if (reason) return reason;
    const active=root.contains(document.activeElement) ? key(document.activeElement, root) : null;
    const details=new Map([...root.querySelectorAll('details')].map(node=>[key(node,root),node.open]));
    const fields=new Map([...root.querySelectorAll('input:not([type="hidden"]),textarea,select')].map(node=>[key(node,root),
      {value:node.value,checked:node.checked}]));
    const scroll=new Map([...root.querySelectorAll('*')].filter(node=>node.scrollTop || node.scrollLeft)
      .map(node=>[key(node,root),[node.scrollLeft,node.scrollTop]]));
    next.querySelectorAll('details').forEach(node=>{const saved=details.get(key(node,next));if(saved!==undefined)node.open=saved;});
    next.querySelectorAll('input:not([type="hidden"]),textarea,select').forEach(node=>{
      const saved=fields.get(key(node,next));
      if(saved && node.type!=='file'){node.value=saved.value;if('checked' in node)node.checked=saved.checked;}
    });
    const x=window.scrollX,y=window.scrollY;
    root.replaceChildren(...next.childNodes);
    for (const name of ['data-poll','data-active','data-status']) {
      if(next.hasAttribute(name)) root.setAttribute(name,next.getAttribute(name));
    }
    let target;
    root.querySelectorAll('*').forEach(node=>{
      const identity=key(node,root),position=scroll.get(identity);
      if(position){node.scrollLeft=position[0];node.scrollTop=position[1];}
      if(identity===active)target=node;
    });
    if(active){
      if(!target){root.tabIndex=-1;target=root;}
      target.focus({preventScroll:true});
    }
    window.scrollTo(x,y);
    document.dispatchEvent(new Event('bulk:updated'));
    return '';
  }
  window.RefreshRegion={blocked,replace};
})();

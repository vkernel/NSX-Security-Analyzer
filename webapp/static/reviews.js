/* A selection is either finding reviews or cleanup plans, never both. */
(() => {
  const totals=()=>{
    document.querySelector('#review-results [data-review-totals]')?.content.querySelectorAll('[data-review-count]').forEach(value=>{
      document.querySelectorAll('[data-review-queue-count]').forEach(target=>{
        if(target.dataset.reviewQueueCount===value.dataset.reviewCount)target.textContent=value.textContent;
      });
    });
  };
  document.addEventListener('region:updated',totals);
  // List rows select the side panel in place; the link still works without scripts.
  let chosen=null;
  const select=(key,{focus=false,remember=true}={})=>{
    const board=document.querySelector('[data-review-board]');if(!board)return;
    const panels=[...board.querySelectorAll('[data-review-detail]')];
    const panel=panels.find(node=>node.dataset.reviewDetail===key)||panels[0];if(!panel)return;
    panels.forEach(node=>{node.hidden=node!==panel;});
    board.querySelectorAll('[data-review-key]').forEach(row=>{if(row.dataset.reviewKey===panel.dataset.reviewDetail)row.setAttribute('aria-current','true');else row.removeAttribute('aria-current');});
    board.dataset.selected=panel.dataset.reviewDetail;
    if(remember){chosen=panel.dataset.reviewDetail;const url=new URL(location.href);url.searchParams.set('selected',chosen);url.hash='';history.replaceState(history.state,'',url);}
    if(focus){panel.focus({preventScroll:true});if(!window.matchMedia||!window.matchMedia('(min-width: 1100px)').matches)panel.scrollIntoView({block:'start'});}
  };
  document.addEventListener('click',event=>{
    const link=event.target.closest('a[data-review-select]');
    if(!link||event.button||event.ctrlKey||event.metaKey||event.shiftKey)return;
    event.preventDefault();select(link.dataset.reviewSelect,{focus:true});
  });
  document.addEventListener('region:updated',()=>{if(chosen)select(chosen,{remember:false});});
  document.addEventListener('change', event => {
    const box=event.target;
    if(!box.matches('[data-review-kind],[data-bulk-all="bulk-work"],[data-bulk-all="bulk-cleanup"]'))return;
    if(!box.checked){
      if(!document.querySelector('[data-review-kind]:checked')){const status=document.getElementById('review-selection-status');if(status)status.textContent='';}
      return;
    }
    const kind=box.dataset.reviewKind||(box.dataset.bulkAll==='bulk-work'?'finding':'cleanup');
    let cleared=false;
    document.querySelectorAll('[data-review-kind]').forEach(other=>{
      if(other.dataset.reviewKind!==kind&&other.checked){other.checked=false;cleared=true;}
    });
    if(cleared){const status=document.getElementById('review-selection-status');if(status)status.textContent=kind==='finding'?'Finding reviews selected; cleanup selection cleared.':'Cleanup plans selected; finding selection cleared.';}
    document.dispatchEvent(new Event('bulk:updated'));
  });
})();

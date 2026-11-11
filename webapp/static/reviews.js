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

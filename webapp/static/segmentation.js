/* Notify about newer evidence without replacing the snapshot being reviewed. */
(()=>{const el=document.querySelector('[data-segmentation-status]');if(!el)return;
let timer;async function check(){try{if(!document.hidden){const response=await fetch(el.dataset.url,{credentials:'same-origin',headers:{Accept:'application/json'}});if(response.ok){const data=await response.json();document.getElementById('seg-new').hidden=!data.latest||data.latest===el.dataset.snapshot;}}}catch(_){}finally{timer=setTimeout(check,30000);}}
window.addEventListener('pagehide',()=>clearTimeout(timer));check();})();

/* Relay shell: server navigation; a bounded adapter for retained hash-based reports. */
(() => {
  'use strict';
  function init() {
    const panel=document.querySelector('[data-relay-section]'), main=document.getElementById('main');
    function reportNavigation(){
      const source=main?.querySelector('.report-section-tabs');if(!source||!panel)return;
      const target=panel.querySelector('[data-section-links]'),nav=document.createElement('nav');nav.className='section-link-group';nav.setAttribute('aria-label','Report pages');
      source.querySelectorAll('a[href]').forEach(link=>nav.append(link.cloneNode(true)));target.replaceChildren(nav);source.classList.add('relay-context-source');
      const active=document.querySelector('.workspace-module-rail [data-primary][aria-current]');if(active?.dataset.moduleLabel)panel.querySelector('h2').textContent=active.dataset.moduleLabel;
    }
    reportNavigation();window.addEventListener('hashchange',reportNavigation);document.addEventListener('report-navigation-ready',reportNavigation);
    const toggle=document.querySelector('.relay-theme-toggle'),status=document.querySelector('[data-relay-theme-status]');
    if(toggle){
      const media=matchMedia('(prefers-color-scheme: dark)');
      const effective=()=>document.body.dataset.theme==='system'?(media.matches?'dark':'light'):document.body.dataset.theme;
      function paint(){const dark=effective()==='dark',label=dark?'Switch to light mode':'Switch to dark mode';toggle.setAttribute('aria-label',label);toggle.title=label;toggle.querySelector('[data-theme-moon]')?.toggleAttribute('hidden',dark);toggle.querySelector('[data-theme-sun]')?.toggleAttribute('hidden',!dark);}
      paint();media.addEventListener('change',paint);new MutationObserver(paint).observe(document.body,{attributes:true,attributeFilter:['data-theme']});
      let pending=false;
      toggle.addEventListener('click',async()=>{
        if(pending)return;pending=true;const before=document.body.dataset.theme,theme=effective()==='dark'?'light':'dark';document.body.dataset.theme=theme;paint();toggle.disabled=true;status.textContent='';
        try{const response=await fetch(toggle.dataset.themeUrl,{method:'POST',headers:{'X-CSRFToken':document.querySelector('#interface-csrf input').value},body:new URLSearchParams({theme}),signal:AbortSignal.timeout(15000)});if(!response.ok||response.redirected||(await response.json()).theme!==theme)throw Error();
          const field=document.getElementById('id_theme');if(field){field.value=theme;document.querySelectorAll('.theme-choices button').forEach((b,i)=>b.setAttribute('aria-pressed',String(field.options[i].value===theme)));}
          status.textContent='Appearance saved';setTimeout(()=>{if(status.textContent==='Appearance saved')status.textContent='';},2500);
        }catch(_){document.body.dataset.theme=before;status.textContent='Appearance could not be saved. Try again.';}finally{pending=false;toggle.disabled=false;paint();}
      });
    }
    const history=document.getElementById('snapshot-history'),open=document.querySelector('[data-snapshot-open]');
    if(history&&open&&typeof history.showModal==='function'){
      open.hidden=false;const search=history.querySelector('input');
      open.addEventListener('click',()=>{if(document.querySelector('dialog[open]'))return;document.querySelectorAll('.workspace-switcher[open]').forEach(d=>d.open=false);history.showModal();search.focus();});
      history.querySelector('[data-context-close]').onclick=()=>history.close();
      history.addEventListener('close',()=>open.focus({preventScroll:true}));
      history.addEventListener('click',e=>{if(e.target!==history)return;const r=history.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right)history.close();});
      search.addEventListener('input',()=>{const choices=[...history.querySelectorAll('[data-snapshot-choice]')];choices.forEach(a=>a.hidden=!a.textContent.toLowerCase().includes(search.value.trim().toLowerCase()));history.querySelector('[data-snapshot-empty]').hidden=choices.some(a=>!a.hidden);});
      history.querySelectorAll('[data-snapshot-choice]').forEach(a=>{const u=new URL(a.href);if(location.pathname.startsWith('/snapshots/')){u.hash=location.hash;a.href=u.href;}});
      window.addEventListener('hashchange',()=>history.querySelectorAll('[data-snapshot-choice]').forEach(a=>{const u=new URL(a.href);u.hash=location.hash;a.href=u.href;}));
    }
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();

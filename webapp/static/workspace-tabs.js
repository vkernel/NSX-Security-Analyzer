/* Accessible tab controller shared by object, review and diagnostic panels. */
(()=>{
 let serial=0;
 window.WorkspaceTabs=(nav,entries,{selected=0,onSelect=()=>{}}={})=>{
  nav.setAttribute('role','tablist');nav.replaceChildren();const id='workspace-tabs-'+(++serial);
  const buttons=entries.map((entry,index)=>{const b=document.createElement('button');b.type='button';b.textContent=entry.label;b.id=id+'-'+index;b.setAttribute('role','tab');entry.panel.id=entry.panel.id||b.id+'-panel';b.setAttribute('aria-controls',entry.panel.id);entry.panel.setAttribute('role','tabpanel');entry.panel.setAttribute('aria-labelledby',b.id);entry.panel.tabIndex=0;nav.append(b);return b;});
  function select(index,focus=false){if(!entries.length)return;selected=Math.max(0,Math.min(index,entries.length-1));entries.forEach((entry,i)=>{entry.panel.hidden=i!==selected;buttons[i].setAttribute('aria-selected',String(i===selected));buttons[i].tabIndex=i===selected?0:-1;});onSelect(selected);if(focus)buttons[selected].focus();}
  buttons.forEach((b,index)=>b.onclick=()=>select(index));
  nav.onkeydown=e=>{const i=buttons.indexOf(document.activeElement);if(i<0)return;const next={ArrowRight:(i+1)%buttons.length,ArrowLeft:(i+buttons.length-1)%buttons.length,Home:0,End:buttons.length-1}[e.key];if(next!==undefined){e.preventDefault();select(next,true);}};
  select(selected);return {select};
 };
})();

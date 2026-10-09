/* Shared read-only evidence UI. Raw values always enter the DOM as text. */
(() => {
  const make=(tag,text)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;return node;};
  window.evidenceState=(container,message,retry)=>{
    const state=make('div');state.className='evidence-state';state.setAttribute('role','status');state.append(make('p',message));
    if(retry){const button=make('button','Retry');button.type='button';button.onclick=retry;state.append(button);}
    container.replaceChildren(state);
  };
  window.technicalEvidence=(raw)=>{
    if(typeof raw!=='string')raw=JSON.stringify(raw,null,2) ?? 'Not recorded';
    const block=make('div');block.className='code-block technical-viewer';
    let json=false;try{JSON.parse(raw);json=true;}catch{}
    const toolbar=make('div');toolbar.className='code-toolbar';
    const lines=raw.split('\n'),list=make('ol');list.className='code-lines';list.tabIndex=0;list.setAttribute('aria-label','Read-only '+(json?'JSON':'text')+' with line numbers');
    toolbar.append(make('span',(json?'JSON':'Text')+' · '+lines.length+' lines · Read only'));
    const action=(label,fn)=>{const button=make('button',label);button.type='button';button.onclick=()=>fn(button);toolbar.append(button);return button;};
    action('Copy',async button=>{try{
      if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(raw);
      else{const field=make('textarea',raw);field.readOnly=true;field.style.cssText='position:fixed;opacity:0';block.append(field);field.select();try{if(!document.execCommand('copy'))throw new Error('Copy unavailable');}finally{field.remove();button.focus();}}
      button.textContent='Copied';}catch{button.textContent='Copy unavailable';}setTimeout(()=>button.textContent='Copy',2000);});
    const wrap=action('Wrap',button=>{const active=block.classList.toggle('wrap');button.setAttribute('aria-pressed',String(active));});wrap.setAttribute('aria-pressed','false');
    const expand=action('Expand',button=>{const active=block.classList.toggle('expanded');button.textContent=active?'Reduce':'Expand';button.setAttribute('aria-pressed',String(active));});expand.setAttribute('aria-pressed','false');
    let position=0;const more=make('button','Show more lines');more.type='button';
    const render=()=>{const end=Math.min(position+200,lines.length);for(;position<end;position++){
      const li=make('li'),line=lines[position];
      if(json && line.length<20000){const pattern=/"(?:\\.|[^"\\])*"\s*:|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;let offset=0;for(const match of line.matchAll(pattern)){li.append(document.createTextNode(line.slice(offset,match.index)));const token=make('span',match[0]);token.className=match[0].endsWith(':')?'json-key':match[0].startsWith('"')?'json-string':'json-value';li.append(token);offset=match.index+match[0].length;}li.append(document.createTextNode(line.slice(offset)||' '));}
      else li.textContent=line||' ';list.append(li);
    }more.hidden=position===lines.length;more.textContent='Show next '+Math.min(200,lines.length-position)+' lines';};
    more.onclick=render;block.append(toolbar,list,more);render();return block;
  };
  window.enhanceTechnicalEvidence=container=>{
    container.querySelectorAll('.code-block:not(.technical-viewer)').forEach(block=>{const lines=block.querySelectorAll('.code-lines li');if(lines.length)block.replaceWith(window.technicalEvidence(Array.from(lines,li=>li.textContent).join('\n')));});
    container.querySelectorAll('pre').forEach(pre=>pre.replaceWith(window.technicalEvidence(pre.textContent)));
  };
  // Preserve DOM nodes/listeners so returning retains expanded sections and pagination.
  window.openRelatedEvidence=async(body,query,label)=>{
    const dialog=body.closest('dialog'),heading=dialog.querySelector('h2'),nodes=Array.from(body.childNodes),title=heading.textContent,scroll=dialog.scrollTop;
    const previousRequest=dialog.reportRequest, previousGeneration=body.evidenceGeneration, previousPending=dialog.tagPending;
    const previousBack=dialog.querySelector('.evidence-back');if(previousBack)previousBack.hidden=true;
    const token={};dialog.reportRequest=token;
    const back=make('button','← Back to '+title.replace(/ —.*$/,''));back.type='button';back.className='evidence-back';
    const restore=()=>{if(dialog.tagPending!==previousPending)dialog.tagPending?.abort();dialog.tagPending=previousPending;dialog.reportRequest=previousRequest;body.evidenceGeneration=previousGeneration;body.replaceChildren(...nodes);heading.textContent=title;back.remove();if(previousBack)previousBack.hidden=false;dialog.scrollTop=scroll;};
    back.onclick=restore;dialog.querySelector('.dialog-heading').after(back);heading.textContent=label;
    const open=async()=>{window.evidenceState(body,'Loading evidence…');try{
      const record=await window.reportData(null,{op:'resolve',...query});if(!dialog.open||dialog.reportRequest!==token)return;
      const button=make('button');button.className='detail-button';button.dataset.title=label;button.dataset.evidenceView=record.view;
      button.dataset[record.view==='tags'?'tagRow':'evidenceRow']=record.id;
      button.hidden=true;document.querySelector('main').append(button);button.click();dialog.addEventListener('close',()=>button.remove(),{once:true});back.addEventListener('click',()=>button.remove(),{once:true});
    }catch(error){if(dialog.open&&dialog.reportRequest===token)window.evidenceState(body,error.message,open);}};
    dialog.addEventListener('close',()=>back.remove(),{once:true});await open();
  };
})();

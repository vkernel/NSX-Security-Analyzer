/* Shared server pagination controls and honest counts for non-paginated tables. */
(() => {
 document.addEventListener('change',event=>{
  if(event.target.matches('[data-table-page-size]'))location.assign(event.target.value);
 });
 function refresh(){
  document.querySelectorAll('.table-footer:not([data-local-table-count])').forEach(footer=>{
   const previous=footer.previousElementSibling;
   if(previous?.matches('table'))previous.dataset.serverFooter='true';
   else if(previous)previous.querySelectorAll('table').forEach(table=>table.dataset.serverFooter='true');
  });
  document.querySelectorAll('main table').forEach(table=>{
   if(table.closest('.table-widget')||table.dataset.serverFooter||table.closest('template'))return;
   const wrapper=table.closest('.table-scroll')||table;
   let footer=wrapper.nextElementSibling;
   if(footer?.matches('.pagination,.table-pagination,.table-footer:not([data-local-table-count])'))return;
   if(!footer?.matches('[data-local-table-count]')){footer=document.createElement('div');footer.className='table-footer';footer.dataset.localTableCount='true';wrapper.after(footer);}
   const rows=[...table.tBodies].flatMap(body=>[...body.rows]).filter(row=>!row.hidden&&getComputedStyle(row).display!=='none'&&!(row.cells.length===1&&row.cells[0].colSpan>1));
   const count=rows.length,selected=rows.filter(row=>row.querySelector('input[type=checkbox]:checked')).length;
   const text=`${count} row${count===1?'':'s'} shown${selected?' · '+selected+' selected on this page':''}`;
   if(footer.textContent!==text)footer.textContent=text;
  });
 }
 let queued=false;const schedule=()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;refresh();});};
 new MutationObserver(schedule).observe(document.querySelector('main')||document.body,{childList:true,subtree:true});
 document.addEventListener('change',schedule);document.addEventListener('table-render',schedule);refresh();
})();

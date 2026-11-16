(() => {
  const makeDialog = (title) => {
    const dialog = document.createElement('dialog');
    dialog.className = 'compact-action-dialog';
    const heading = document.createElement('h2');
    heading.id = `compact-dialog-${document.querySelectorAll('dialog').length}`;
    heading.textContent = title;
    dialog.setAttribute('aria-labelledby', heading.id);
    dialog.append(heading);
    return dialog;
  };
  const cancelButton = (dialog) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'button'; button.textContent = 'Cancel';
    button.addEventListener('click', () => dialog.close());
    return button;
  };
  document.querySelectorAll('[data-dialog-disclosure]').forEach(details => {
    const content = details.querySelector('[data-dialog-content]');
    const trigger = document.createElement('button');
    trigger.type = 'button'; trigger.className = 'button';
    trigger.textContent = details.querySelector('summary').textContent;
    const dialog = makeDialog(content.querySelector('h2').textContent);
    content.querySelector('h2').remove();
    dialog.append(content);
    content.querySelector('.form-actions').prepend(cancelButton(dialog));
    details.replaceWith(trigger, dialog);
    trigger.addEventListener('click', () => dialog.showModal());
    dialog.addEventListener('close', () => trigger.focus());
  });

  const review = document.querySelector('[data-finding-review]');
  // Keep server validation errors and their original fields visible on a failed POST.
  if (review && !review.querySelector('.errorlist')) {
    const secondary = [...review.querySelectorAll('button[name="action"]')]
      .filter(button => ['assign', 'unassign', 'reopen'].includes(button.value));
    if (secondary.length) {
      const menu = document.createElement('details'); menu.className = 'compact-action-menu';
      const summary = document.createElement('summary'); summary.className = 'button'; summary.textContent = 'Actions';
      menu.append(summary);
      const items = document.createElement('div'); menu.append(items);
      // Popovers stay in the form's DOM but render above scrolling and sticky containers.
      if ('showPopover' in items) {
        items.setAttribute('popover','auto');
        items.classList.add('review-action-popover');
        summary.setAttribute('aria-expanded','false');
        const position=()=>{const box=summary.getBoundingClientRect();items.style.left=Math.max(8,Math.min(box.left,innerWidth-items.offsetWidth-8))+'px';items.style.top=Math.max(8,Math.min(box.bottom+6,innerHeight-items.offsetHeight-8))+'px';};
        summary.addEventListener('click',event=>{event.preventDefault();menu.open=true;items.togglePopover();if(items.matches(':popover-open'))position();});
        summary.addEventListener('keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();menu.open=true;if(!items.matches(':popover-open'))items.showPopover();position();items.querySelector('button')?.focus();}});
        items.addEventListener('toggle',()=>summary.setAttribute('aria-expanded',String(items.matches(':popover-open'))));
        items.addEventListener('keydown',event=>{if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const buttons=[...items.querySelectorAll('button')];let index=buttons.indexOf(document.activeElement);index=event.key==='Home'?0:event.key==='End'?buttons.length-1:(index+(event.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length;buttons[index]?.focus();}});
        window.addEventListener('resize',()=>{if(items.matches(':popover-open'))position();});
        document.addEventListener('scroll',()=>{if(items.matches(':popover-open'))position();},true);
      }

      const direct = secondary.length === 1 && secondary[0].value === 'assign';
      const buttonRow = review.querySelector('.review-buttons');
      if (!direct) buttonRow.append(menu);
      let opener = summary;
      const dialog = makeDialog('Review action'); review.append(dialog);
      const fields = document.createElement('div'); dialog.append(fields);
      const footer = document.createElement('div'); footer.className = 'finding-release-actions';
      const submit = document.createElement('button'); submit.type = 'submit'; submit.name = 'action'; submit.className = 'button primary';
      footer.append(cancelButton(dialog), submit); dialog.append(footer);
      const note = review.querySelector('[name="note"]')?.closest('.form-field');
      const owner = review.querySelector('[name="owner"]')?.closest('.form-field');
      const ownerDisclosure = owner?.closest('details');
      const updateSubmit = () => { submit.disabled = submit.value === 'assign' && !owner?.querySelector('[name="owner"]').value; };
      owner?.addEventListener('change', updateSubmit);
      const notePosition = document.createComment('review note'); if (note) note.before(notePosition);
      const ownerPosition = document.createComment('review owner'); if (owner) owner.before(ownerPosition);
      secondary.forEach(original => {
        const trigger = document.createElement('button'); trigger.type = 'button'; trigger.className = 'button'; trigger.textContent = original.textContent;
        original.hidden = true; (direct ? buttonRow : items).append(trigger);
        trigger.addEventListener('click', () => {
          menu.open = false; if(items.hidePopover && items.matches(':popover-open'))items.hidePopover(); opener = summary;
          dialog.querySelector('h2').textContent = original.textContent;
          submit.textContent = original.textContent; submit.value = original.value;
          if (original.value === 'assign' && owner) fields.append(owner);
          if (note) {
            note.hidden = original.hasAttribute('data-no-reason');
            note.querySelector('[name="note"]').disabled = note.hidden;
            fields.append(note);
          }
          updateSubmit();
          dialog.showModal();
        });
      });
      if (ownerDisclosure) ownerDisclosure.hidden = true;
      dialog.addEventListener('close', () => {
        if (note) { note.hidden = false; note.querySelector('[name="note"]').disabled = false; notePosition.after(note); }
        if (owner) ownerPosition.after(owner);
        opener.focus();
      });
    }
  }

  const bar = document.querySelector('[data-snapshot-bar]');
  if (bar) {
    const boxes = [...document.querySelectorAll('[data-snapshot-choice]')];
    const link = bar.querySelector('[data-snapshot-compare]');
    const base = link.getAttribute('href');
    const update = () => {
      const selected = boxes.filter(box => box.checked).sort((a, b) => Number(a.dataset.time) - Number(b.dataset.time));
      bar.hidden = !selected.length;
      bar.querySelector('[data-snapshot-count]').textContent = `${selected.length} of 2 selected`;
      const valid = selected.length === 2 && selected[0].dataset.time !== selected[1].dataset.time;
      link.setAttribute('aria-disabled', String(!valid));
      if (valid) link.href = `${base}?${new URLSearchParams({before:selected[0].value, after:selected[1].value})}`;
      else link.removeAttribute('href');
      boxes.forEach(box => {
        box.disabled = selected.length >= 2 && !box.checked;
        box.parentElement.querySelector('[data-snapshot-role]').textContent = selected.length === 2 && box.checked ? (box === selected[0] ? 'Before' : 'After') : '';
      });
    };
    boxes.forEach(box => box.addEventListener('change', update));
    bar.querySelector('[data-snapshot-clear]').addEventListener('click', () => {
      const first = boxes.find(box => box.checked);
      boxes.forEach(box => {box.checked = false;}); update(); first?.focus();
    });
    window.addEventListener('pageshow', update); update();
  }
})();

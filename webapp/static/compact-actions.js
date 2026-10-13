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
      review.querySelector('.review-buttons').append(menu);
      const dialog = makeDialog('Review action'); review.append(dialog);
      const fields = document.createElement('div'); dialog.append(fields);
      const footer = document.createElement('div'); footer.className = 'finding-release-actions';
      const submit = document.createElement('button'); submit.type = 'submit'; submit.name = 'action'; submit.className = 'button primary';
      footer.append(cancelButton(dialog), submit); dialog.append(footer);
      const note = review.querySelector('[name="note"]').closest('.form-field');
      const owner = review.querySelector('[name="owner"]')?.closest('.form-field');
      const ownerDisclosure = owner?.closest('details');
      const notePosition = document.createComment('review note'); note.before(notePosition);
      const ownerPosition = document.createComment('review owner'); if (owner) owner.before(ownerPosition);
      secondary.forEach(original => {
        const trigger = document.createElement('button'); trigger.type = 'button'; trigger.className = 'button'; trigger.textContent = original.textContent;
        original.hidden = true; items.append(trigger);
        trigger.addEventListener('click', () => {
          menu.open = false;
          dialog.querySelector('h2').textContent = original.textContent;
          submit.textContent = original.textContent; submit.value = original.value;
          if (original.value === 'assign' && owner) fields.append(owner);
          fields.append(note);
          dialog.showModal();
        });
      });
      if (ownerDisclosure) ownerDisclosure.hidden = true;
      dialog.addEventListener('close', () => {
        notePosition.after(note);
        if (owner) ownerPosition.after(owner);
        summary.focus();
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

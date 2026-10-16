// Native dialog provides keyboard focus containment and Escape without blocking JS.
function uiConfirm({title, message, confirmLabel = '确认'}) {
  return new Promise(resolve => {
    const previous = document.activeElement;
    const dialog = document.createElement('dialog');
    dialog.className = 'confirm-dialog';
    dialog.setAttribute('aria-labelledby', 'confirm-title');
    dialog.setAttribute('aria-describedby', 'confirm-message');
    dialog.innerHTML = '<form method="dialog"><div class="confirm-mark" aria-hidden="true">!</div><h2 id="confirm-title"></h2><p id="confirm-message"></p><div class="dialog-actions"><button class="btn" value="cancel" autofocus>取消</button><button class="btn btn-primary" value="confirm"></button></div></form>';
    dialog.querySelector('h2').textContent = title;
    dialog.querySelector('p').textContent = message;
    dialog.querySelector('[value="confirm"]').textContent = confirmLabel;
    dialog.addEventListener('close', () => {
      const accepted = dialog.returnValue === 'confirm';
      dialog.remove();
      if (previous?.isConnected) previous.focus();
      resolve(accepted);
    }, {once:true});
    document.body.append(dialog);
    dialog.showModal();
  });
}

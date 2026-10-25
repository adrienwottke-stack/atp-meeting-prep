const dialog = document.querySelector('.guide-lightbox');
const image = dialog.querySelector('img');
const title = document.getElementById('screenshot-title');
document.querySelectorAll('[data-enlarge]').forEach(link => {
  link.addEventListener('click', event => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    image.src = link.href;
    image.alt = link.querySelector('img').alt;
    title.textContent = image.alt;
    dialog.querySelectorAll('.guide-pin').forEach(pin => pin.remove());
    link.querySelectorAll('.guide-pin').forEach(pin => image.parentElement.append(pin.cloneNode(true)));
    dialog.showModal();
    const stage = dialog.querySelector('.guide-lightbox-stage');
    const firstPin = dialog.querySelector('.guide-pin');
    stage.scrollTop = 0;
    stage.scrollLeft = stage.scrollWidth > stage.clientWidth && firstPin
      ? Math.max(0, firstPin.offsetLeft - 28)
      : 0;
  });
});
dialog.querySelector('[data-close]').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });

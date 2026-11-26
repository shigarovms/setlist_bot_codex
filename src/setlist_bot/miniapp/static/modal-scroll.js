let watching = false;
let gesture = null;

function startGesture(event) {
  gesture = null;
  if (event.touches.length !== 1) return;
  const point = event.touches[0];
  const modal = event.target instanceof Element ? event.target.closest("dialog[open]") : null;
  const scrollers = [];
  if (modal) {
    const bounds = modal.getBoundingClientRect();
    if (point.clientX >= bounds.left && point.clientX <= bounds.right
      && point.clientY >= bounds.top && point.clientY <= bounds.bottom) {
      for (let node = event.target; node; node = node.parentElement) {
        if (node === modal || ["auto", "scroll", "overlay"].includes(getComputedStyle(node).overflowY))
          scrollers.push(node);
        if (node === modal) break;
      }
    }
  }
  gesture = { x: point.clientX, y: point.clientY, scrollers };
}

function containGesture(event) {
  if (!gesture || event.touches.length !== 1) return;
  const point = event.touches[0];
  const dx = point.clientX - gesture.x;
  const dy = point.clientY - gesture.y;
  gesture.x = point.clientX;
  gesture.y = point.clientY;
  if (Math.abs(dy) <= Math.abs(dx)) return;

  // Some iOS web views still pan the page at a dialog's scroll boundary.
  const canScroll = gesture.scrollers.some(node => dy > 0
    ? node.scrollTop > 1
    : node.scrollHeight - node.clientHeight - node.scrollTop > 1);
  if (!canScroll && event.cancelable) event.preventDefault();
}

function watchModalGestures() {
  const open = !!document.querySelector("dialog[open]");
  if (open === watching) return;
  watching = open;
  if (open) {
    document.addEventListener("touchstart", startGesture, { capture: true, passive: true });
    document.addEventListener("touchmove", containGesture, { capture: true, passive: false });
  } else {
    gesture = null;
    document.removeEventListener("touchstart", startGesture, true);
    document.removeEventListener("touchmove", containGesture, true);
  }
}

new MutationObserver(watchModalGestures).observe(document.body, {
  subtree: true, childList: true, attributes: true, attributeFilter: ["open"],
});
watchModalGestures();

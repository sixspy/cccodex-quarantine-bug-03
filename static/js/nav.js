export function navigate(page) {
  window.dispatchEvent(new CustomEvent('app:navigate', { detail: page }));
}

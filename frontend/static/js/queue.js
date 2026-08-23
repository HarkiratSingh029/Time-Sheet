/**
 * Approval queue.
 *
 * One job: make a bulk approval say what it is about to do before it does it. Everything
 * else on this page is a plain form, so it keeps working with this module absent — which
 * is also what makes the whole queue keyboard-operable for free.
 */

for (const button of document.querySelectorAll("[data-confirm-count]")) {
  button.addEventListener("click", (event) => {
    const count = Number(button.dataset.confirmCount);
    if (!Number.isFinite(count) || count <= 1) return;

    const entries = `${count} entries`;
    if (!window.confirm(`Approve ${entries}? This cannot be undone from here.`)) {
      event.preventDefault();
    }
  });
}

/**
 * Week grid.
 *
 * Each cell is its own form, so the grid already works with this module absent — every
 * cell has a submit button under <noscript>, and Tab moves through them in reading order.
 * All this adds is saving on blur, so filling a week is typing rather than typing and
 * clicking.
 */

for (const input of document.querySelectorAll("[data-week-cell]")) {
  const initial = input.value;

  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      input.blur();
    }
    if (event.key === "Escape") {
      input.value = initial;
      input.blur();
    }
  });

  input.addEventListener("blur", () => {
    // Submitting an unchanged cell would be a pointless round trip and a spurious flash.
    if (input.value === initial) return;
    input.form.requestSubmit();
  });
}

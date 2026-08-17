/**
 * Theme toggle.
 *
 * Three states, not two: an explicit "light", an explicit "dark", and no stored
 * preference at all — which is the default, and lets the OS decide. Clearing the stored
 * value is therefore a real option, not a missing one.
 *
 * The pre-paint application of a stored theme lives inline in base.html; by the time this
 * module runs, the first frame has already been painted with the right palette.
 */

const STORAGE_KEY = "ts-theme";
const LIGHT = "light";
const DARK = "dark";

const root = document.documentElement;
const osPrefersDark = window.matchMedia("(prefers-color-scheme: dark)");

function storedTheme() {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === LIGHT || value === DARK ? value : null;
  } catch (error) {
    return null;
  }
}

function activeTheme() {
  return storedTheme() ?? (osPrefersDark.matches ? DARK : LIGHT);
}

function applyTheme(theme) {
  root.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch (error) {
    /* private mode: the toggle still works for this page view */
  }
  syncButtons(theme);
}

function syncButtons(theme) {
  for (const button of document.querySelectorAll("[data-theme-toggle]")) {
    button.dataset.themeState = theme;
    button.setAttribute("aria-pressed", String(theme === DARK));
    button.title = theme === DARK ? "Switch to light theme" : "Switch to dark theme";
  }
}

function toggle() {
  applyTheme(activeTheme() === DARK ? LIGHT : DARK);
}

for (const button of document.querySelectorAll("[data-theme-toggle]")) {
  button.addEventListener("click", toggle);
}

// Follow the OS while the user has expressed no preference of their own.
osPrefersDark.addEventListener("change", (event) => {
  if (storedTheme() === null) {
    syncButtons(event.matches ? DARK : LIGHT);
  }
});

syncButtons(activeTheme());

export { activeTheme, applyTheme, toggle };

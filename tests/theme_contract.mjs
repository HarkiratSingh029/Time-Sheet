/**
 * Exercises frontend/static/js/theme.js against a stub DOM.
 *
 * The theme toggle is the one piece of EPIC 0 behaviour that lives entirely in the
 * browser, so asserting it by reading the source would be assuming it rather than testing
 * it. A few dozen lines of stub is cheaper than a headless browser and runs in the same
 * second as the rest of the suite.
 *
 * Run directly, or via tests/test_ui_shell.py.
 */

import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const THEME_MODULE = pathToFileURL(resolve(REPO_ROOT, "frontend/static/js/theme.js"));

function installStubDom({ stored = null, osPrefersDark = false } = {}) {
  const store = new Map();
  if (stored !== null) store.set("ts-theme", stored);

  const button = {
    dataset: {},
    attributes: {},
    listeners: {},
    title: "",
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    addEventListener(event, handler) {
      this.listeners[event] = handler;
    },
    click() {
      this.listeners.click?.();
    },
  };

  const root = {
    attributes: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    getAttribute(name) {
      return this.attributes[name] ?? null;
    },
  };

  const mediaListeners = [];

  globalThis.document = {
    documentElement: root,
    querySelectorAll: (selector) => (selector === "[data-theme-toggle]" ? [button] : []),
  };

  globalThis.localStorage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
  };

  globalThis.window = {
    matchMedia: () => ({
      matches: osPrefersDark,
      addEventListener: (_event, handler) => mediaListeners.push(handler),
    }),
  };

  return { button, root, store, mediaListeners };
}

async function loadTheme(options) {
  const dom = installStubDom(options);
  // A cache-busting query keeps each scenario on a fresh module instance.
  const module = await import(`${THEME_MODULE}?scenario=${Math.random()}`);
  return { ...dom, module };
}

const checks = [];
function check(name, fn) {
  checks.push([name, fn]);
}

check("with no stored preference the OS decides, and nothing is written", async () => {
  const dark = await loadTheme({ stored: null, osPrefersDark: true });
  assert.equal(dark.module.activeTheme(), "dark");
  assert.equal(dark.store.has("ts-theme"), false, "must not persist a preference nobody set");
  assert.equal(dark.button.dataset.themeState, "dark");

  const light = await loadTheme({ stored: null, osPrefersDark: false });
  assert.equal(light.module.activeTheme(), "light");
});

check("a stored preference overrides the OS", async () => {
  const { module } = await loadTheme({ stored: "light", osPrefersDark: true });
  assert.equal(module.activeTheme(), "light");
});

check("clicking the toggle flips the theme, sets data-theme and persists it", async () => {
  const { button, root, store, module } = await loadTheme({ stored: null, osPrefersDark: false });

  button.click();
  assert.equal(root.getAttribute("data-theme"), "dark");
  assert.equal(store.get("ts-theme"), "dark");
  assert.equal(button.attributes["aria-pressed"], "true");
  assert.equal(module.activeTheme(), "dark", "a reload in this tab reads dark");

  button.click();
  assert.equal(root.getAttribute("data-theme"), "light");
  assert.equal(store.get("ts-theme"), "light");
  assert.equal(button.attributes["aria-pressed"], "false");
});

check("a stored choice survives a fresh page load, which is what a new tab is", async () => {
  const first = await loadTheme({ stored: null, osPrefersDark: false });
  first.button.click();
  assert.equal(first.store.get("ts-theme"), "dark");

  const reopened = await loadTheme({ stored: "dark", osPrefersDark: false });
  assert.equal(reopened.module.activeTheme(), "dark");
  assert.equal(reopened.button.dataset.themeState, "dark");
});

check("an OS change is followed only while the user has chosen nothing", async () => {
  const following = await loadTheme({ stored: null, osPrefersDark: false });
  following.mediaListeners.forEach((handler) => handler({ matches: true }));
  assert.equal(following.button.dataset.themeState, "dark");

  const chosen = await loadTheme({ stored: "light", osPrefersDark: false });
  chosen.mediaListeners.forEach((handler) => handler({ matches: true }));
  assert.equal(chosen.button.dataset.themeState, "light", "an explicit choice wins over the OS");
});

let failed = 0;
for (const [name, fn] of checks) {
  try {
    await fn();
    console.log(`  ok    ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`  FAIL  ${name}\n        ${error.message}`);
  }
}

if (failed > 0) {
  console.error(`${failed} theme check(s) failed`);
  process.exit(1);
}
console.log(`OK    ${checks.length} theme checks passed`);

#!/usr/bin/env node
/**
 * Frontend gate — run on any change under frontend/.
 *
 * "No build step" must not mean "no standards", so this script is the standard:
 *
 *   1. Every JS module parses.
 *   2. No hard-coded colours — application source uses brand tokens (BRAND_GUIDELINES.md §3).
 *   3. Jinja block tags are balanced.
 *   4. Every page template extends a layout; partials are exempt.
 *   5. The generated token stylesheet exists.
 *
 * Usage: node scripts/check-frontend.mjs
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND_DIR = join(REPO_ROOT, "frontend");
const TOKENS_CSS = join(REPO_ROOT, "brands", "dist", "tokens.css");

const COLOR_PATTERN =
  /(#[0-9a-fA-F]{3,8}\b|\brgba?\s*\(|\bhsla?\s*\()/;
const JINJA_PAIRS = [
  ["block", "endblock"],
  ["if", "endif"],
  ["for", "endfor"],
  ["macro", "endmacro"],
  ["with", "endwith"],
  ["call", "endcall"],
  ["filter", "endfilter"],
];

const problems = [];
const checked = { js: 0, css: 0, html: 0 };

function walk(dir) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return [];
  }
  return entries.flatMap((entry) => {
    if (entry === "node_modules" || entry === "dist" || entry.startsWith(".")) return [];
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

function report(file, message) {
  problems.push(`${relative(REPO_ROOT, file)}: ${message}`);
}

function checkJavaScript(file, source) {
  checked.js += 1;
  // Copy to a .mjs temp file so `node --check` parses ESM regardless of the source extension.
  const scratch = join(mkdtempSync(join(tmpdir(), "ts-frontend-")), "module.mjs");
  writeFileSync(scratch, source);
  try {
    execFileSync(process.execPath, ["--check", scratch], { stdio: "pipe" });
  } catch (error) {
    const detail = (error.stderr?.toString() ?? error.message).split("\n").slice(0, 3).join(" ").trim();
    report(file, `does not parse — ${detail}`);
  }

  source.split("\n").forEach((line, index) => {
    if (line.trim().startsWith("//")) return;
    if (COLOR_PATTERN.test(line)) {
      report(file, `line ${index + 1} hard-codes a colour; use a var(--color-*) token`);
    }
  });
}

function checkCss(file, source) {
  checked.css += 1;
  source.split("\n").forEach((line, index) => {
    const code = line.split("/*")[0];
    if (COLOR_PATTERN.test(code)) {
      report(file, `line ${index + 1} hard-codes a colour; use a var(--color-*) token`);
    }
  });
}

function checkTemplate(file, source) {
  checked.html += 1;

  for (const [open, close] of JINJA_PAIRS) {
    const opened = source.match(new RegExp(`{%-?\\s*${open}[\\s%]`, "g"))?.length ?? 0;
    const closed = source.match(new RegExp(`{%-?\\s*${close}\\s*-?%}`, "g"))?.length ?? 0;
    if (opened !== closed) {
      report(file, `unbalanced Jinja tags: ${opened} {% ${open} %} vs ${closed} {% ${close} %}`);
    }
  }

  source.split("\n").forEach((line, index) => {
    if (line.includes("style=") && COLOR_PATTERN.test(line)) {
      report(file, `line ${index + 1} hard-codes a colour in a style attribute; use a token`);
    }
  });

  const isPartial = file.includes(`${join("templates", "partials")}`) || /(^|\/)_/.test(file);
  const isLayout = /\b(base|layout)\.html$/.test(file);
  if (!isPartial && !isLayout && !/{%-?\s*extends\s/.test(source)) {
    report(file, "page template does not extend a layout");
  }
}

const files = walk(FRONTEND_DIR);

for (const file of files) {
  const source = readFileSync(file, "utf8");
  switch (extname(file)) {
    case ".js":
    case ".mjs":
      checkJavaScript(file, source);
      break;
    case ".css":
      checkCss(file, source);
      break;
    case ".html":
    case ".jinja":
      checkTemplate(file, source);
      break;
    default:
      break;
  }
}

try {
  statSync(TOKENS_CSS);
} catch {
  problems.push(
    "brands/dist/tokens.css is missing — run: python3 brands/scripts/build_all.py",
  );
}

const summary = `${checked.js} js, ${checked.css} css, ${checked.html} template(s)`;

if (problems.length > 0) {
  console.error(`FAIL  ${problems.length} problem(s) in ${summary}`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}

console.log(`OK    frontend clean — ${summary}`);

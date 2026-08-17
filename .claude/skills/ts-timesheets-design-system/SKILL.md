---
name: ts-timesheets-design-system
description: The TS Timesheets colour palette, tokens, typography and UI rules — Clean Navy & Mint in light mode, Ocean Depth in dark. Use when writing any CSS, Jinja template or UI component, choosing a colour, or changing the brand toolkit.
---

# TS Timesheets — design system

Design is at the heart of this application. Full text: `BRAND_GUIDELINES.md`. Source of
truth for values: `brands/tokens/palette.json` → generated `brands/dist/tokens.css`.

## The rule that matters most

**Never hard-code a colour in `frontend/` or `backend/`.** Use `var(--color-*)`.
`node scripts/check-frontend.mjs` fails the build on a raw hex, `rgb()` or `hsl()`.

## Light — Clean Navy & Mint (default)

| Token | Hex | Role |
| --- | --- | --- |
| `--color-primary` | `#000055` | Classic Navy — headers, primary actions |
| `--color-secondary` | `#556B7D` | Muted Slate — metadata |
| `--color-tertiary` | `#98DDCA` | Mint — relief on dense screens |
| `--color-accent` | `#00BCD4` | Bright Cyan — focus, selection |
| `--color-background` | `#FAFAFA` | Off-White |
| `--color-surface` | `#FFFFFF` | Cards, tables |
| `--color-text-heading` | `#000055` | Headings |
| `--color-text-body` | `#333333` | Body copy |

## Dark — Ocean Depth

| Token | Hex | Role |
| --- | --- | --- |
| `--color-background` | `#101626` | Deep Blue-Gray |
| `--color-surface` | `#1E293B` | Cards, modules |
| `--color-text-body` | `#CBD5E1` | Pale Periwinkle |
| `--color-text-heading` | `#FFFFFF` | High emphasis |
| `--color-accent` | `#38BDF8` | Bright Sky Blue |
| `--color-tertiary` | `#34D399` | Vibrant Mint |

## Semantic colours — locked, never restyled for variety

`--color-state-approved` · `--color-state-pending` · `--color-state-rejected` ·
`--color-state-draft` · `--color-health-green` / `-amber` / `-red`

Approval state and project health share one colour language: a green pill means "fine"
everywhere. **Colour never carries meaning alone** — always pair it with a label.

## Theming

`<html data-theme="dark">` or `"light"`. With neither, the OS preference wins. The user's
choice persists in `localStorage`. Define light on bare `:root`; redefine only the tokens
under both `@media (prefers-color-scheme: dark)` (guarded with
`:root:not([data-theme="light"])`) and `[data-theme="dark"]`.

## Layout

- 8px rhythm — spacing only from `--space-*`.
- `--radius-md` inputs and buttons, `--radius-lg` cards and modals, `--radius-pill` chips.
- Cards use a border, not a shadow. Shadow is for modals and popovers only.
- Tables and the calendar are the product: comfortable line height, generous column
  padding, a visible hover row.
- Numerics use `--font-mono` so columns align.

## The calendar

One month grid per project, bounded by project start/end. Out-of-window days are inert,
not merely dim. A logged day shows total hours plus a state chip; an empty working day
invites a double-click. Today gets a ring, selection gets a fill — they never compete.

## Accessibility

WCAG AA (4.5:1) for body text in both themes, enforced by
`python3 brands/scripts/build_all.py`. Visible focus ring in `--color-accent` on every
interactive element. The whole logging flow is keyboard-reachable.

## Changing the brand

A value change is a `[Design]` ticket: edit `brands/tokens/palette.json`, run
`python3 brands/scripts/build_all.py`, commit the regenerated `brands/dist/`. It must not
touch `backend/`, `frontend/`, `scripts/` or `tests/` — adopting a token in the product is
a separate `[Feature]` ticket.

## External design references

- Design taste: <https://github.com/leonxlnx/taste-skill>
- Dependency and design research: <https://github.com/Panniantong/Agent-Reach>

On conflict, `BRAND_GUIDELINES.md` wins on TS Timesheets surfaces — record the decision in
the ticket so it is not re-litigated.

# Brand Guidelines — TS Timesheets

Design is at the heart of this application. It is a dense-data product people look at for
hours; the job of the design system is to make that feel calm rather than heavy.

The single source of truth is **`brands/tokens/palette.json`**. Everything below describes
what those tokens mean and how to use them. Changing a value is a `[Design]` ticket
(CHANGE_MANAGEMENT.md §7); adopting one in a screen is a `[Feature]` ticket.

---

## 1. Naming

**TS Timesheets** is the product. The repository is `Time-Sheet`. The short form in UI
chrome is **TS**. Do not introduce alternative product names, and do not restyle the
wordmark inside a feature ticket.

---

## 2. Palette

### 2.1 Light — Clean Navy & Mint

The standard mode. Classic navy for authority, mint for relief, so dense screens stop
feeling like a spreadsheet.

| Role | Token | Hex |
| --- | --- | --- |
| Primary | `--color-primary` | `#000055` Classic Navy |
| Secondary | `--color-secondary` | `#556B7D` Muted Slate |
| Tertiary | `--color-tertiary` | `#98DDCA` Mint Green |
| Accent | `--color-accent` | `#00BCD4` Bright Cyan |
| Background | `--color-background` | `#FAFAFA` Off-White |
| Surface | `--color-surface` | `#FFFFFF` |
| Headings | `--color-text-heading` | `#000055` Navy |
| Body text | `--color-text-body` | `#333333` Dark Gray |

### 2.2 Dark — Ocean Depth

Requested by anyone who lives in a data-heavy app. A very dark blue-gray base with
lighter blues defining structure.

| Role | Token | Hex |
| --- | --- | --- |
| Background | `--color-background` | `#101626` Deep Blue-Gray |
| Surface (cards, modules) | `--color-surface` | `#1E293B` |
| Secondary / text | `--color-text-body` | `#CBD5E1` Pale Periwinkle |
| Accent 1 | `--color-accent` | `#38BDF8` Bright Sky Blue |
| Accent 2 | `--color-tertiary` | `#34D399` Vibrant Mint |
| High emphasis text | `--color-text-heading` | `#FFFFFF` |

### 2.3 Semantic colours — locked

These carry meaning, not taste. They are not restyled for variety:

| Meaning | Token | Light | Dark |
| --- | --- | --- | --- |
| Approved | `--color-state-approved` | `#2E9E7B` | `#34D399` |
| Pending approval | `--color-state-pending` | `#C98A00` | `#FBBF24` |
| Rejected | `--color-state-rejected` | `#C1372F` | `#F87171` |
| Draft | `--color-state-draft` | `#556B7D` | `#94A3B8` |
| Project health green / amber / red | `--color-health-*` | as above | as above |

Approval state and project health share a colour language on purpose: a green pill means
"fine" everywhere in the product.

---

## 3. Using the tokens

Consume the generated stylesheet; never hard-code a hex in application source.

```css
@import url("/brand/tokens.css");

.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  color: var(--color-text-body);
}
```

Theme switching is a single attribute on `<html>`: `data-theme="dark"` or
`data-theme="light"`. With neither, the OS preference decides. The user's choice persists
in `localStorage`.

A hard-coded colour in `frontend/` or `backend/` is a review blocker. `node
scripts/check-frontend.mjs` enforces it.

---

## 4. Typography

- **Sans:** Inter, with a system stack fallback. Used everywhere.
- **Mono:** JetBrains Mono, with a system fallback. Durations, IDs, and numeric table
  columns, so figures align.
- **Scale:** `--text-xs` … `--text-3xl`. Three sizes on a screen is usually enough.
- Headings use `--color-text-heading`; body copy uses `--color-text-body`. Muted text is
  for metadata only, never for something the user must read to act.

---

## 5. Layout and components

- **8px rhythm.** All spacing comes from `--space-*`.
- **Radius:** `--radius-md` for inputs and buttons, `--radius-lg` for cards and modals,
  `--radius-pill` for status chips.
- **Elevation is restrained.** A single soft shadow separates modals and popovers from
  the page. Cards use a border, not a shadow.
- **Density.** Tables and the calendar are the product. Comfortable line height,
  generous column padding, and a visible hover row — the eye should be able to track a
  row across the screen.
- **Status is always a chip**, never bare coloured text, and never colour alone: the chip
  carries a label so it survives colour-blind readers and greyscale printing.

---

## 6. The calendar

The project calendar is the primary surface of the product.

- One month grid per project, bounded by the project start and end dates.
- Days outside the project window are visibly inert, not merely dimmed.
- A day with logged time shows total hours and a state chip; an empty working day invites
  a double-click.
- Weekends and non-working days use `--color-surface-muted`.
- Selection uses `--color-accent`; today gets a ring, not a fill, so it never competes
  with selection.

---

## 7. Accessibility

- Body text meets **WCAG AA 4.5:1** against its background in both themes. Contrast is
  enforced by `brands/scripts/build_all.py`, not by good intentions.
- Every interactive element has a visible focus ring in `--color-accent`.
- Colour never carries meaning on its own — pair it with a label or an icon.
- The whole timesheet flow is keyboard-reachable: a consultant who logs time daily should
  never need the mouse.

---

## 8. Precedence

When a generic or vendored design skill conflicts with this document, **this document
wins** on TS Timesheets surfaces. Record the decision in the `[Design]` ticket so a
future session does not re-litigate it.

Locked without a dedicated Design ticket that argues the change on its merits: the
semantic colour meanings (§2.3), the light/dark contract (§2.1–§2.2), and the product
naming (§1).

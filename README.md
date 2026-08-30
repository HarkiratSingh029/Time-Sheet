# TS Timesheets

A central timesheet application for small and medium organizations: consultants log
time against projects, approvers sign it off, and project owners watch billability and
project health from a dashboard.

It is built to be **SAAS in a box** — one `docker compose up` gives you the whole
product on a modest VPC, with an optional Cloudflare Tunnel as the only door to the
outside world. No S3, no managed services, no per-seat cloud bill.

---

## What it does

| Who | What they get |
| --- | --- |
| **Administrator** | The license holder and super user. Unrestricted CRUD across every object; adds users and assigns their roles. |
| **Project owner** | Creates projects, sets budget/duration/approval rules, and reads the metrics dashboard (per project and global). |
| **Approver** | Reviews submitted time notes for the projects they approve — up to five approvals per project. |
| **Consultant** | Opens a project calendar, double-clicks a date, and logs a time note with duration and detail. |

The unit of work is a **time note**: a consultant's entry for one day on one project.
Time notes flow `draft → submitted → approved/rejected`, and the dashboard aggregates
them into billability, burn-down and project health (green / amber / red).

---

## Repository layout

```
backend/app/      FastAPI application — config, entry point, models, database, seeding
frontend/         Jinja templates + vanilla JS/CSS served by the backend (no build step)
brands/           Brand toolkit: design tokens, palette, generated CSS. Owned by [Design] tickets.
docs/             Architecture, data model, roadmap, epic planning
scripts/          Repo gates and utilities (frontend check, skill mirroring)
tests/            Pytest suite — consolidated, few and meaningful (§4.7 of CHANGE_MANAGEMENT.md)
.claude/skills/   Agent skills that encode this project's conventions
```

---

## Getting started

Prerequisites: **Python 3.12+** and **Node 20+**. Docker is optional for local dev and
required only for the container path.

The container runs **Python 3.14**, matching development — the interpreter that serves
users is the one the tests ran on. `requires-python` stays at 3.12 so the package still
installs on older interpreters.

```bash
git clone https://github.com/HarkiratSingh029/Time-Sheet.git
cd Time-Sheet
cp .env.example .env       # then set TS_SECRET_KEY
./scripts/dev.sh           # creates .venv, installs, builds tokens, serves
```

The app is then on <http://127.0.0.1:8000>, with `/healthz` as the liveness check and the
API docs at `/api/docs` (development only).

`./scripts/dev.sh --fresh` wipes `data/` first — a half-migrated local database is a worse
debugging experience than losing a throwaway one.

Doing it by hand instead:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn backend.app.main:build --factory --reload
```

The `--factory` flag matters: the app is built by `build()` rather than created at import
time, so a configuration error exits cleanly instead of raising during import.

### Docker

```bash
docker compose up --build                             # app on 127.0.0.1:8000
docker compose --profile tunnel up --build            # app plus the Cloudflare Tunnel
docker compose down -v && docker compose up --build   # fresh, clean launch
```

The tunnel sits behind a compose profile, so a plain `docker compose up` never starts
`cloudflared`. Enable it only with `TS_TUNNEL_TOKEN` set in `.env` — the token reaches
cloudflared as `TUNNEL_TOKEN`. Without one the container refuses to run and restarts in a
loop logging `"cloudflared tunnel run" requires the ID or name of the tunnel`; that is a
missing token, and the app itself is unaffected.

The published port binds to loopback — in production the tunnel is the only route in.
`down -v` removes the `ts-data` volume, which is what makes it a genuinely fresh launch;
a plain `down` keeps your data.

---

## The gates

Every change must be green on these before a PR is opened:

```bash
pytest                              # backend behaviour, including migration drift
ruff check .                        # lint
node scripts/check-frontend.mjs     # any frontend change
python3 brands/scripts/build_all.py # any [Design] / brands change
```

Schema changes are Alembic revisions in `migrations/`. The app runs `upgrade head` at
startup, so there is no separate deploy step:

```bash
alembic revision --autogenerate -m "what changed"
alembic upgrade head
```

---

## How we work

Read **[CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md)** before your first change. In short:

- Branch from `develop`, never from `main`.
- One issue → one branch → one PR. Branch and issue numbers match.
- `[Feature]` / `[Fix]` / `[Design]` prefixes are shared by the issue, the branch and the PR.
- Gates green and scope complete → push and open the PR to `develop` without being asked.

Design work follows **[BRAND_GUIDELINES.md](BRAND_GUIDELINES.md)** — the Clean Navy &
Mint palette in light mode, Ocean Depth in dark mode.

---

## Roadmap

Planning lives in **[docs/ROADMAP.md](docs/ROADMAP.md)**:

- **EPIC 0 — Foundation. Complete.** Auth, users, projects, time notes on a calendar, a single approval, and a deployable container.
- **EPIC 1 — Workflow & approvals. Complete.** User-defined roles, invitations, 1–5 sequenced approvers, the audit trail, notifications, and the week view.
- **EPIC 2 — Insight. Complete.** Metrics engine, project and global dashboards, files, CSV and PDF exports, search, and the consultant's own view.
- **EPIC 3 — Operations. In progress.** Hardening, deployment, Cloudflare Tunnel, backups, CI and admin tooling.

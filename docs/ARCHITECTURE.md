# Architecture

> Simplicity is the ultimate sophistication. Small, quick and powerful like a honey badger.

TS Timesheets serves **at most ~30 concurrent users**. Every decision below is made
against that number. Architecture that would be correct at 30,000 users is wrong here: it
costs money, operational attention and build time we would never get back.

---

## 1. SAAS in a box

The whole product is one `docker compose up`.

```
                    ┌─────────────────────────────────────────┐
   internet ───►    │  cloudflared  (optional, on by default) │
                    └───────────────────┬─────────────────────┘
                                        │ tunnel, no inbound ports
                    ┌───────────────────▼─────────────────────┐
                    │  ts-timesheets  (FastAPI + Uvicorn)     │
                    │  ├─ API + server-rendered pages          │
                    │  ├─ session auth                         │
                    │  └─ approval workflow engine             │
                    └───────┬─────────────────────┬───────────┘
                            │                     │
                    ┌───────▼──────┐      ┌───────▼──────────┐
                    │  data/app.db │      │  data/uploads/   │
                    │  SQLite (WAL)│      │  project files   │
                    └──────────────┘      └──────────────────┘
                       one docker volume, one backup target
```

- **Dev** runs on a laptop: `uvicorn --reload`, no containers required.
- **Production** is a single low-spec VPC host running the same compose file.
- **Cloudflare Tunnel is enabled by default.** The host publishes no inbound ports; the
  tunnel is the only door. It is convenient, it is free, and it keeps the firewall story
  to one sentence.

---

## 2. Stack

| Layer | Choice | Why |
| --- | --- | --- |
| API + pages | **FastAPI** on Uvicorn | One process serves the JSON API and the HTML. No second service to deploy or keep in sync. |
| Templates | **Jinja2** | Server-rendered. No bundler, no node in the runtime image, no hydration bugs. |
| Client JS | **Vanilla ES modules** | The calendar and dashboard need interactivity, not a framework. Served straight from `frontend/static/`. |
| Data | **SQLite (WAL)** via SQLAlchemy | 30 users do not need a database server. WAL handles our concurrency; a single file is a single backup. |
| Validation | **Pydantic v2** | Already in FastAPI's path; one schema layer, not two. |
| Files | **Local volume** | No S3. Project attachments live in `data/uploads/`, backed up with the database. |
| Auth | **Signed, timestamped session cookies** | Simpler and safer than JWT for a first-party web app with no third-party clients, and with no session table to store or expire. The trade-off is that a valid cookie stays valid until it ages out — server-side revocation is an EPIC 3 hardening story. |

**On SQLite.** SQLAlchemy keeps Postgres a configuration change rather than a rewrite. We
move only if a real limit shows up — concurrent write contention or a need for multiple
app instances — not preemptively.

**On the frontend.** The gate `node scripts/check-frontend.mjs` exists so "no build step"
never means "no standards". It checks token usage, template hygiene and JS syntax.

---

## 3. Runtime layout

```
data/                 the only stateful directory — one volume, one backup
├── app.db            SQLite database
├── app.db-wal
└── uploads/<project_id>/<file>
```

Nothing else on the host is stateful. Restoring a backup is: stop, replace `data/`, start.

---

## 4. Request path

1. Cloudflare Tunnel terminates TLS and forwards to the app container.
2. Session middleware resolves the cookie to a user, or redirects to login.
3. A dependency resolves the user's role and the permissions it grants.
4. Route handlers validate input with Pydantic and read/write through the repository layer.
5. Pages render server-side; interactive pieces (calendar, dashboard) call the JSON API.

Authorization is enforced **per object**, not per page: a consultant may see only the
projects they are assigned to, an approver only what they approve, the administrator
everything.

---

## 5. Configuration

All configuration is environment variables, documented in `.env.example`. No secret is
committed, and the app fails loudly at startup on a missing required variable rather than
running with a silent default.

| Variable | Purpose |
| --- | --- |
| `TS_SECRET_KEY` | Session signing key. Required. |
| `TS_DATABASE_URL` | Defaults to `sqlite:///data/app.db`. |
| `TS_DATA_DIR` | Root for the database and uploads. |
| `TS_ADMIN_EMAIL` / `TS_ADMIN_PASSWORD` | Bootstrap administrator, used once on an empty database. |
| `TS_TUNNEL_TOKEN` | Cloudflare Tunnel token; the tunnel container starts only when set. |

---

## 6. Build and launch

Launchers carry a flag for a fresh, clean start — a half-migrated dev database is a
worse debugging experience than losing a throwaway one.

```bash
./scripts/dev.sh                 # local dev, reload on
./scripts/dev.sh --fresh         # wipe data/, reseed, then run
docker compose up --build        # container launch
docker compose down -v && docker compose up --build   # fresh container launch
```

---

## 7. What we are deliberately not building

- No S3, object store or CDN.
- No Redis, Celery or message broker. Scheduled work is a background task in-process.
- No Kubernetes. One host, one compose file.
- No microservices. One deployable.
- No SPA framework or bundler in the runtime image.

Each of these is a cost — in money, in build time, in the number of things that can be
broken at 2am — that 30 users cannot justify. Revisit only against a measured limit.

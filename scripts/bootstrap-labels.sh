#!/usr/bin/env bash
# Provision the label taxonomy from CHANGE_MANAGEMENT.md §5 on the GitHub repo.
# Idempotent: re-running updates colours and descriptions instead of failing.
#
#   ./scripts/bootstrap-labels.sh

set -euo pipefail

command -v gh >/dev/null || { echo "FAIL  gh is not installed" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "FAIL  gh is not authenticated — run: gh auth login" >&2; exit 1; }

create() {
  local name="$1" color="$2" description="$3"
  gh label create "$name" --color "$color" --description "$description" --force >/dev/null
  echo "  ✓ $name"
}

echo "==> type:"
create "type:feature"   "0E8A16" "New capability"
create "type:fix"       "D73A4A" "Bug fix"
create "type:design"    "98DDCA" "Brand toolkit change — never application source"
create "type:epic"      "000055" "Epic tracking issue"
create "type:chore"     "BFD4F2" "Maintenance with no user-visible change"
create "type:docs"      "0075CA" "Documentation"
create "type:refactor"  "C5DEF5" "Behaviour-preserving restructure"
create "type:perf"      "00BCD4" "Performance"
create "type:test"      "556B7D" "Tests and gates"
create "type:security"  "B60205" "Security"

echo "==> priority:"
create "priority:p0-blocker" "B60205" "Drop everything"
create "priority:p1-high"    "D93F0B" "High priority"
create "priority:p2-medium"  "FBCA04" "Normal priority"
create "priority:p3-low"     "C2E0C6" "Low priority"

echo "==> status:"
create "status:triage"       "EDEDED" "Not yet scoped"
create "status:in-progress"  "1D76DB" "Being worked on"
create "status:blocked"      "D73A4A" "Blocked on something external"
create "status:needs-review" "FBCA04" "Awaiting review"
create "status:needs-qa"     "98DDCA" "Awaiting verification"

echo "==> release:"
create "release:next"    "0E8A16" "Ships in the next promotion"
create "release:backlog" "EDEDED" "Not scheduled"
create "release:hotfix"  "B60205" "Out-of-band fix"

echo "==> platform:"
create "platform:server" "556B7D" "Backend service"
create "platform:web"    "1D76DB" "Browser-facing"
create "platform:cli"    "333333" "Command line and scripts"

echo "==> tech:"
create "tech:python"     "3572A5" "Python"
create "tech:fastapi"    "009688" "FastAPI"
create "tech:pydantic"   "E92063" "Pydantic"
create "tech:sqlalchemy" "A1341A" "SQLAlchemy"
create "tech:jinja"      "B41717" "Jinja templates"
create "tech:javascript" "F1E05A" "Browser JavaScript"
create "tech:css"        "563D7C" "CSS"

echo "==> area:"
create "area:api"       "0075CA" "HTTP API"
create "area:auth"      "5319E7" "Authentication and sessions"
create "area:db"        "A1341A" "Persistence and schema"
create "area:ui"        "563D7C" "User interface"
create "area:timesheet" "00BCD4" "Time notes and the calendar"
create "area:projects"  "0E8A16" "Projects, tasks and members"
create "area:approvals" "FBCA04" "Approval workflow"
create "area:dashboard" "98DDCA" "Metrics and dashboards"
create "area:storage"   "BFD4F2" "Files on the local volume"
create "area:scripts"   "C5DEF5" "Repo scripts and gates"
create "area:deploy"    "333333" "Docker, tunnel, hosting"

echo "==> build:"
create "build:pip"        "3572A5" "Python dependencies"
create "build:docker"     "2496ED" "Container build"
create "build:ci-actions" "2088FF" "GitHub Actions"
create "build:ruff"       "D7FF64" "Lint"
create "build:pytest"     "0A9EDC" "Test suite"

echo "OK    label taxonomy provisioned"

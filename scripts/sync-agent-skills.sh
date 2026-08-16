#!/usr/bin/env bash
# Mirror .claude/skills into .agents/skills so non-Claude agents read the same rules.
#
# .claude/skills is the source of truth and the only place a skill is edited.
# .agents/skills is generated and git-ignored.
#
#   ./scripts/sync-agent-skills.sh          # mirror
#   ./scripts/sync-agent-skills.sh --check  # fail if the mirror is stale

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/.claude/skills"
MIRROR_DIR="$REPO_ROOT/.agents/skills"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "FAIL  $SOURCE_DIR does not exist" >&2
  exit 1
fi

if [[ "${1:-}" == "--check" ]]; then
  if ! diff -rq "$SOURCE_DIR" "$MIRROR_DIR" >/dev/null 2>&1; then
    echo "FAIL  .agents/skills is stale — run: ./scripts/sync-agent-skills.sh" >&2
    exit 1
  fi
  echo "OK    agent skill mirror current"
  exit 0
fi

mkdir -p "$MIRROR_DIR"
rm -rf "${MIRROR_DIR:?}/"*
cp -R "$SOURCE_DIR/." "$MIRROR_DIR/"

skill_count=$(find "$MIRROR_DIR" -name "SKILL.md" | wc -l | tr -d ' ')
echo "OK    mirrored $skill_count skill(s) to .agents/skills"
